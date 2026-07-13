from typing import Callable, Any, List, Optional
from time import sleep
from subprocess import Popen
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from device_manager import device_manager
from config import config
from model import collapseTranslationEngineSelection, model, normalizeTranslationEngineSelection
from utils import removeLog, printLog, errorLogging, isConnectedNetwork, isValidIpAddress, isAvailableWebSocketServer
from errors import ErrorCode, VRCTError
from models.transcription.transcription_languages import transcription_lang
from models.transcription.transcription_whisper import DEFAULT_WHISPER_WEIGHT_TYPE
from models.transcription.transcription_vosk import getVoskModelMeta
from models.transcription.transcription_parakeet import getParakeetModelMeta
from models.transcription.transcription_sensevoice import getSenseVoiceModelMeta
from resource_usage import collect_resource_usage

class Controller:
    def __init__(self) -> None:
        # typed attributes to satisfy static type checkers
        self.init_mapping: dict = {}
        self.run_mapping: dict = {}
        # initialize with a no-op callable so callers can safely call self.run
        def _noop_run(status: int, endpoint: str, payload: Any = None) -> None:
            return None
        self.run: Callable[[int, str, Any], None] = _noop_run
        self.device_access_status: bool = True

    def _startupWhisperWeightType(self) -> str:
        selectable_weights = config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT
        if config.WHISPER_WEIGHT_TYPE in selectable_weights:
            return config.WHISPER_WEIGHT_TYPE
        if DEFAULT_WHISPER_WEIGHT_TYPE in selectable_weights:
            return DEFAULT_WHISPER_WEIGHT_TYPE
        return next(iter(selectable_weights), config.WHISPER_WEIGHT_TYPE)

    def _fallbackSelectedWhisperWeight(self, fallback_weight_type: str, fallback_available: bool) -> None:
        if fallback_available is False or not fallback_weight_type:
            return
        selected_weight_type = config.WHISPER_WEIGHT_TYPE
        if selected_weight_type == fallback_weight_type:
            return
        if model.checkTranscriptionWhisperModelWeight(selected_weight_type) is False:
            config.WHISPER_WEIGHT_TYPE = fallback_weight_type

    def _is_overlay_available(self) -> bool:
        """Safe check whether overlay is present and should receive updates.

        If OpenVR drops the overlay, the next update should be allowed to
        restart it instead of silently skipping all future overlay messages.
        """
        try:
            overlay = getattr(model, "overlay", None)
            if overlay is None:
                return False
            if getattr(overlay, "initialized", False) is False:
                model.startOverlay()
            return True
        except Exception:
            errorLogging()
            return False

    def _transcriptionLanguageCode(self, engine: str, language_data: dict) -> str:
        try:
            language = language_data.get("language")
            country = language_data.get("country")
            return transcription_lang[language][country].get(engine, "")
        except Exception:
            return ""

    def _transcriptionSupportedLanguageCodes(self, engine: str) -> Optional[set]:
        if engine == "Vosk":
            meta = getVoskModelMeta(config.VOSK_WEIGHT_TYPE)
        elif engine == "Parakeet":
            meta = getParakeetModelMeta(config.PARAKEET_WEIGHT_TYPE)
        elif engine == "SenseVoice":
            meta = getSenseVoiceModelMeta(config.SENSEVOICE_WEIGHT_TYPE)
        else:
            return None

        languages = meta.get("languages")
        if not languages and meta.get("language"):
            languages = [meta["language"]]
        return set(languages or [])

    def _isTranscriptionLanguageSupported(self, language_data: dict, engine: Optional[str] = None) -> bool:
        engine = engine or config.SELECTED_TRANSCRIPTION_ENGINE
        if engine not in {"Vosk", "Parakeet", "SenseVoice"}:
            return True

        language_code = self._transcriptionLanguageCode(engine, language_data)
        supported_codes = self._transcriptionSupportedLanguageCodes(engine)
        return bool(language_code and supported_codes and language_code in supported_codes)

    def _selectedTabLanguagesSupported(self, selected_languages: dict, only_enabled: bool = True) -> bool:
        tab_languages = selected_languages.get(config.SELECTED_TAB_NO, {})
        for language_data in tab_languages.values():
            if only_enabled and language_data.get("enable") is not True:
                continue
            if self._isTranscriptionLanguageSupported(language_data) is False:
                return False
        return True

    def _findFirstSupportedTranscriptionLanguage(self) -> Optional[dict]:
        preferred = [
            ("English", "United States"),
            ("Japanese", "Japan"),
            ("Korean", "South Korea"),
            ("Chinese Simplified", "China"),
            ("French", "France"),
            ("Spanish", "Spain"),
            ("German", "Germany"),
        ]

        for language, country in preferred:
            language_data = {"language": language, "country": country, "enable": True}
            if self._isTranscriptionLanguageSupported(language_data):
                return language_data

        for language, countries in transcription_lang.items():
            for country in countries.keys():
                language_data = {"language": language, "country": country, "enable": True}
                if self._isTranscriptionLanguageSupported(language_data):
                    return language_data
        return None

    def _findSelectableComputeDevice(self, device_kind: Optional[str] = None) -> Optional[dict]:
        try:
            selectable_devices = list(config.SELECTABLE_COMPUTE_DEVICE_LIST)
        except Exception:
            selectable_devices = []

        for device in selectable_devices:
            if device_kind is None or device.get("device") == device_kind:
                return device
        return selectable_devices[0] if selectable_devices else None

    def _getTranscriptionRuntimeRule(self, engine: Optional[str] = None) -> dict:
        engine = engine or config.SELECTED_TRANSCRIPTION_ENGINE
        if engine == "Parakeet":
            return {"device_kind": "cuda", "compute_types": ["auto"]}
        if engine in {"Google", "Vosk", "SenseVoice"}:
            return {"device_kind": "cpu", "compute_types": ["auto"]}
        return {"device_kind": None, "compute_types": None}

    def _normalizeTranscriptionRuntimeSelection(self, notify: bool = False) -> bool:
        changed = False
        rule = self._getTranscriptionRuntimeRule()
        selected_device = config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE
        target_device_kind = rule.get("device_kind")

        if target_device_kind is not None and selected_device.get("device") != target_device_kind:
            replacement = self._findSelectableComputeDevice(target_device_kind)
            if replacement is not None:
                config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE = replacement
                selected_device = replacement
                changed = True
                if notify is True:
                    self.run(
                        200,
                        self.run_mapping["selected_transcription_compute_device"],
                        config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE,
                    )

        allowed_compute_types = rule.get("compute_types")
        if allowed_compute_types is None:
            allowed_compute_types = selected_device.get("compute_types", []) or ["auto"]

        selected_compute_type = config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE
        fallback_compute_type = "auto" if "auto" in allowed_compute_types else allowed_compute_types[0]
        if selected_compute_type not in allowed_compute_types:
            config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE = fallback_compute_type
            changed = True
            if notify is True:
                self.run(
                    200,
                    self.run_mapping["selected_transcription_compute_type"],
                    config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE,
                )

        return changed

    def _normalizeSelectedYourLanguageForTranscription(self) -> bool:
        try:
            selected = config.SELECTED_YOUR_LANGUAGES
            tab_languages = selected[config.SELECTED_TAB_NO]
        except Exception:
            return False

        changed = False
        engine = config.SELECTED_TRANSCRIPTION_ENGINE

        if engine not in {"Whisper", "SenseVoice"}:
            for key, language_data in tab_languages.items():
                if key != "1" and language_data.get("enable") is True:
                    language_data["enable"] = False
                    changed = True

        if engine in {"Vosk", "Parakeet", "SenseVoice"}:
            for key, language_data in tab_languages.items():
                if language_data.get("enable") is not True:
                    continue
                if self._isTranscriptionLanguageSupported(language_data):
                    continue

                if key == "1":
                    replacement = self._findFirstSupportedTranscriptionLanguage()
                    if replacement is not None:
                        tab_languages[key] = replacement
                        changed = True
                else:
                    language_data["enable"] = False
                    changed = True

        if changed is False:
            return False

        config.SELECTED_YOUR_LANGUAGES = selected
        self.updateTranslationEngineAndEngineList()
        self.run(200, "/set/data/selected_your_languages", config.SELECTED_YOUR_LANGUAGES)
        return True

    def setInitMapping(self, init_mapping:dict) -> None:
        self.init_mapping = init_mapping

    def setRunMapping(self, run_mapping:dict) -> None:
        self.run_mapping = run_mapping

    def setRun(self, run:Callable[[int, str, Any], None]) -> None:
        self.run = run
    
    def shutdown(self, *args, **kwargs) -> dict:
        """Shutdown controller and model (including telemetry).
        
        Returns:
            dict with status 200 and result True on success.
        """
        try:
            model.telemetryShutdown()
            return {"status": 200, "result": True}
        except Exception:
            errorLogging()
            return {"status": 500, "result": False}

    # response functions
    def connectedNetwork(self) -> None:
        self.run(
            200,
            self.run_mapping["connected_network"],
            True,
        )

    def disconnectedNetwork(self) -> None:
        self.run(
            200,
            self.run_mapping["connected_network"],
            False,
        )

    def enableAiModels(self) -> None:
        self.run(
            200,
            self.run_mapping["enable_ai_models"],
            True,
        )

    def disableAiModels(self) -> None:
        self.run(
            200,
            self.run_mapping["enable_ai_models"],
            False,
        )

    def updateMicHostList(self) -> None:
        self.run(
            200,
            self.run_mapping["selectable_mic_host_list"],
            model.getListMicHost(),
        )

    def updateMicDeviceList(self) -> None:
        self.run(
            200,
            self.run_mapping["selectable_mic_device_list"],
            model.getListMicDevice(),
        )

    def updateSpeakerDeviceList(self) -> None:
        self.run(
            200,
            self.run_mapping["selectable_speaker_device_list"],
            model.getListSpeakerDevice(),
        )

    def updateConfigSettings(self) -> None:
        settings = {}
        deferred_endpoints = {
            "/get/data/selectable_mic_host_list",
            "/get/data/selectable_mic_device_list",
            "/get/data/selectable_speaker_device_list",
            "/get/data/connected_lmstudio",
            "/get/data/connected_ollama",
            "/get/data/selectable_lmstudio_model_list",
            "/get/data/selectable_ollama_model_list",
        }
        for endpoint, dict_data in self.init_mapping.items():
            if endpoint in deferred_endpoints:
                continue
            response = dict_data["variable"](None)
            result = response.get("result", None)
            settings[endpoint] = result
        self.run(
            200,
            self.run_mapping["initialization_complete"],
            settings,
        )

    def sendDeferredConfigSettings(self) -> None:
        deferred_endpoints = (
            "/get/data/selectable_mic_host_list",
            "/get/data/selectable_mic_device_list",
            "/get/data/selectable_speaker_device_list",
            "/get/data/connected_lmstudio",
            "/get/data/connected_ollama",
            "/get/data/selectable_lmstudio_model_list",
            "/get/data/selectable_ollama_model_list",
        )
        for endpoint in deferred_endpoints:
            dict_data = self.init_mapping.get(endpoint)
            if dict_data is None:
                continue
            try:
                response = dict_data["variable"](None)
                self.run(200, endpoint, response.get("result", None))
            except Exception:
                errorLogging()

    def restartAccessMicDevices(self) -> None:
        if config.ENABLE_TRANSCRIPTION_SEND is True:
            self.startThreadingTranscriptionSendMessage()
        if config.ENABLE_CHECK_ENERGY_SEND is True:
            model.startCheckMicEnergy(
                self.progressBarMicEnergy,
            )

    def restartAccessSpeakerDevices(self) -> None:
        if config.ENABLE_TRANSCRIPTION_RECEIVE is True:
            self.startThreadingTranscriptionReceiveMessage()
        if config.ENABLE_CHECK_ENERGY_RECEIVE is True:
            model.startCheckSpeakerEnergy(
                self.progressBarSpeakerEnergy,
            )

    def stopAccessMicDevices(self) -> None:
        if config.ENABLE_TRANSCRIPTION_SEND is True:
            self.stopThreadingTranscriptionSendMessage()
        if config.ENABLE_CHECK_ENERGY_SEND is True:
            model.stopCheckMicEnergy()

    def stopAccessSpeakerDevices(self) -> None:
        if config.ENABLE_TRANSCRIPTION_RECEIVE is True:
            self.stopThreadingTranscriptionReceiveMessage()
        if config.ENABLE_CHECK_ENERGY_RECEIVE is True:
            model.stopCheckSpeakerEnergy()

    def updateSelectedMicDevice(self, host, device) -> None:
        config.SELECTED_MIC_HOST = host
        config.SELECTED_MIC_DEVICE = device
        self.run(200, self.run_mapping["selected_mic_host"], config.SELECTED_MIC_HOST)
        self.run(200, self.run_mapping["selected_mic_device"], config.SELECTED_MIC_DEVICE)

    def updateSelectedSpeakerDevice(self, device) -> None:
        config.SELECTED_SPEAKER_DEVICE = device
        self.run(
            200,
            self.run_mapping["selected_speaker_device"],
            device,
        )

    def progressBarMicEnergy(self, energy) -> None:
        if energy is False:
            error_response = VRCTError.create_error_response(
                ErrorCode.DEVICE_NO_MIC,
                data=None
            )
            self.run(
                error_response["status"],
                self.run_mapping["error_device"],
                error_response["result"],
            )
        else:
            self.run(
                200,
                self.run_mapping["check_mic_volume"],
                energy,
            )

    def progressBarSpeakerEnergy(self, energy) -> None:
        if energy is False:
            error_response = VRCTError.create_error_response(
                ErrorCode.DEVICE_NO_SPEAKER,
                data=None
            )
            self.run(
                error_response["status"],
                self.run_mapping["error_device"],
                error_response["result"],
            )
        else:
            self.run(
                200,
                self.run_mapping["check_speaker_volume"],
                energy,
            )

    class DownloadCTranslate2:
        def __init__(self, run_mapping:dict,  weight_type:str, run:Callable[[int, str, Any], None]) -> None:
            self.run_mapping = run_mapping
            self.weight_type = weight_type
            self.run = run

        def progressBar(self, progress) -> None:
            printLog("CTranslate2 Weight Download Progress", progress)
            self.run(
                200,
                self.run_mapping["download_progress_ctranslate2_weight"],
                {"weight_type": self.weight_type, "progress": progress},
            )

        def downloaded(self) -> None:
            if (
                model.checkTranslatorCTranslate2ModelWeight(self.weight_type) is True
                and model.checkTranslatorCTranslate2ModelTokenizer(self.weight_type) is True
            ):
                config.SELECTABLE_CTRANSLATE2_WEIGHT_TYPE_DICT[self.weight_type] = True

                self.run(
                    200,
                    self.run_mapping["downloaded_ctranslate2_weight"],
                    self.weight_type,
                )
            else:
                error_response = VRCTError.create_error_response(
                    ErrorCode.WEIGHT_CTRANSLATE2_DOWNLOAD,
                    data=None
                )
                self.run(
                    error_response["status"],
                    self.run_mapping["error_ctranslate2_weight"],
                    error_response["result"],
                )

    class DownloadWhisper:
        def __init__(self, run_mapping:dict, weight_type:str, run:Callable[[int, str, Any], None]) -> None:
            self.run_mapping = run_mapping
            self.weight_type = weight_type
            self.run = run

        def progressBar(self, progress) -> None:
            printLog("Whisper Weight Download Progress", progress)
            self.run(
                200,
                self.run_mapping["download_progress_whisper_weight"],
                {"weight_type": self.weight_type, "progress": progress},
            )

        def downloaded(self) -> None:
            if model.checkTranscriptionWhisperModelWeight(self.weight_type) is True:
                config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT[self.weight_type] = True

                self.run(
                    200,
                    self.run_mapping["downloaded_whisper_weight"],
                    self.weight_type,
                )
            else:
                error_response = VRCTError.create_error_response(
                    ErrorCode.WEIGHT_WHISPER_DOWNLOAD,
                    data=None
                )
                self.run(
                    error_response["status"],
                    self.run_mapping["error_whisper_weight"],
                    error_response["result"],
                )

    class DownloadVosk:
        def __init__(self, run_mapping:dict, weight_type:str, run:Callable[[int, str, Any], None]) -> None:
            self.run_mapping = run_mapping
            self.weight_type = weight_type
            self.run = run

        def progressBar(self, progress) -> None:
            self.run(
                200,
                self.run_mapping.get("download_progress_vosk_weight", "download_progress_vosk_weight"),
                {"weight_type": self.weight_type, "progress": progress},
            )

        def downloaded(self) -> None:
            if model.checkTranscriptionVoskModelWeight(self.weight_type) is True:
                config.SELECTABLE_VOSK_WEIGHT_TYPE_DICT[self.weight_type] = True
                self.run(
                    200,
                    self.run_mapping.get("downloaded_vosk_weight", "downloaded_vosk_weight"),
                    self.weight_type,
                )

    class DownloadParakeet:
        def __init__(self, run_mapping:dict, weight_type:str, run:Callable[[int, str, Any], None]) -> None:
            self.run_mapping = run_mapping
            self.weight_type = weight_type
            self.run = run

        def progressBar(self, progress) -> None:
            self.run(
                200,
                self.run_mapping.get("download_progress_parakeet_weight", "download_progress_parakeet_weight"),
                {"weight_type": self.weight_type, "progress": progress},
            )

        def downloaded(self) -> None:
            if model.checkTranscriptionParakeetModelWeight(self.weight_type) is True:
                config.SELECTABLE_PARAKEET_WEIGHT_TYPE_DICT[self.weight_type] = True
                self.run(
                    200,
                    self.run_mapping.get("downloaded_parakeet_weight", "downloaded_parakeet_weight"),
                    self.weight_type,
                )

    class DownloadSenseVoice:
        def __init__(self, run_mapping:dict, weight_type:str, run:Callable[[int, str, Any], None]) -> None:
            self.run_mapping = run_mapping
            self.weight_type = weight_type
            self.run = run

        def progressBar(self, progress) -> None:
            self.run(
                200,
                self.run_mapping.get("download_progress_sensevoice_weight", "download_progress_sensevoice_weight"),
                {"weight_type": self.weight_type, "progress": progress},
            )

        def downloaded(self) -> None:
            if model.checkTranscriptionSenseVoiceModelWeight(self.weight_type) is True:
                config.SELECTABLE_SENSEVOICE_WEIGHT_TYPE_DICT[self.weight_type] = True
                self.run(
                    200,
                    self.run_mapping.get("downloaded_sensevoice_weight", "downloaded_sensevoice_weight"),
                    self.weight_type,
                )
            else:
                error_response = VRCTError.create_error_response(
                    ErrorCode.WEIGHT_SENSEVOICE_DOWNLOAD,
                    data=None
                )
                self.run(
                    error_response["status"],
                    self.run_mapping.get("error_sensevoice_weight", "error_sensevoice_weight"),
                    error_response["result"],
                )

    def micMessage(self, result: dict) -> None:
        message = result["text"]
        language = result["language"]
        if isinstance(message, bool) and message is False:
            self.run(
                400,
                self.run_mapping["error_device"],
                {
                    "message":"No mic device detected",
                    "data": None
                },
            )

        elif isinstance(message, str) and len(message) > 0:
            model.telemetryTrackCoreFeature("mic_speech_to_text")
            translation = []
            transliteration_message = []
            transliteration_translation = []
            if model.checkKeywords(message):
                self.run(
                    200,
                    self.run_mapping["word_filter"],
                    {"message":f"Detected by word filter: {message}"},
                )
                return
            elif model.detectRepeatSendMessage(message):
                return
            elif config.ENABLE_TRANSLATION is False:
                pass
            else:
                try:
                    model.telemetryTrackCoreFeature("translation")
                    translation, success = model.getInputTranslate(message, source_language=language)
                    if all(success) is not True:
                        self.changeToCTranslate2Process()
                        self.run(
                            400,
                            self.run_mapping["error_translation_engine"],
                            {
                                "message":"Translation engine limit error",
                                "data": None
                            },
                        )
                    else:
                        pass
                except Exception as e:
                    # VRAM不足エラーの検出
                    is_vram_error, error_message = model.detectVRAMError(e)
                    if is_vram_error:
                        error_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_VRAM_MIC,
                            data=error_message
                        )
                        self.run(
                            error_response["status"],
                            self.run_mapping["error_translation_mic_vram_overflow"],
                            error_response["result"],
                        )
                        # 翻訳機能をOFFにする
                        self.setDisableTranslation()
                        disable_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_DISABLED_VRAM,
                            data=False
                        )
                        self.run(
                            disable_response["status"],
                            self.run_mapping["enable_translation"],
                            disable_response["result"],
                        )
                        return
                    else:
                        # その他のエラーは通常通り処理
                        raise

            if config.CONVERT_MESSAGE_TO_HIRAGANA is True or config.CONVERT_MESSAGE_TO_ROMAJI is True:
                if config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"] == "Japanese":
                    transliteration_message = model.convertMessageToTransliteration(
                        message,
                        hiragana=config.CONVERT_MESSAGE_TO_HIRAGANA,
                        romaji=config.CONVERT_MESSAGE_TO_ROMAJI
                    )

                for i, no in enumerate(config.SELECTED_TAB_TARGET_LANGUAGES_NO_LIST):
                    if (config.ENABLE_TRANSLATION is True and
                        config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO][no]["language"] == "Japanese" and
                        config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO][no]["enable"] is True
                        ):
                        transliteration_translation.append(
                            model.convertMessageToTransliteration(
                                translation[i],
                                hiragana=config.CONVERT_MESSAGE_TO_HIRAGANA,
                                romaji=config.CONVERT_MESSAGE_TO_ROMAJI
                            )
                        )
                    else:
                        transliteration_translation.append([])
            else:
                transliteration_translation = [[] for _ in config.SELECTED_TAB_TARGET_LANGUAGES_NO_LIST]

            if config.ENABLE_TRANSCRIPTION_SEND is True:
                if config.SEND_MESSAGE_TO_VRC is True:
                    if config.SEND_ONLY_TRANSLATED_MESSAGES is True:
                        if config.ENABLE_TRANSLATION is False:
                            osc_message = self.messageFormatter("SEND", [], message)
                        else:
                            osc_message = self.messageFormatter("SEND", translation, "")
                    else:
                        osc_message = self.messageFormatter("SEND", translation, message)
                    model.oscSendMessage(osc_message)

                self.run(
                    200,
                    self.run_mapping["transcription_mic"],
                    {
                        "original": {
                            "message": message,
                            "transliteration": transliteration_message
                        },
                        "translations": [
                            {
                                "message": translation_message,
                                "transliteration": transliteration
                            } for translation_message, transliteration in zip(translation, transliteration_translation)
                        ]
                    })

                if config.OVERLAY_LARGE_LOG is True and self._is_overlay_available():
                    if config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES is True:
                        if len(translation) > 0:
                            overlay_image = model.createOverlayImageLargeLog(
                                "send",
                                None,
                                None,
                                translation,
                                config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                                transliteration_message,
                                transliteration_translation
                            )
                            model.updateOverlayLargeLog(overlay_image)
                    else:
                        overlay_image = model.createOverlayImageLargeLog(
                            "send",
                            message,
                            config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"],
                            translation,
                            config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                            transliteration_message,
                            transliteration_translation
                        )
                        model.updateOverlayLargeLog(overlay_image)

                if config.ENABLE_CLIPBOARD is True:
                    clipboard_message = self.messageFormatter("SEND", translation, message)
                    model.setCopyToClipboardAndPasteFromClipboard(clipboard_message)

                if model.checkWebSocketServerAlive() is True:
                    model.websocketSendMessage(
                        {
                            "type":"SENT",
                            "src_languages":config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO],
                            "dst_languages":config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                            "message":message,
                            "translation":translation,
                            "transliteration":transliteration_translation
                        }
                    )

                if config.LOGGER_FEATURE is True:
                    translation_text = f" ({'/'.join(translation)})" if translation else ""
                    model.logger.info(f"[SENT] {message}{translation_text}")

            model.addTranslationHistory("mic", message)

    def speakerMessage(self, result:dict) -> None:
        message = result["text"]
        language = result["language"]
        if isinstance(message, bool) and message is False:
            self.run(
                400,
                self.run_mapping["error_device"],
                {
                    "message":"No speaker device detected",
                    "data": None
                },
            )
        elif isinstance(message, str) and len(message) > 0:
            model.telemetryTrackCoreFeature("speaker_speech_to_text")
            translation = []
            transliteration_message = []
            transliteration_translation = []
            if model.checkKeywords(message):
                self.run(
                    200,
                    self.run_mapping["word_filter"],
                    {"message":f"Detected by word filter: {message}"},
                )
                return
            elif model.detectRepeatReceiveMessage(message):
                return
            elif config.ENABLE_TRANSLATION is False:
                pass
            else:
                try:
                    model.telemetryTrackCoreFeature("translation")
                    translation, success = model.getOutputTranslate(message, source_language=language)
                    if all(success) is not True:
                        self.changeToCTranslate2Process()
                        error_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_ENGINE_LIMIT,
                            data=None
                        )
                        self.run(
                            error_response["status"],
                            self.run_mapping["error_translation_engine"],
                            error_response["result"],
                        )
                    else:
                        pass
                except Exception as e:
                    # VRAM不足エラーの検出
                    is_vram_error, error_message = model.detectVRAMError(e)
                    if is_vram_error:
                        error_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_VRAM_SPEAKER,
                            data=error_message
                        )
                        self.run(
                            error_response["status"],
                            self.run_mapping["error_translation_speaker_vram_overflow"],
                            error_response["result"],
                        )
                        # 翻訳機能をOFFにする
                        self.setDisableTranslation()
                        disable_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_DISABLED_VRAM,
                            data=False
                        )
                        self.run(
                            disable_response["status"],
                            self.run_mapping["enable_translation"],
                            disable_response["result"],
                        )
                        return
                    else:
                        # その他のエラーは通常通り処理
                        raise

            if config.CONVERT_MESSAGE_TO_HIRAGANA is True or config.CONVERT_MESSAGE_TO_ROMAJI is True:
                if language == "Japanese":
                    transliteration_message = model.convertMessageToTransliteration(
                        message,
                        hiragana=config.CONVERT_MESSAGE_TO_HIRAGANA,
                        romaji=config.CONVERT_MESSAGE_TO_ROMAJI
                    )

                if (config.ENABLE_TRANSLATION is True and
                    config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"] == "Japanese"
                    ):
                    transliteration_translation.append(
                        model.convertMessageToTransliteration(
                            translation[0],
                            hiragana=config.CONVERT_MESSAGE_TO_HIRAGANA,
                            romaji=config.CONVERT_MESSAGE_TO_ROMAJI
                        )
                    )
                else:
                    transliteration_translation.append([])
            else:
                transliteration_translation = [[]]

            if config.ENABLE_TRANSCRIPTION_RECEIVE is True:
                if config.OVERLAY_SMALL_LOG is True and self._is_overlay_available():
                    if config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES is True:
                        if len(translation) > 0:
                            overlay_image = model.createOverlayImageSmallLog(
                                None,
                                None,
                                translation,
                                config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO],
                                transliteration_message,
                                transliteration_translation
                            )
                            model.updateOverlaySmallLog(overlay_image)
                    else:
                        overlay_image = model.createOverlayImageSmallLog(
                            message,
                            language,
                            translation,
                            config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO],
                            transliteration_message,
                            transliteration_translation
                        )
                        model.updateOverlaySmallLog(overlay_image)

                if config.OVERLAY_LARGE_LOG is True and self._is_overlay_available():
                    if config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES is True:
                        if len(translation) > 0:
                            overlay_image = model.createOverlayImageLargeLog(
                                "receive",
                                None,
                                None,
                                translation,
                                config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO],
                                transliteration_message,
                                transliteration_translation
                            )
                            model.updateOverlayLargeLog(overlay_image)
                    else:
                        overlay_image = model.createOverlayImageLargeLog(
                            "receive",
                            message,
                            language,
                            translation,
                            config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO],
                            transliteration_message,
                            transliteration_translation
                        )
                        model.updateOverlayLargeLog(overlay_image)

                if config.SEND_RECEIVED_MESSAGE_TO_VRC is True:
                    if config.SEND_ONLY_TRANSLATED_MESSAGES is True:
                        if config.ENABLE_TRANSLATION is False:
                            osc_message = self.messageFormatter("RECEIVED", [], message)
                        else:
                            osc_message = self.messageFormatter("RECEIVED", translation, "")
                    else:
                        osc_message = self.messageFormatter("RECEIVED", translation, message)
                    model.oscSendMessage(osc_message)

                # update textbox message log (Received)
                self.run(
                    200,
                    self.run_mapping["transcription_speaker"],
                    {
                        "original": {
                            "message": message,
                            "transliteration": transliteration_message
                        },
                        "translations": [
                            {
                                "message": translation_message,
                                "transliteration": transliteration
                            } for translation_message, transliteration in zip(translation, transliteration_translation)
                        ]
                    })

                if model.checkWebSocketServerAlive() is True:
                    model.websocketSendMessage(
                        {
                            "type":"RECEIVED",
                            "src_languages":config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                            "dst_languages":config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO],
                            "message":message,
                            "translation":translation,
                            "transliteration":transliteration_translation
                        }
                    )

                if config.LOGGER_FEATURE is True:
                    translation_text = f" ({'/'.join(translation)})" if translation else ""
                    model.logger.info(f"[RECEIVED] {message}{translation_text}")

            model.addTranslationHistory("speaker", message)

    def chatMessage(self, data) -> dict:
        id = data["id"]
        message = data["message"]
        if len(message) > 0:
            model.telemetryTrackCoreFeature("text_input")
            translation = []
            transliteration_message: List[Any] = []
            transliteration_translation = []
            if config.ENABLE_TRANSLATION is False:
                pass
            else:
                try:
                    model.telemetryTrackCoreFeature("translation")
                    if config.USE_EXCLUDE_WORDS is True:
                        replacement_message, replacement_dict = self.replaceExclamationsWithRandom(message)
                        translation, success = model.getInputTranslate(replacement_message)

                        message = self.removeExclamations(message)
                        for i in range(len(translation)):
                            translation[i] = self.restoreText(translation[i], replacement_dict)
                    else:
                        translation, success = model.getInputTranslate(message)

                    if all(success) is not True:
                        error_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_ENGINE_LIMIT,
                            data=None
                        )
                        self.run(
                            error_response["status"],
                            self.run_mapping["error_translation_engine"],
                            error_response["result"],
                        )
                    else:
                        pass
                except Exception as e:
                    # VRAM不足エラーの検出
                    is_vram_error, error_message = model.detectVRAMError(e)
                    if is_vram_error:
                        error_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_VRAM_CHAT,
                            data=error_message
                        )
                        self.run(
                            error_response["status"],
                            self.run_mapping["error_translation_chat_vram_overflow"],
                            error_response["result"],
                        )
                        # 翻訳機能をOFFにする
                        self.setDisableTranslation()
                        disable_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_DISABLED_VRAM,
                            data=False
                        )
                        self.run(
                            disable_response["status"],
                            self.run_mapping["enable_translation"],
                            disable_response["result"],
                        )
                        # エラー時は翻訳なしで返す
                        return {"status":200,
                                "result":
                                {
                                    "id":id,
                                    "original": {
                                        "message":message,
                                        "transliteration":[]
                                    },
                                    "translations": [
                                        {
                                            "message": "",
                                            "transliteration": []
                                        } for _ in config.SELECTED_TAB_TARGET_LANGUAGES_NO_LIST
                                    ]
                                },
                            }
                    else:
                        # その他のエラーは通常通り処理
                        raise

            if config.CONVERT_MESSAGE_TO_HIRAGANA is True or config.CONVERT_MESSAGE_TO_ROMAJI is True:
                if config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"] == "Japanese":
                    transliteration_message = model.convertMessageToTransliteration(
                        message,
                        hiragana=config.CONVERT_MESSAGE_TO_HIRAGANA,
                        romaji=config.CONVERT_MESSAGE_TO_ROMAJI
                    )
                for i, no in enumerate(config.SELECTED_TAB_TARGET_LANGUAGES_NO_LIST):
                    if (config.ENABLE_TRANSLATION is True and
                        config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO][no]["language"] == "Japanese" and
                        config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO][no]["enable"] is True
                        ):
                        transliteration_translation.append(
                            model.convertMessageToTransliteration(
                                translation[i],
                                hiragana=config.CONVERT_MESSAGE_TO_HIRAGANA,
                                romaji=config.CONVERT_MESSAGE_TO_ROMAJI
                            )
                        )
                    else:
                        transliteration_translation.append([])
            else:
                transliteration_translation = [[] for _ in config.SELECTED_TAB_TARGET_LANGUAGES_NO_LIST]

            # send OSC message
            if config.SEND_MESSAGE_TO_VRC is True:
                if config.SEND_ONLY_TRANSLATED_MESSAGES is True:
                    if config.ENABLE_TRANSLATION is False:
                        osc_message = self.messageFormatter("SEND", [], message)
                    else:
                        osc_message = self.messageFormatter("SEND", translation, "")
                else:
                    osc_message = self.messageFormatter("SEND", translation, message)
                model.oscSendMessage(osc_message)

            if config.OVERLAY_LARGE_LOG is True:
                if config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES is True:
                    if len(translation) > 0:
                        overlay_image = model.createOverlayImageLargeLog(
                            "send",
                            None,
                            None,
                            translation,
                            config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                            transliteration_message,
                            transliteration_translation
                        )
                        model.updateOverlayLargeLog(overlay_image)
                else:
                    overlay_image = model.createOverlayImageLargeLog(
                        "send",
                        message,
                        config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"],
                        translation,
                        config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                        transliteration_message,
                        transliteration_translation
                    )
                    model.updateOverlayLargeLog(overlay_image)

            if model.checkWebSocketServerAlive() is True:
                model.websocketSendMessage(
                    {
                        "type":"CHAT",
                        "src_languages":config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO],
                        "dst_languages":config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
                        "message":message,
                        "translation":translation,
                        "transliteration":transliteration_translation
                    }
                )

            if config.LOGGER_FEATURE is True:
                translation_text = f" ({'/'.join(translation)})" if translation else ""
                model.logger.info(f"[CHAT] {message}{translation_text}")

        model.addTranslationHistory("chat", message)

        return {
                "status":200,
                "result":{
                    "id":id,
                    "original": {
                        "message":message,
                        "transliteration":transliteration_message
                    },
                    "translations": [
                        {
                            "message": translation_message,
                            "transliteration": transliteration
                        } for translation_message, transliteration in zip(translation, transliteration_translation)
                    ]
                }}

    @staticmethod
    def getVersion(*args, **kwargs) -> dict:
        return {"status":200, "result":config.VERSION}

    def checkSoftwareUpdated(self) -> dict:
        software_update_info = model.checkSoftwareUpdated()
        self.run(
            200,
            self.run_mapping["software_update_info"],
            software_update_info,
        )
        return {"status":200, "result": software_update_info}

    @staticmethod
    def getComputeMode(*args, **kwargs) -> dict:
        return {"status":200, "result":config.COMPUTE_MODE}

    @staticmethod
    def _getSelectedResourceMonitorGpuIndex(data: dict | None = None) -> int | None:
        if isinstance(data, dict) and data.get("mode") == "manual":
            try:
                return int(data.get("device_index"))
            except (TypeError, ValueError):
                return None

        for selected_device in (
            config.SELECTED_TRANSLATION_COMPUTE_DEVICE,
            config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE,
        ):
            if selected_device.get("device") == "cuda":
                try:
                    return int(selected_device.get("device_index"))
                except (TypeError, ValueError):
                    return None

        return None

    def getResourceUsage(self, data=None, *args, **kwargs) -> dict:
        selected_gpu_index = self._getSelectedResourceMonitorGpuIndex(data)
        return {"status": 200, "result": collect_resource_usage(selected_gpu_index)}

    @staticmethod
    def getComputeDeviceList(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTABLE_COMPUTE_DEVICE_LIST}

    @staticmethod
    def getSelectedTranslationComputeDevice(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TRANSLATION_COMPUTE_DEVICE}

    def setSelectedTranslationComputeDevice(self, device:str, *args, **kwargs) -> dict:
        printLog("setSelectedTranslationComputeDevice", device)
        config.SELECTED_TRANSLATION_COMPUTE_DEVICE = device
        config.SELECTED_TRANSLATION_COMPUTE_TYPE = "auto"
        self.run(200, self.run_mapping["selected_translation_compute_type"], config.SELECTED_TRANSLATION_COMPUTE_TYPE)
        model.setChangedTranslatorParameters(True)
        return {"status":200,"result":config.SELECTED_TRANSLATION_COMPUTE_DEVICE}

    @staticmethod
    def getSelectableCtranslate2WeightTypeDict(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTABLE_CTRANSLATE2_WEIGHT_TYPE_DICT}

    @staticmethod
    def getSelectedTranscriptionComputeDevice(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE}

    def setSelectedTranscriptionComputeDevice(self, device:str, *args, **kwargs) -> dict:
        printLog("setSelectedTranscriptionComputeDevice", device)
        config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE = device
        config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE = "auto"
        self.run(200, self.run_mapping["selected_transcription_compute_type"], config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE)
        self._normalizeTranscriptionRuntimeSelection(notify=True)
        return {"status":200,"result":config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE}

    @staticmethod
    def getSelectableWhisperWeightTypeDict(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT}

    @staticmethod
    def getSelectableVoskWeightTypeDict(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTABLE_VOSK_WEIGHT_TYPE_DICT}

    @staticmethod
    def getSelectableParakeetWeightTypeDict(*args, **kwargs) -> dict:
        result = {}
        for weight_type, is_downloaded in config.SELECTABLE_PARAKEET_WEIGHT_TYPE_DICT.items():
            meta = getParakeetModelMeta(weight_type)
            result[weight_type] = {
                "is_downloaded": is_downloaded,
                "downloadable": meta.get("downloadable", True),
                "unavailable_reason": meta.get("unavailable_reason", ""),
            }
        return {"status":200, "result":result}

    @staticmethod
    def getSelectableSenseVoiceWeightTypeDict(*args, **kwargs) -> dict:
        result = {}
        for weight_type, is_downloaded in config.SELECTABLE_SENSEVOICE_WEIGHT_TYPE_DICT.items():
            meta = getSenseVoiceModelMeta(weight_type)
            result[weight_type] = {
                "is_downloaded": is_downloaded,
                "downloadable": meta.get("downloadable", True),
                "unavailable_reason": meta.get("unavailable_reason", ""),
            }
        return {"status":200, "result":result}

    # @staticmethod
    # def getMaxMicThreshold(*args, **kwargs) -> dict:
    #     return {"status":200, "result":config.MAX_MIC_THRESHOLD}

    # @staticmethod
    # def getMaxSpeakerThreshold(*args, **kwargs) -> dict:
    #     return {"status":200, "result":config.MAX_SPEAKER_THRESHOLD}

    def setEnableTranslation(self, *args, **kwargs) -> dict:
        if config.ENABLE_TRANSLATION is False:
            selected_engine = config.SELECTED_TRANSLATION_ENGINES.get(config.SELECTED_TAB_NO, "CTranslate2")
            selected_engines = normalizeTranslationEngineSelection(selected_engine)
            if "CTranslate2" not in selected_engines:
                config.ENABLE_TRANSLATION = True
            elif model.isLoadedCTranslate2Model() is False or model.isChangedTranslatorParameters() is True:
                try:
                    printLog("Loading CTranslate2 translation model")
                    model.changeTranslatorCTranslate2Model()
                    model.setChangedTranslatorParameters(False)
                    config.ENABLE_TRANSLATION = True
                except Exception as e:
                    # VRAM不足エラーの検出（デバイス切り替え時）
                    is_vram_error, error_message = model.detectVRAMError(e)
                    if is_vram_error:
                        # Defaultのデバイス設定に戻す
                        printLog("VRAM error detected, reverting device setting")
                        error_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_VRAM_ENABLE,
                            data=error_message
                        )
                        self.run(
                            error_response["status"],
                            self.run_mapping["error_translation_enable_vram_overflow"],
                            error_response["result"],
                        )
                        self.setDisableTranslation()
                        disable_response = VRCTError.create_error_response(
                            ErrorCode.TRANSLATION_DISABLED_VRAM,
                            data=False
                        )
                        self.run(
                            disable_response["status"],
                            self.run_mapping["enable_translation"],
                            disable_response["result"],
                        )
                        model.changeTranslatorCTranslate2Model()
                        model.setChangedTranslatorParameters(False)
                    else:
                        # その他のエラーは通常通り処理
                        errorLogging()
                        return {"status":500, "result":config.ENABLE_TRANSLATION}
            else:
                config.ENABLE_TRANSLATION = True
        return {"status":200, "result":config.ENABLE_TRANSLATION}

    @staticmethod
    def setDisableTranslation(*args, **kwargs) -> dict:
        if config.ENABLE_TRANSLATION is True:
            config.ENABLE_TRANSLATION = False
        return {"status":200, "result":config.ENABLE_TRANSLATION}

    @staticmethod
    def setEnableForeground(*args, **kwargs) -> dict:
        if config.ENABLE_FOREGROUND is False:
            config.ENABLE_FOREGROUND = True
        return {"status":200, "result":config.ENABLE_FOREGROUND}

    @staticmethod
    def setDisableForeground(*args, **kwargs) -> dict:
        if config.ENABLE_FOREGROUND is True:
            config.ENABLE_FOREGROUND = False
        return {"status":200, "result":config.ENABLE_FOREGROUND}

    @staticmethod
    def getSelectedTabNo(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TAB_NO}

    def setSelectedTabNo(self, selected_tab_no:str, *args, **kwargs) -> dict:
        printLog("setSelectedTabNo", selected_tab_no)
        config.SELECTED_TAB_NO = selected_tab_no
        self._normalizeSelectedYourLanguageForTranscription()
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.SELECTED_TAB_NO}

    @staticmethod
    def getTranslationEngines(*args, **kwargs) -> dict:
        input_engines = model.findTranslationEngines(
            config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO],
            config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
            config.SELECTABLE_TRANSLATION_ENGINE_STATUS,
            )
        output_engines = model.findTranslationEngines(
            config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO],
            config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO],
            config.SELECTABLE_TRANSLATION_ENGINE_STATUS,
            )
        engines = [engine for engine in input_engines if engine in output_engines]

        return {"status":200, "result":engines}

    @staticmethod
    def getListLanguageAndCountry(*args, **kwargs) -> dict:
        return {"status":200, "result": model.getListLanguageAndCountry()}

    @staticmethod
    def getMicHostList(*args, **kwargs) -> dict:
        return {"status":200, "result": model.getListMicHost()}

    @staticmethod
    def getMicDeviceList(*args, **kwargs) -> dict:
        return {"status":200, "result": model.getListMicDevice()}

    @staticmethod
    def getSpeakerDeviceList(*args, **kwargs) -> dict:
        return {"status":200, "result": model.getListSpeakerDevice()}

    @staticmethod
    def getSelectedTranslationEngines(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TRANSLATION_ENGINES}

    @staticmethod
    def setSelectedTranslationEngines(data:dict, *args, **kwargs) -> dict:
        config.SELECTED_TRANSLATION_ENGINES = data
        return {"status":200,"result":config.SELECTED_TRANSLATION_ENGINES}

    @staticmethod
    def getSelectedYourLanguages(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_YOUR_LANGUAGES}

    def setSelectedYourLanguages(self, select:dict, *args, **kwargs) -> dict:
        if self._selectedTabLanguagesSupported(select) is False:
            return {"status":200, "result":config.SELECTED_YOUR_LANGUAGES}
        config.SELECTED_YOUR_LANGUAGES = select
        self._normalizeSelectedYourLanguageForTranscription()
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.SELECTED_YOUR_LANGUAGES}

    @staticmethod
    def getSelectedYourTranslationLanguages(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_YOUR_TRANSLATION_LANGUAGES}

    def setSelectedYourTranslationLanguages(self, select:dict, *args, **kwargs) -> dict:
        config.SELECTED_YOUR_TRANSLATION_LANGUAGES = select
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.SELECTED_YOUR_TRANSLATION_LANGUAGES}

    @staticmethod
    def getSelectedTargetLanguages(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TARGET_LANGUAGES}

    def setSelectedTargetLanguages(self, select:dict, *args, **kwargs) -> dict:
        if config.ENABLE_TRANSCRIPTION_RECEIVE is True and self._selectedTabLanguagesSupported(select) is False:
            return {"status":200, "result":config.SELECTED_TARGET_LANGUAGES}
        config.SELECTED_TARGET_LANGUAGES = select
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.SELECTED_TARGET_LANGUAGES}

    @staticmethod
    def getTranscriptionEngines(*args, **kwargs) -> dict:
        engines = [key for key, value in config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS.items() if value is True]
        return {"status":200, "result":engines}

    @staticmethod
    def getSelectedTranscriptionEngine(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TRANSCRIPTION_ENGINE}

    def setSelectedTranscriptionEngine(self, data, *args, **kwargs) -> dict:
        config.SELECTED_TRANSCRIPTION_ENGINE = str(data)
        self._normalizeTranscriptionRuntimeSelection(notify=True)
        self._normalizeSelectedYourLanguageForTranscription()
        return {"status":200, "result":config.SELECTED_TRANSCRIPTION_ENGINE}

    @staticmethod
    def getConvertMessageToRomaji(*args, **kwargs) -> dict:
        return {"status":200, "result":config.CONVERT_MESSAGE_TO_ROMAJI}

    @staticmethod
    def setEnableConvertMessageToRomaji(*args, **kwargs) -> dict:
        if config.CONVERT_MESSAGE_TO_ROMAJI is False:
            if config.CONVERT_MESSAGE_TO_HIRAGANA is False:
                model.startTransliteration()
            config.CONVERT_MESSAGE_TO_ROMAJI = True
        return {"status":200, "result":config.CONVERT_MESSAGE_TO_ROMAJI}

    @staticmethod
    def setDisableConvertMessageToRomaji(*args, **kwargs) -> dict:
        if config.CONVERT_MESSAGE_TO_ROMAJI is True:
            if config.CONVERT_MESSAGE_TO_HIRAGANA is False:
                model.stopTransliteration()
            config.CONVERT_MESSAGE_TO_ROMAJI = False
        return {"status":200, "result":config.CONVERT_MESSAGE_TO_ROMAJI}

    @staticmethod
    def getConvertMessageToHiragana(*args, **kwargs) -> dict:
        return {"status":200, "result":config.CONVERT_MESSAGE_TO_HIRAGANA}

    @staticmethod
    def setEnableConvertMessageToHiragana(*args, **kwargs) -> dict:
        if config.CONVERT_MESSAGE_TO_HIRAGANA is False:
            if config.CONVERT_MESSAGE_TO_ROMAJI is False:
                model.startTransliteration()
            config.CONVERT_MESSAGE_TO_HIRAGANA = True
        return {"status":200, "result":config.CONVERT_MESSAGE_TO_HIRAGANA}

    @staticmethod
    def setDisableConvertMessageToHiragana(*args, **kwargs) -> dict:
        if config.CONVERT_MESSAGE_TO_HIRAGANA is True:
            if config.CONVERT_MESSAGE_TO_ROMAJI is False:
                model.stopTransliteration()
            config.CONVERT_MESSAGE_TO_HIRAGANA = False
        return {"status":200, "result":config.CONVERT_MESSAGE_TO_HIRAGANA}

    @staticmethod
    def getMainWindowSidebarCompactMode(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE}

    @staticmethod
    def setEnableMainWindowSidebarCompactMode(*args, **kwargs) -> dict:
        if config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE is False:
            config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE = True
        return {"status":200, "result":config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE}

    @staticmethod
    def setDisableMainWindowSidebarCompactMode(*args, **kwargs) -> dict:
        if config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE is True:
            config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE = False
        return {"status":200, "result":config.MAIN_WINDOW_SIDEBAR_COMPACT_MODE}

    @staticmethod
    def getTransparency(*args, **kwargs) -> dict:
        return {"status":200, "result":config.TRANSPARENCY}

    @staticmethod
    def setTransparency(data, *args, **kwargs) -> dict:
        config.TRANSPARENCY = int(data)
        return {"status":200, "result":config.TRANSPARENCY}

    @staticmethod
    def getUiScaling(*args, **kwargs) -> dict:
        return {"status":200, "result":config.UI_SCALING}

    @staticmethod
    def setUiScaling(data, *args, **kwargs) -> dict:
        config.UI_SCALING = int(data)
        return {"status":200, "result":config.UI_SCALING}

    @staticmethod
    def getTextboxUiScaling(*args, **kwargs) -> dict:
        return {"status":200, "result":config.TEXTBOX_UI_SCALING}

    @staticmethod
    def setTextboxUiScaling(data, *args, **kwargs) -> dict:
        config.TEXTBOX_UI_SCALING = int(data)
        return {"status":200, "result":config.TEXTBOX_UI_SCALING}

    @staticmethod
    def getMessageBoxRatio(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MESSAGE_BOX_RATIO}

    @staticmethod
    def setMessageBoxRatio(data, *args, **kwargs) -> dict:
        config.MESSAGE_BOX_RATIO = data
        return {"status":200, "result":config.MESSAGE_BOX_RATIO}

    @staticmethod
    def getSendMessageButtonType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SEND_MESSAGE_BUTTON_TYPE}

    @staticmethod
    def setSendMessageButtonType(data, *args, **kwargs) -> dict:
        config.SEND_MESSAGE_BUTTON_TYPE = data
        return {"status":200, "result":config.SEND_MESSAGE_BUTTON_TYPE}

    @staticmethod
    def getShowResendButton(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SHOW_RESEND_BUTTON}

    @staticmethod
    def setEnableShowResendButton(*args, **kwargs) -> dict:
        if config.SHOW_RESEND_BUTTON is False:
            config.SHOW_RESEND_BUTTON = True
        return {"status":200, "result":config.SHOW_RESEND_BUTTON}

    @staticmethod
    def setDisableShowResendButton(*args, **kwargs) -> dict:
        if config.SHOW_RESEND_BUTTON is True:
            config.SHOW_RESEND_BUTTON = False
        return {"status":200, "result":config.SHOW_RESEND_BUTTON}

    @staticmethod
    def getFontFamily(*args, **kwargs) -> dict:
        return {"status":200, "result":config.FONT_FAMILY}

    @staticmethod
    def setFontFamily(data, *args, **kwargs) -> dict:
        config.FONT_FAMILY = data
        return {"status":200, "result":config.FONT_FAMILY}

    @staticmethod
    def getUiLanguage(*args, **kwargs) -> dict:
        return {"status":200, "result":config.UI_LANGUAGE}

    @staticmethod
    def setUiLanguage(data, *args, **kwargs) -> dict:
        config.UI_LANGUAGE = data
        return {"status":200, "result":config.UI_LANGUAGE}

    @staticmethod
    def getMainWindowGeometry(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MAIN_WINDOW_GEOMETRY}

    @staticmethod
    def setMainWindowGeometry(data, *args, **kwargs) -> dict:
        config.MAIN_WINDOW_GEOMETRY = data
        return {"status":200, "result":config.MAIN_WINDOW_GEOMETRY}

    @staticmethod
    def getAutoMicSelect(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTO_MIC_SELECT}

    def applyAutoMicSelect(self) -> None:
        device_manager.setCallbackProcessBeforeUpdateMicDevices(self.stopAccessMicDevices)
        device_manager.setCallbackDefaultMicDevice(self.updateSelectedMicDevice)
        device_manager.setCallbackProcessAfterUpdateMicDevices(self.restartAccessMicDevices)
        device_manager.forceUpdateAndSetMicDevices()
        device_manager.startMonitoring()

    def setEnableAutoMicSelect(self, *args, **kwargs) -> dict:
        if config.AUTO_MIC_SELECT is False:
            self.applyAutoMicSelect()
            config.AUTO_MIC_SELECT = True
        return {"status":200, "result":config.AUTO_MIC_SELECT}

    @staticmethod
    def setDisableAutoMicSelect(*args, **kwargs) -> dict:
        if config.AUTO_SPEAKER_SELECT is False:
            device_manager.stopMonitoring()

        if config.AUTO_MIC_SELECT is True:
            device_manager.clearCallbackProcessBeforeUpdateMicDevices()
            device_manager.clearCallbackDefaultMicDevice()
            device_manager.clearCallbackProcessAfterUpdateMicDevices()
            config.AUTO_MIC_SELECT = False
        return {"status":200, "result":config.AUTO_MIC_SELECT}

    @staticmethod
    def getSelectedMicHost(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_MIC_HOST}

    def setSelectedMicHost(self, data, *args, **kwargs) -> dict:
        config.SELECTED_MIC_HOST = data
        config.SELECTED_MIC_DEVICE = model.getMicDefaultDevice()
        if config.ENABLE_CHECK_ENERGY_SEND is True:
            self.stopThreadingCheckMicEnergy()
            self.startThreadingTranscriptionSendMessage()
        self.run(200, self.run_mapping["selected_mic_device"], config.SELECTED_MIC_DEVICE)
        return {"status":200, "result":config.SELECTED_MIC_HOST}

    @staticmethod
    def getSelectedMicDevice(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_MIC_DEVICE}

    def setSelectedMicDevice(self, data, *args, **kwargs) -> dict:
        config.SELECTED_MIC_DEVICE = data
        if config.ENABLE_CHECK_ENERGY_SEND is True:
            self.stopThreadingCheckMicEnergy()
            self.startThreadingTranscriptionSendMessage()
        return {"status":200, "result": config.SELECTED_MIC_DEVICE}

    @staticmethod
    def getMicThreshold(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_THRESHOLD}

    @staticmethod
    def setMicThreshold(data, *args, **kwargs) -> dict:
        try:
            data = int(data)
            if 0 <= data <= config.MAX_MIC_THRESHOLD:
                config.MIC_THRESHOLD = data
                status = 200
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_MIC_THRESHOLD,
                data=config.MIC_THRESHOLD
            )
        else:
            response = {"status":status, "result":config.MIC_THRESHOLD}
        return response

    @staticmethod
    def getMicAutomaticThreshold(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_AUTOMATIC_THRESHOLD}

    @staticmethod
    def setEnableMicAutomaticThreshold(*args, **kwargs) -> dict:
        if config.MIC_AUTOMATIC_THRESHOLD is False:
            config.MIC_AUTOMATIC_THRESHOLD = True
        return {"status":200, "result":config.MIC_AUTOMATIC_THRESHOLD}

    @staticmethod
    def setDisableMicAutomaticThreshold(*args, **kwargs) -> dict:
        if config.MIC_AUTOMATIC_THRESHOLD is True:
            config.MIC_AUTOMATIC_THRESHOLD = False
        return {"status":200, "result":config.MIC_AUTOMATIC_THRESHOLD}

    @staticmethod
    def getMicRecordTimeout(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_RECORD_TIMEOUT}

    @staticmethod
    def setMicRecordTimeout(data, *args, **kwargs) -> dict:
        printLog("Set Mic Record Timeout", data)
        try:
            data = int(data)
            if 0 <= data <= config.MIC_PHRASE_TIMEOUT:
                config.MIC_RECORD_TIMEOUT = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_MIC_RECORD_TIMEOUT,
                data=config.MIC_RECORD_TIMEOUT
            )
        else:
            response = {"status":200, "result":config.MIC_RECORD_TIMEOUT}
        return response

    @staticmethod
    def getMicPhraseTimeout(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_PHRASE_TIMEOUT}

    @staticmethod
    def setMicPhraseTimeout(data, *args, **kwargs) -> dict:
        try:
            data = int(data)
            if data >= config.MIC_RECORD_TIMEOUT:
                config.MIC_PHRASE_TIMEOUT = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_MIC_PHRASE_TIMEOUT,
                data=config.MIC_PHRASE_TIMEOUT
            )
        else:
            response = {"status":200, "result":config.MIC_PHRASE_TIMEOUT}
        return response

    @staticmethod
    def getMicMaxPhrases(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_MAX_PHRASES}

    @staticmethod
    def setMicMaxPhrases(data, *args, **kwargs) -> dict:
        try:
            data = int(data)
            if 0 <= data:
                config.MIC_MAX_PHRASES = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_MIC_MAX_PHRASES,
                data=config.MIC_MAX_PHRASES
            )
        else:
            response = {"status":200, "result":config.MIC_MAX_PHRASES}
        return response

    @staticmethod
    def getMicWordFilter(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_WORD_FILTER}

    @staticmethod
    def setMicWordFilter(data, *args, **kwargs) -> dict:
        config.MIC_WORD_FILTER = sorted(set(data), key=data.index)
        model.resetKeywordProcessor()
        model.addKeywords()
        return {"status":200, "result":config.MIC_WORD_FILTER}

    @staticmethod
    def getMicAvgLogprob(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_AVG_LOGPROB}

    @staticmethod
    def setMicAvgLogprob(data, *args, **kwargs) -> dict:
        config.MIC_AVG_LOGPROB = float(data)
        return {"status":200, "result":config.MIC_AVG_LOGPROB}

    @staticmethod
    def getMicNoSpeechProb(*args, **kwargs) -> dict:
        return {"status":200, "result":config.MIC_NO_SPEECH_PROB}

    @staticmethod
    def setMicNoSpeechProb(data, *args, **kwargs) -> dict:
        config.MIC_NO_SPEECH_PROB = float(data)
        return {"status":200, "result":config.MIC_NO_SPEECH_PROB}

    @staticmethod
    def getAutoSpeakerSelect(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTO_SPEAKER_SELECT}

    def applyAutoSpeakerSelect(self) -> None:
        device_manager.setCallbackProcessBeforeUpdateSpeakerDevices(self.stopAccessSpeakerDevices)
        device_manager.setCallbackDefaultSpeakerDevice(self.updateSelectedSpeakerDevice)
        device_manager.setCallbackProcessAfterUpdateSpeakerDevices(self.restartAccessSpeakerDevices)
        device_manager.forceUpdateAndSetSpeakerDevices()
        device_manager.startMonitoring()

    def setEnableAutoSpeakerSelect(self, *args, **kwargs) -> dict:
        if config.AUTO_SPEAKER_SELECT is False:
            self.applyAutoSpeakerSelect()
            config.AUTO_SPEAKER_SELECT = True
        return {"status":200, "result":config.AUTO_SPEAKER_SELECT}

    @staticmethod
    def setDisableAutoSpeakerSelect(*args, **kwargs) -> dict:
        if config.AUTO_MIC_SELECT is False:
            device_manager.stopMonitoring()

        if config.AUTO_SPEAKER_SELECT is True:
            device_manager.clearCallbackProcessBeforeUpdateSpeakerDevices()
            device_manager.clearCallbackDefaultSpeakerDevice()
            device_manager.clearCallbackProcessAfterUpdateSpeakerDevices()
            config.AUTO_SPEAKER_SELECT = False
        return {"status":200, "result":config.AUTO_SPEAKER_SELECT}

    @staticmethod
    def getSelectedSpeakerDevice(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_SPEAKER_DEVICE}

    def setSelectedSpeakerDevice(self, data, *args, **kwargs) -> dict:
        config.SELECTED_SPEAKER_DEVICE = data
        if config.ENABLE_CHECK_ENERGY_RECEIVE is True:
            self.stopThreadingCheckSpeakerEnergy()
            self.startThreadingTranscriptionReceiveMessage()
        return {"status":200, "result":config.SELECTED_SPEAKER_DEVICE}

    @staticmethod
    def getSpeakerThreshold(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_THRESHOLD}

    @staticmethod
    def setSpeakerThreshold(data, *args, **kwargs) -> dict:
        printLog("Set Speaker Energy Threshold", data)
        try:
            data = int(data)
            if 0 <= data <= config.MAX_SPEAKER_THRESHOLD:
                config.SPEAKER_THRESHOLD = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_SPEAKER_THRESHOLD,
                data=config.SPEAKER_THRESHOLD
            )
        else:
            response = {"status":200, "result":config.SPEAKER_THRESHOLD}
        return response

    @staticmethod
    def getSpeakerAutomaticThreshold(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_AUTOMATIC_THRESHOLD}

    @staticmethod
    def setEnableSpeakerAutomaticThreshold(*args, **kwargs) -> dict:
        if config.SPEAKER_AUTOMATIC_THRESHOLD is False:
            config.SPEAKER_AUTOMATIC_THRESHOLD = True
        return {"status":200, "result":config.SPEAKER_AUTOMATIC_THRESHOLD}

    @staticmethod
    def setDisableSpeakerAutomaticThreshold(*args, **kwargs) -> dict:
        if config.SPEAKER_AUTOMATIC_THRESHOLD is True:
            config.SPEAKER_AUTOMATIC_THRESHOLD = False
        return {"status":200, "result":config.SPEAKER_AUTOMATIC_THRESHOLD}

    @staticmethod
    def getSpeakerRecordTimeout(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_RECORD_TIMEOUT}

    @staticmethod
    def setSpeakerRecordTimeout(data, *args, **kwargs) -> dict:
        try:
            data = int(data)
            if 0 <= data <= config.SPEAKER_PHRASE_TIMEOUT:
                config.SPEAKER_RECORD_TIMEOUT = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_SPEAKER_RECORD_TIMEOUT,
                data=config.SPEAKER_RECORD_TIMEOUT
            )
        else:
            response = {"status":200, "result":config.SPEAKER_RECORD_TIMEOUT}
        return response

    @staticmethod
    def getSpeakerPhraseTimeout(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_PHRASE_TIMEOUT}

    @staticmethod
    def setSpeakerPhraseTimeout(data, *args, **kwargs) -> dict:
        try:
            data = int(data)
            if 0 <= data and data >= config.SPEAKER_RECORD_TIMEOUT:
                config.SPEAKER_PHRASE_TIMEOUT = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_SPEAKER_PHRASE_TIMEOUT,
                data=config.SPEAKER_PHRASE_TIMEOUT
            )
        else:
            response = {"status":200, "result":config.SPEAKER_PHRASE_TIMEOUT}
        return response

    @staticmethod
    def getSpeakerMaxPhrases(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_MAX_PHRASES}

    @staticmethod
    def setSpeakerMaxPhrases(data, *args, **kwargs) -> dict:
        printLog("Set Speaker Max Phrases", data)
        try:
            data = int(data)
            if 0 <= data:
                config.SPEAKER_MAX_PHRASES = data
            else:
                raise ValueError()
        except Exception:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_SPEAKER_MAX_PHRASES,
                data=config.SPEAKER_MAX_PHRASES
            )
        else:
            response = {"status":200, "result":config.SPEAKER_MAX_PHRASES}
        return response

    @staticmethod
    def getHotkeys(*args, **kwargs) -> dict:
        return {"status":200, "result":config.HOTKEYS}

    @staticmethod
    def setHotkeys(data, *args, **kwargs) -> dict:
        config.HOTKEYS = data
        return {"status":200, "result":config.HOTKEYS}

    @staticmethod
    def getSpeakerAvgLogprob(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_AVG_LOGPROB}

    @staticmethod
    def setSpeakerAvgLogprob(data, *args, **kwargs) -> dict:
        config.SPEAKER_AVG_LOGPROB = float(data)
        return {"status":200, "result":config.SPEAKER_AVG_LOGPROB}

    @staticmethod
    def getSpeakerNoSpeechProb(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SPEAKER_NO_SPEECH_PROB}

    @staticmethod
    def setSpeakerNoSpeechProb(data, *args, **kwargs) -> dict:
        config.SPEAKER_NO_SPEECH_PROB = float(data)
        return {"status":200, "result":config.SPEAKER_NO_SPEECH_PROB}

    @staticmethod
    def getOscIpAddress(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OSC_IP_ADDRESS}

    def setOscIpAddress(self, data, *args, **kwargs) -> dict:
        if isValidIpAddress(data) is False:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_INVALID_IP,
                data=config.OSC_IP_ADDRESS
            )
        else:
            try:
                model.setOscIpAddress(data)
                config.OSC_IP_ADDRESS = data
                if model.getIsOscQueryEnabled() is True:
                    self.enableOscQuery()
                else:
                    mute_sync_info_flag = False
                    if config.VRC_MIC_MUTE_SYNC is True:
                        self.setDisableVrcMicMuteSync()
                        mute_sync_info_flag = True
                    self.disableOscQuery(mute_sync_info=mute_sync_info_flag)

                response = {"status":200, "result":config.OSC_IP_ADDRESS}
            except Exception:
                model.setOscIpAddress(config.OSC_IP_ADDRESS)
                response = VRCTError.create_error_response(
                    ErrorCode.VALIDATION_CANNOT_SET_IP,
                    data=config.OSC_IP_ADDRESS
                )
        return response

    @staticmethod
    def getOscPort(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OSC_PORT}

    @staticmethod
    def setOscPort(data, *args, **kwargs) -> dict:
        config.OSC_PORT = int(data)
        model.setOscPort(config.OSC_PORT)
        return {"status":200, "result":config.OSC_PORT}

    @staticmethod
    def getNotificationVrcSfx(*args, **kwargs) -> dict:
        return {"status":200, "result":config.NOTIFICATION_VRC_SFX}

    @staticmethod
    def setEnableNotificationVrcSfx(*args, **kwargs) -> dict:
        if config.NOTIFICATION_VRC_SFX is False:
            config.NOTIFICATION_VRC_SFX = True
        return {"status":200, "result":config.NOTIFICATION_VRC_SFX}

    @staticmethod
    def setDisableNotificationVrcSfx(*args, **kwargs) -> dict:
        if config.NOTIFICATION_VRC_SFX is True:
            config.NOTIFICATION_VRC_SFX = False
        return {"status":200, "result":config.NOTIFICATION_VRC_SFX}

    @staticmethod
    def getDeepLAuthKey(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTH_KEYS["DeepL_API"]}

    def setDeeplAuthKey(self, data, *args, **kwargs) -> dict:
        printLog("Set DeepL Auth Key", data)
        translator_name = "DeepL_API"
        try:
            data = str(data)
            if len(data) == 36 or len(data) == 39:
                result = model.authenticationTranslatorDeepLAuthKey(auth_key=data)
                if result is True:
                    key = data
                    auth_keys = config.AUTH_KEYS
                    auth_keys[translator_name] = key
                    config.AUTH_KEYS = auth_keys
                    config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                    self.updateTranslationEngineAndEngineList()
                    response = {"status":200, "result":config.AUTH_KEYS[translator_name]}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.AUTH_DEEPL_FAILED,
                        data=config.AUTH_KEYS[translator_name]
                    )
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.AUTH_DEEPL_LENGTH,
                    data=config.AUTH_KEYS[translator_name]
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.AUTH_KEYS[translator_name]
            )
        return response

    def delDeeplAuthKey(self, *args, **kwargs) -> dict:
        translator_name = "DeepL_API"
        auth_keys = config.AUTH_KEYS
        auth_keys[translator_name] = None
        config.AUTH_KEYS = auth_keys
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.AUTH_KEYS[translator_name]}

    def getPlamoAuthKey(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTH_KEYS["Plamo_API"]}

    def setPlamoAuthKey(self, data, *args, **kwargs) -> dict:
        printLog("Set Plamo Auth Key", data)
        translator_name = "Plamo_API"
        try:
            data = str(data)
            if len(data) >= 72:
                result = model.authenticationTranslatorPlamoAuthKey(auth_key=data)
                if result is True:
                    key = data
                    auth_keys = config.AUTH_KEYS
                    auth_keys[translator_name] = key
                    config.AUTH_KEYS = auth_keys
                    config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                    config.SELECTABLE_PLAMO_MODEL_LIST = model.getTranslatorPlamoModelList()
                    self.run(200, self.run_mapping["selectable_plamo_model_list"], config.SELECTABLE_PLAMO_MODEL_LIST)
                    if config.SELECTED_PLAMO_MODEL not in config.SELECTABLE_PLAMO_MODEL_LIST:
                        config.SELECTED_PLAMO_MODEL = config.SELECTABLE_PLAMO_MODEL_LIST[0]
                    model.setTranslatorPlamoModel(model=config.SELECTED_PLAMO_MODEL)
                    self.run(200, self.run_mapping["selected_plamo_model"], config.SELECTED_PLAMO_MODEL)
                    model.updateTranslatorPlamoClient()
                    self.updateTranslationEngineAndEngineList()
                    response = {"status":200, "result":config.AUTH_KEYS[translator_name]}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.AUTH_PLAMO_FAILED,
                        data=None
                    )
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.AUTH_PLAMO_LENGTH,
                    data=None
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=None
            )
        if response["status"] == 400:
            self.delPlamoAuthKey()
        return response

    def delPlamoAuthKey(self, *args, **kwargs) -> dict:
        translator_name = "Plamo_API"
        auth_keys = config.AUTH_KEYS
        auth_keys[translator_name] = None
        config.AUTH_KEYS = auth_keys
        config.SELECTABLE_PLAMO_MODEL_LIST = []
        config.SELECTED_PLAMO_MODEL = None
        self.run(200, self.run_mapping["selectable_plamo_model_list"], config.SELECTABLE_PLAMO_MODEL_LIST)
        self.run(200, self.run_mapping["selected_plamo_model"], config.SELECTED_PLAMO_MODEL)
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.AUTH_KEYS[translator_name]}

    def getPlamoModelList(self, *args, **kwargs) -> dict:
        return {"status":200, "result": config.SELECTABLE_PLAMO_MODEL_LIST}

    def getPlamoModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_PLAMO_MODEL}

    def setPlamoModel(self, data, *args, **kwargs) -> dict:
        printLog("Set Plamo Model", data)
        try:
            data = str(data)
            result = model.setTranslatorPlamoModel(model=data)
            if result is True:
                config.SELECTED_PLAMO_MODEL = data
                model.setTranslatorPlamoModel(model=config.SELECTED_PLAMO_MODEL)
                model.updateTranslatorPlamoClient()
                response = {"status":200, "result":config.SELECTED_PLAMO_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_PLAMO_INVALID,
                    data=config.SELECTED_PLAMO_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_PLAMO_MODEL
            )
        return response

    def getGeminiAuthKey(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTH_KEYS["Gemini_API"]}

    def setGeminiAuthKey(self, data, *args, **kwargs) -> dict:
        printLog("Set Gemini Auth Key", data)
        translator_name = "Gemini_API"
        try:
            data = str(data)
            if len(data) >= 39:
                result = model.authenticationTranslatorGeminiAuthKey(auth_key=data)
                if result is True:
                    key = data
                    auth_keys = config.AUTH_KEYS
                    auth_keys[translator_name] = key
                    config.AUTH_KEYS = auth_keys
                    config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                    config.SELECTABLE_GEMINI_MODEL_LIST = model.getTranslatorGeminiModelList()
                    self.run(200, self.run_mapping["selectable_gemini_model_list"], config.SELECTABLE_GEMINI_MODEL_LIST)
                    if config.SELECTED_GEMINI_MODEL not in config.SELECTABLE_GEMINI_MODEL_LIST:
                        config.SELECTED_GEMINI_MODEL = config.SELECTABLE_GEMINI_MODEL_LIST[0]
                    model.setTranslatorGeminiModel(model=config.SELECTED_GEMINI_MODEL)
                    self.run(200, self.run_mapping["selected_gemini_model"], config.SELECTED_GEMINI_MODEL)
                    model.updateTranslatorGeminiClient()
                    self.updateTranslationEngineAndEngineList()
                    response = {"status":200, "result":config.AUTH_KEYS[translator_name]}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.AUTH_GEMINI_FAILED,
                        data=None
                    )
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.AUTH_GEMINI_LENGTH,
                    data=None
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=None
            )
        if response["status"] == 400:
            self.delGeminiAuthKey()
        return response

    def delGeminiAuthKey(self, *args, **kwargs) -> dict:
        translator_name = "Gemini_API"
        auth_keys = config.AUTH_KEYS
        auth_keys[translator_name] = None
        config.AUTH_KEYS = auth_keys
        config.SELECTABLE_GEMINI_MODEL_LIST = []
        config.SELECTED_GEMINI_MODEL = None
        self.run(200, self.run_mapping["selectable_gemini_model_list"], config.SELECTABLE_GEMINI_MODEL_LIST)
        self.run(200, self.run_mapping["selected_gemini_model"], config.SELECTED_GEMINI_MODEL)
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.AUTH_KEYS[translator_name]}

    def getGeminiModelList(self, *args, **kwargs) -> dict:
        return {"status":200, "result": config.SELECTABLE_GEMINI_MODEL_LIST}

    def getGeminiModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_GEMINI_MODEL}

    def setGeminiModel(self, data, *args, **kwargs) -> dict:
        printLog("Set Gemini Model", data)
        try:
            data = str(data)
            result = model.setTranslatorGeminiModel(model=data)
            if result is True:
                config.SELECTED_GEMINI_MODEL = data
                model.setTranslatorGeminiModel(model=config.SELECTED_GEMINI_MODEL)
                model.updateTranslatorGeminiClient()
                response = {"status":200, "result":config.SELECTED_GEMINI_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_GEMINI_INVALID,
                    data=config.SELECTED_GEMINI_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_GEMINI_MODEL
            )
        return response

    @staticmethod
    def getOpenAIAuthKey(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTH_KEYS["OpenAI_API"]}

    def setOpenAIAuthKey(self, data, *args, **kwargs) -> dict:
        printLog("Set OpenAI Auth Key", data)
        translator_name = "OpenAI_API"
        try:
            data = str(data)
            if data.startswith("sk-") and len(data) >= 164:
                result = model.authenticationTranslatorOpenAIAuthKey(auth_key=data)
                if result is True:
                    key = data
                    auth_keys = config.AUTH_KEYS
                    auth_keys[translator_name] = key
                    config.AUTH_KEYS = auth_keys
                    config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                    config.SELECTABLE_OPENAI_MODEL_LIST = model.getTranslatorOpenAIModelList()
                    self.run(200, self.run_mapping["selectable_openai_model_list"], config.SELECTABLE_OPENAI_MODEL_LIST)
                    if config.SELECTED_OPENAI_MODEL not in config.SELECTABLE_OPENAI_MODEL_LIST:
                        config.SELECTED_OPENAI_MODEL = config.SELECTABLE_OPENAI_MODEL_LIST[0]
                    model.setTranslatorOpenAIModel(model=config.SELECTED_OPENAI_MODEL)
                    self.run(200, self.run_mapping["selected_openai_model"], config.SELECTED_OPENAI_MODEL)
                    model.updateTranslatorOpenAIClient()
                    self.updateTranslationEngineAndEngineList()
                    response = {"status":200, "result":config.AUTH_KEYS[translator_name]}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.AUTH_OPENAI_FAILED,
                        data=None
                    )
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.AUTH_OPENAI_INVALID,
                    data=None
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=None
            )
        if response["status"] == 400:
            self.delOpenAIAuthKey()
        return response

    def delOpenAIAuthKey(self, *args, **kwargs) -> dict:
        translator_name = "OpenAI_API"
        auth_keys = config.AUTH_KEYS
        auth_keys[translator_name] = None
        config.AUTH_KEYS = auth_keys
        config.SELECTABLE_OPENAI_MODEL_LIST = []
        config.SELECTED_OPENAI_MODEL = None
        self.run(200, self.run_mapping["selectable_openai_model_list"], config.SELECTABLE_OPENAI_MODEL_LIST)
        self.run(200, self.run_mapping["selected_openai_model"], config.SELECTED_OPENAI_MODEL)
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.AUTH_KEYS[translator_name]}

    def getOpenAIModelList(self, *args, **kwargs) -> dict:
        return {"status":200, "result": config.SELECTABLE_OPENAI_MODEL_LIST}

    def getOpenAIModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_OPENAI_MODEL}

    def setOpenAIModel(self, data, *args, **kwargs) -> dict:
        printLog("Set OpenAI Model", data)
        try:
            data = str(data)
            result = model.setTranslatorOpenAIModel(model=data)
            if result is True:
                config.SELECTED_OPENAI_MODEL = data
                model.setTranslatorOpenAIModel(model=config.SELECTED_OPENAI_MODEL)
                model.updateTranslatorOpenAIClient()
                response = {"status":200, "result":config.SELECTED_OPENAI_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_OPENAI_INVALID,
                    data=config.SELECTED_OPENAI_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_OPENAI_MODEL
            )
        return response

    @staticmethod
    def getGroqAuthKey(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTH_KEYS["Groq_API"]}

    def setGroqAuthKey(self, data, *args, **kwargs) -> dict:
        printLog("Set Groq Auth Key", data)
        translator_name = "Groq_API"
        try:
            data = str(data)
            if data.startswith("gsk") and len(data) >= 40:
                result = model.authenticationTranslatorGroqAuthKey(auth_key=data)
                if result is True:
                    key = data
                    auth_keys = config.AUTH_KEYS
                    auth_keys[translator_name] = key
                    config.AUTH_KEYS = auth_keys
                    config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                    config.SELECTABLE_GROQ_MODEL_LIST = model.getTranslatorGroqModelList()
                    self.run(200, self.run_mapping["selectable_groq_model_list"], config.SELECTABLE_GROQ_MODEL_LIST)
                    if config.SELECTED_GROQ_MODEL not in config.SELECTABLE_GROQ_MODEL_LIST:
                        config.SELECTED_GROQ_MODEL = config.SELECTABLE_GROQ_MODEL_LIST[0]
                    model.setTranslatorGroqModel(model=config.SELECTED_GROQ_MODEL)
                    self.run(200, self.run_mapping["selected_groq_model"], config.SELECTED_GROQ_MODEL)
                    model.updateTranslatorGroqClient()
                    self.updateTranslationEngineAndEngineList()
                    response = {"status":200, "result":config.AUTH_KEYS[translator_name]}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.AUTH_GROQ_FAILED,
                        data=None
                    )
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.AUTH_GROQ_INVALID,
                    data=None
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=None
            )
        if response["status"] == 400:
            self.delGroqAuthKey()
        return response

    def delGroqAuthKey(self, *args, **kwargs) -> dict:
        translator_name = "Groq_API"
        auth_keys = config.AUTH_KEYS
        auth_keys[translator_name] = None
        config.AUTH_KEYS = auth_keys
        config.SELECTABLE_GROQ_MODEL_LIST = []
        config.SELECTED_GROQ_MODEL = None
        self.run(200, self.run_mapping["selectable_groq_model_list"], config.SELECTABLE_GROQ_MODEL_LIST)
        self.run(200, self.run_mapping["selected_groq_model"], config.SELECTED_GROQ_MODEL)
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.AUTH_KEYS[translator_name]}

    def getGroqModelList(self, *args, **kwargs) -> dict:
        return {"status":200, "result": config.SELECTABLE_GROQ_MODEL_LIST}

    def getGroqModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_GROQ_MODEL}

    def setGroqModel(self, data, *args, **kwargs) -> dict:
        printLog("Set Groq Model", data)
        try:
            data = str(data)
            result = model.setTranslatorGroqModel(model=data)
            if result is True:
                config.SELECTED_GROQ_MODEL = data
                model.setTranslatorGroqModel(model=config.SELECTED_GROQ_MODEL)
                model.updateTranslatorGroqClient()
                response = {"status":200, "result":config.SELECTED_GROQ_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_GROQ_INVALID,
                    data=config.SELECTED_GROQ_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_GROQ_MODEL
            )
        return response

    @staticmethod
    def getOpenRouterAuthKey(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTH_KEYS["OpenRouter_API"]}

    def setOpenRouterAuthKey(self, data, *args, **kwargs) -> dict:
        printLog("Set OpenRouter Auth Key", data)
        translator_name = "OpenRouter_API"
        try:
            data = str(data)
            if len(data) >= 20:  # OpenRouter API key basic validation
                result = model.authenticationTranslatorOpenRouterAuthKey(auth_key=data)
                if result is True:
                    key = data
                    auth_keys = config.AUTH_KEYS
                    auth_keys[translator_name] = key
                    config.AUTH_KEYS = auth_keys
                    config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                    config.SELECTABLE_OPENROUTER_MODEL_LIST = model.getTranslatorOpenRouterModelList()
                    self.run(200, self.run_mapping["selectable_openrouter_model_list"], config.SELECTABLE_OPENROUTER_MODEL_LIST)
                    if config.SELECTED_OPENROUTER_MODEL not in config.SELECTABLE_OPENROUTER_MODEL_LIST:
                        config.SELECTED_OPENROUTER_MODEL = config.SELECTABLE_OPENROUTER_MODEL_LIST[0]
                    model.setTranslatorOpenRouterModel(model=config.SELECTED_OPENROUTER_MODEL)
                    self.run(200, self.run_mapping["selected_openrouter_model"], config.SELECTED_OPENROUTER_MODEL)
                    model.updateTranslatorOpenRouterClient()
                    self.updateTranslationEngineAndEngineList()
                    response = {"status":200, "result":config.AUTH_KEYS[translator_name]}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.AUTH_OPENROUTER_FAILED,
                        data=None
                    )
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.AUTH_OPENROUTER_INVALID,
                    data=None
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=None
            )
        if response["status"] == 400:
            self.delOpenRouterAuthKey()
        return response

    def delOpenRouterAuthKey(self, *args, **kwargs) -> dict:
        translator_name = "OpenRouter_API"
        auth_keys = config.AUTH_KEYS
        auth_keys[translator_name] = None
        config.AUTH_KEYS = auth_keys
        config.SELECTABLE_OPENROUTER_MODEL_LIST = []
        config.SELECTED_OPENROUTER_MODEL = None
        self.run(200, self.run_mapping["selectable_openrouter_model_list"], config.SELECTABLE_OPENROUTER_MODEL_LIST)
        self.run(200, self.run_mapping["selected_openrouter_model"], config.SELECTED_OPENROUTER_MODEL)
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
        self.updateTranslationEngineAndEngineList()
        return {"status":200, "result":config.AUTH_KEYS[translator_name]}

    def getOpenRouterModelList(self, *args, **kwargs) -> dict:
        return {"status":200, "result": config.SELECTABLE_OPENROUTER_MODEL_LIST}

    def getOpenRouterModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_OPENROUTER_MODEL}

    def setOpenRouterModel(self, data, *args, **kwargs) -> dict:
        printLog("Set OpenRouter Model", data)
        try:
            data = str(data)
            result = model.setTranslatorOpenRouterModel(model=data)
            if result is True:
                config.SELECTED_OPENROUTER_MODEL = data
                model.setTranslatorOpenRouterModel(model=config.SELECTED_OPENROUTER_MODEL)
                model.updateTranslatorOpenRouterClient()
                response = {"status":200, "result":config.SELECTED_OPENROUTER_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_OPENROUTER_INVALID,
                    data=config.SELECTED_OPENROUTER_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_OPENROUTER_MODEL
            )
        return response

    def getTranslatorLMStudioConnection(self, *args, **kwargs) -> dict:
        return {"status":200, "result":model.getTranslatorLMStudioConnected()}

    def checkTranslatorLMStudioConnection(self, *args, **kwargs) -> dict:
        printLog("Check Translator LMStudio Connection")
        translator_name = "LMStudio"
        try:
            result = model.authenticationTranslatorLMStudio(base_url=config.LMSTUDIO_URL)
            if result is True:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                config.SELECTABLE_LMSTUDIO_MODEL_LIST = model.getTranslatorLMStudioModelList()
                self.run(200, self.run_mapping["selectable_lmstudio_model_list"], config.SELECTABLE_LMSTUDIO_MODEL_LIST)
                if len(config.SELECTABLE_LMSTUDIO_MODEL_LIST) == 0:
                    raise Exception("No LMStudio models available")
                if config.SELECTED_LMSTUDIO_MODEL not in config.SELECTABLE_LMSTUDIO_MODEL_LIST:
                    config.SELECTED_LMSTUDIO_MODEL = config.SELECTABLE_LMSTUDIO_MODEL_LIST[0]
                model.setTranslatorLMStudioModel(model=config.SELECTED_LMSTUDIO_MODEL)
                self.run(200, self.run_mapping["selected_lmstudio_model"], config.SELECTED_LMSTUDIO_MODEL)
                model.updateTranslatorLMStudioClient()
                self.updateTranslationEngineAndEngineList()
                response = {"status":200, "result":True}
            else:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
                config.SELECTABLE_LMSTUDIO_MODEL_LIST = []
                config.SELECTED_LMSTUDIO_MODEL = None
                self.run(200, self.run_mapping["selectable_lmstudio_model_list"], config.SELECTABLE_LMSTUDIO_MODEL_LIST)
                self.run(200, self.run_mapping["selected_lmstudio_model"], config.SELECTED_LMSTUDIO_MODEL)
                self.updateTranslationEngineAndEngineList()
                response = VRCTError.create_error_response(
                    ErrorCode.CONNECTION_LMSTUDIO_FAILED,
                    data=False
                )
        except Exception as e:
            errorLogging()
            config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
            config.SELECTABLE_LMSTUDIO_MODEL_LIST = []
            config.SELECTED_LMSTUDIO_MODEL = None
            self.run(200, self.run_mapping["selectable_lmstudio_model_list"], config.SELECTABLE_LMSTUDIO_MODEL_LIST)
            self.run(200, self.run_mapping["selected_lmstudio_model"], config.SELECTED_LMSTUDIO_MODEL)
            self.updateDownloadedCTranslate2ModelWeight(scan_all=True)
            self.updateDownloadedWhisperModelWeight(scan_all=True)
            self.updateDownloadedVoskModelWeight(scan_all=True)
            self.updateDownloadedParakeetModelWeight(scan_all=True)
            self.updateDownloadedSenseVoiceModelWeight(scan_all=True)
            self.updateTranslationEngineAndEngineList()
            response = VRCTError.create_exception_error_response(
                e,
                data=False
            )
        return response

    def getConnectedLMStudio(self, *args, **kwargs) -> dict:
        is_connected = model.getTranslatorLMStudioConnected()
        return {"status":200, "result": is_connected}

    def getTranslatorLMStudioURL(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.LMSTUDIO_URL}

    def setTranslatorLMStudioURL(self, data, *args, **kwargs) -> dict:
        printLog("Set Translator LMStudio URL", data)
        translator_name = "LMStudio"
        try:
            data = str(data)
            result = model.authenticationTranslatorLMStudio(base_url=data)
            if result is True:
                config.LMSTUDIO_URL = data
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                config.SELECTABLE_LMSTUDIO_MODEL_LIST = model.getTranslatorLMStudioModelList()
                self.run(200, self.run_mapping["selectable_lmstudio_model_list"], config.SELECTABLE_LMSTUDIO_MODEL_LIST)
                if len(config.SELECTABLE_LMSTUDIO_MODEL_LIST) == 0:
                    raise Exception("No LMStudio models available")
                if config.SELECTED_LMSTUDIO_MODEL not in config.SELECTABLE_LMSTUDIO_MODEL_LIST:
                    config.SELECTED_LMSTUDIO_MODEL = config.SELECTABLE_LMSTUDIO_MODEL_LIST[0]
                model.setTranslatorLMStudioModel(model=config.SELECTED_LMSTUDIO_MODEL)
                self.run(200, self.run_mapping["selected_lmstudio_model"], config.SELECTED_LMSTUDIO_MODEL)
                model.updateTranslatorLMStudioClient()
                self.updateTranslationEngineAndEngineList()
                response = {"status":200, "result":config.LMSTUDIO_URL}
            else:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
                config.SELECTABLE_LMSTUDIO_MODEL_LIST = []
                config.SELECTED_LMSTUDIO_MODEL = None
                self.run(200, self.run_mapping["selectable_lmstudio_model_list"], config.SELECTABLE_LMSTUDIO_MODEL_LIST)
                self.run(200, self.run_mapping["selected_lmstudio_model"], config.SELECTED_LMSTUDIO_MODEL)
                self.updateTranslationEngineAndEngineList()
                response = VRCTError.create_error_response(
                    ErrorCode.CONNECTION_LMSTUDIO_URL_INVALID,
                    data=config.LMSTUDIO_URL
                )
        except Exception as e:
            errorLogging()
            config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
            config.SELECTABLE_LMSTUDIO_MODEL_LIST = []
            config.SELECTED_LMSTUDIO_MODEL = None
            self.run(200, self.run_mapping["selectable_lmstudio_model_list"], config.SELECTABLE_LMSTUDIO_MODEL_LIST)
            self.run(200, self.run_mapping["selected_lmstudio_model"], config.SELECTED_LMSTUDIO_MODEL)
            self.updateTranslationEngineAndEngineList()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.LMSTUDIO_URL
            )
        return response

    def getTranslatorLStudioModelList(self, *args, **kwargs) -> dict:
        model_list = model.getTranslatorLMStudioModelList()
        return {"status":200, "result": model_list}

    def getTranslatorLMStudioModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_LMSTUDIO_MODEL}

    def setTranslatorLMStudioModel(self, data, *args, **kwargs) -> dict:
        printLog("Set Translator LMStudio Model", data)
        try:
            data = str(data)
            result = model.setTranslatorLMStudioModel(model=data)
            if result is True:
                config.SELECTED_LMSTUDIO_MODEL = data
                model.setTranslatorLMStudioModel(model=config.SELECTED_LMSTUDIO_MODEL)
                model.updateTranslatorLMStudioClient()
                response = {"status":200, "result":config.SELECTED_LMSTUDIO_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_LMSTUDIO_INVALID,
                    data=config.SELECTED_LMSTUDIO_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_LMSTUDIO_MODEL
            )
        return response

    def getTranslatorOllamaConnection(self, *args, **kwargs) -> dict:
        return {"status":200, "result":model.getTranslatorOllamaConnected()}

    def checkTranslatorOllamaConnection(self, *args, **kwargs) -> dict:
        printLog("Check Translator Ollama Connection")
        translator_name = "Ollama"
        try:
            result = model.authenticationTranslatorOllama()
            if result is True:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = True
                config.SELECTABLE_OLLAMA_MODEL_LIST = model.getTranslatorOllamaModelList()
                self.run(200, self.run_mapping["selectable_ollama_model_list"], config.SELECTABLE_OLLAMA_MODEL_LIST)
                if len(config.SELECTABLE_OLLAMA_MODEL_LIST) == 0:
                    raise Exception("No Ollama models available")
                if config.SELECTED_OLLAMA_MODEL not in config.SELECTABLE_OLLAMA_MODEL_LIST:
                    config.SELECTED_OLLAMA_MODEL = config.SELECTABLE_OLLAMA_MODEL_LIST[0]
                model.setTranslatorOllamaModel(model=config.SELECTED_OLLAMA_MODEL)
                self.run(200, self.run_mapping["selected_ollama_model"], config.SELECTED_OLLAMA_MODEL)
                model.updateTranslatorOllamaClient()
                self.updateTranslationEngineAndEngineList()
                response = {"status":200, "result":True}
            else:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
                config.SELECTABLE_OLLAMA_MODEL_LIST = []
                config.SELECTED_OLLAMA_MODEL = None
                self.run(200, self.run_mapping["selectable_ollama_model_list"], config.SELECTABLE_OLLAMA_MODEL_LIST)
                self.run(200, self.run_mapping["selected_ollama_model"], config.SELECTED_OLLAMA_MODEL)
                self.updateTranslationEngineAndEngineList()
                response = VRCTError.create_error_response(
                    ErrorCode.CONNECTION_OLLAMA_FAILED,
                    data=False
                )
        except Exception as e:
            errorLogging()
            config.SELECTABLE_TRANSLATION_ENGINE_STATUS[translator_name] = False
            config.SELECTABLE_OLLAMA_MODEL_LIST = []
            config.SELECTED_OLLAMA_MODEL = None
            self.run(200, self.run_mapping["selectable_ollama_model_list"], config.SELECTABLE_OLLAMA_MODEL_LIST)
            self.run(200, self.run_mapping["selected_ollama_model"], config.SELECTED_OLLAMA_MODEL)
            self.updateTranslationEngineAndEngineList()
            response = VRCTError.create_exception_error_response(
                e,
                data=False
            )
        return response

    def getTranslatorOllamaModelList(self, *args, **kwargs) -> dict:
        model_list = model.getTranslatorOllamaModelList()
        return {"status":200, "result": model_list}

    def getTranslatorOllamaModel(self, *args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_OLLAMA_MODEL}

    def setTranslatorOllamaModel(self, data, *args, **kwargs) -> dict:
        printLog("Set Translator Ollama Model", data)
        try:
            data = str(data)
            result = model.setTranslatorOllamaModel(model=data)
            if result is True:
                config.SELECTED_OLLAMA_MODEL = data
                model.setTranslatorOllamaModel(model=config.SELECTED_OLLAMA_MODEL)
                model.updateTranslatorOllamaClient()
                response = {"status":200, "result":config.SELECTED_OLLAMA_MODEL}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.MODEL_OLLAMA_INVALID,
                    data=config.SELECTED_OLLAMA_MODEL
                )
        except Exception as e:
            errorLogging()
            response = VRCTError.create_exception_error_response(
                e,
                data=config.SELECTED_OLLAMA_MODEL
            )
        return response

    @staticmethod
    def getCtranslate2WeightType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.CTRANSLATE2_WEIGHT_TYPE}

    @staticmethod
    def setCtranslate2WeightType(data, *args, **kwargs) -> dict:
        config.CTRANSLATE2_WEIGHT_TYPE = str(data)
        model.setChangedTranslatorParameters(True)
        return {"status":200, "result":config.CTRANSLATE2_WEIGHT_TYPE}

    @staticmethod
    def getSelectedTranslationComputeType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TRANSLATION_COMPUTE_TYPE}

    @staticmethod
    def setSelectedTranslationComputeType(data, *args, **kwargs) -> dict:
        config.SELECTED_TRANSLATION_COMPUTE_TYPE = str(data)
        model.setChangedTranslatorParameters(True)
        return {"status":200, "result":config.SELECTED_TRANSLATION_COMPUTE_TYPE}

    @staticmethod
    def getWhisperWeightType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.WHISPER_WEIGHT_TYPE}

    @staticmethod
    def setWhisperWeightType(data, *args, **kwargs) -> dict:
        config.WHISPER_WEIGHT_TYPE = str(data)
        return {"status":200, "result": config.WHISPER_WEIGHT_TYPE}

    @staticmethod
    def getWhisperDecodingProfile(*args, **kwargs) -> dict:
        return {"status": 200, "result": config.WHISPER_DECODING_PROFILE}

    def setWhisperDecodingProfile(self, data, *args, **kwargs) -> dict:
        config.WHISPER_DECODING_PROFILE = str(data).lower()
        self._requestCoordinatedTranscriptionRestart()
        return {"status": 200, "result": config.WHISPER_DECODING_PROFILE}

    @staticmethod
    def getVoskWeightType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.VOSK_WEIGHT_TYPE}

    def setVoskWeightType(self, data, *args, **kwargs) -> dict:
        config.VOSK_WEIGHT_TYPE = str(data)
        self._normalizeSelectedYourLanguageForTranscription()
        return {"status":200, "result": config.VOSK_WEIGHT_TYPE}

    @staticmethod
    def getParakeetWeightType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.PARAKEET_WEIGHT_TYPE}

    def setParakeetWeightType(self, data, *args, **kwargs) -> dict:
        config.PARAKEET_WEIGHT_TYPE = str(data)
        self._normalizeTranscriptionRuntimeSelection(notify=True)
        self._normalizeSelectedYourLanguageForTranscription()
        return {"status":200, "result": config.PARAKEET_WEIGHT_TYPE}

    @staticmethod
    def getSenseVoiceWeightType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SENSEVOICE_WEIGHT_TYPE}

    def setSenseVoiceWeightType(self, data, *args, **kwargs) -> dict:
        config.SENSEVOICE_WEIGHT_TYPE = str(data)
        self._normalizeTranscriptionRuntimeSelection(notify=True)
        self._normalizeSelectedYourLanguageForTranscription()
        return {"status":200, "result": config.SENSEVOICE_WEIGHT_TYPE}

    @staticmethod
    def getSelectedTranscriptionComputeType(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE}

    def setSelectedTranscriptionComputeType(self, data, *args, **kwargs) -> dict:
        config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE = str(data)
        self._normalizeTranscriptionRuntimeSelection(notify=True)
        return {"status":200, "result":config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE}

    @staticmethod
    def getSendMessageFormatParts(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SEND_MESSAGE_FORMAT_PARTS}

    @staticmethod
    def setSendMessageFormatParts(data, *args, **kwargs) -> dict:
        config.SEND_MESSAGE_FORMAT_PARTS = dict(data)
        return {"status":200, "result":config.SEND_MESSAGE_FORMAT_PARTS}

    @staticmethod
    def getReceivedMessageFormatParts(*args, **kwargs) -> dict:
        return {"status":200, "result":config.RECEIVED_MESSAGE_FORMAT_PARTS}

    @staticmethod
    def setReceivedMessageFormatParts(data, *args, **kwargs) -> dict:
        config.RECEIVED_MESSAGE_FORMAT_PARTS = dict(data)
        return {"status":200, "result":config.RECEIVED_MESSAGE_FORMAT_PARTS}

    @staticmethod
    def getAutoClearMessageBox(*args, **kwargs) -> dict:
        return {"status":200, "result":config.AUTO_CLEAR_MESSAGE_BOX}

    @staticmethod
    def setEnableAutoClearMessageBox(*args, **kwargs) -> dict:
        if config.AUTO_CLEAR_MESSAGE_BOX is False:
            config.AUTO_CLEAR_MESSAGE_BOX = True
        return {"status":200, "result":config.AUTO_CLEAR_MESSAGE_BOX}

    @staticmethod
    def setDisableAutoClearMessageBox(*args, **kwargs) -> dict:
        if config.AUTO_CLEAR_MESSAGE_BOX is True:
            config.AUTO_CLEAR_MESSAGE_BOX = False
        return {"status":200, "result":config.AUTO_CLEAR_MESSAGE_BOX}

    @staticmethod
    def getSendOnlyTranslatedMessages(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SEND_ONLY_TRANSLATED_MESSAGES}

    @staticmethod
    def setEnableSendOnlyTranslatedMessages(*args, **kwargs) -> dict:
        if config.SEND_ONLY_TRANSLATED_MESSAGES is False:
            config.SEND_ONLY_TRANSLATED_MESSAGES = True
        return {"status":200, "result":config.SEND_ONLY_TRANSLATED_MESSAGES}

    @staticmethod
    def setDisableSendOnlyTranslatedMessages(*args, **kwargs) -> dict:
        if config.SEND_ONLY_TRANSLATED_MESSAGES is True:
            config.SEND_ONLY_TRANSLATED_MESSAGES = False
        return {"status":200, "result":config.SEND_ONLY_TRANSLATED_MESSAGES}

    @staticmethod
    def getOverlaySmallLog(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OVERLAY_SMALL_LOG}

    @staticmethod
    def setEnableOverlaySmallLog(*args, **kwargs) -> dict:
        model.startOverlay()
        if config.OVERLAY_SMALL_LOG is False:
            config.OVERLAY_SMALL_LOG = True
        return {"status":200, "result":config.OVERLAY_SMALL_LOG}

    @staticmethod
    def setDisableOverlaySmallLog(*args, **kwargs) -> dict:
        if config.OVERLAY_SMALL_LOG is True:
            model.clearOverlayImageSmallLog()
            if config.OVERLAY_LARGE_LOG is False:
                model.shutdownOverlay()
            config.OVERLAY_SMALL_LOG = False
        return {"status":200, "result":config.OVERLAY_SMALL_LOG}

    @staticmethod
    def getOverlaySmallLogSettings(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OVERLAY_SMALL_LOG_SETTINGS}

    @staticmethod
    def setOverlaySmallLogSettings(data, *args, **kwargs) -> dict:
        config.OVERLAY_SMALL_LOG_SETTINGS = data
        model.updateOverlaySmallLogSettings()
        return {"status":200, "result":config.OVERLAY_SMALL_LOG_SETTINGS}

    @staticmethod
    def getOverlayLargeLog(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OVERLAY_LARGE_LOG}

    @staticmethod
    def setEnableOverlayLargeLog(*args, **kwargs) -> dict:
        model.startOverlay()
        if config.OVERLAY_LARGE_LOG is False:
            config.OVERLAY_LARGE_LOG = True
        return {"status":200, "result":config.OVERLAY_LARGE_LOG}

    @staticmethod
    def setDisableOverlayLargeLog(*args, **kwargs) -> dict:
        if config.OVERLAY_LARGE_LOG is True:
            model.clearOverlayImageLargeLog()
            if config.OVERLAY_SMALL_LOG is False:
                model.shutdownOverlay()
            config.OVERLAY_LARGE_LOG = False
        return {"status":200, "result":config.OVERLAY_LARGE_LOG}

    @staticmethod
    def getOverlayLargeLogSettings(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OVERLAY_LARGE_LOG_SETTINGS}

    @staticmethod
    def setOverlayLargeLogSettings(data, *args, **kwargs) -> dict:
        config.OVERLAY_LARGE_LOG_SETTINGS = data
        model.updateOverlayLargeLogSettings()
        return {"status":200, "result":config.OVERLAY_LARGE_LOG_SETTINGS}

    @staticmethod
    def getOverlayShowOnlyTranslatedMessages(*args, **kwargs) -> dict:
        return {"status":200, "result":config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES}

    @staticmethod
    def setEnableOverlayShowOnlyTranslatedMessages(*args, **kwargs) -> dict:
        if config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES is False:
            config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES = True
        return {"status":200, "result":config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES}

    @staticmethod
    def setDisableOverlayShowOnlyTranslatedMessages(*args, **kwargs) -> dict:
        if config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES is True:
            config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES = False
        return {"status":200, "result":config.OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES}

    @staticmethod
    def getSendMessageToVrc(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SEND_MESSAGE_TO_VRC}

    @staticmethod
    def setEnableSendMessageToVrc(*args, **kwargs) -> dict:
        if config.SEND_MESSAGE_TO_VRC is False:
            config.SEND_MESSAGE_TO_VRC = True
        return {"status":200, "result":config.SEND_MESSAGE_TO_VRC}

    @staticmethod
    def setDisableSendMessageToVrc(*args, **kwargs) -> dict:
        if config.SEND_MESSAGE_TO_VRC is True:
            config.SEND_MESSAGE_TO_VRC = False
        return {"status":200, "result":config.SEND_MESSAGE_TO_VRC}

    @staticmethod
    def getSendReceivedMessageToVrc(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SEND_RECEIVED_MESSAGE_TO_VRC}

    @staticmethod
    def setEnableSendReceivedMessageToVrc(*args, **kwargs) -> dict:
        if config.SEND_RECEIVED_MESSAGE_TO_VRC is False:
            config.SEND_RECEIVED_MESSAGE_TO_VRC = True
        return {"status":200, "result":config.SEND_RECEIVED_MESSAGE_TO_VRC}

    @staticmethod
    def setDisableSendReceivedMessageToVrc(*args, **kwargs) -> dict:
        if config.SEND_RECEIVED_MESSAGE_TO_VRC is True:
            config.SEND_RECEIVED_MESSAGE_TO_VRC = False
        return {"status":200, "result":config.SEND_RECEIVED_MESSAGE_TO_VRC}

    @staticmethod
    def getLoggerFeature(*args, **kwargs) -> dict:
        return {"status":200, "result":config.LOGGER_FEATURE}

    @staticmethod
    def setEnableLoggerFeature(*args, **kwargs) -> dict:
        if config.LOGGER_FEATURE is False:
            model.startLogger()
            config.LOGGER_FEATURE = True
        return {"status":200, "result":config.LOGGER_FEATURE}

    @staticmethod
    def setDisableLoggerFeature(*args, **kwargs) -> dict:
        if config.LOGGER_FEATURE is True:
            model.stopLogger()
            config.LOGGER_FEATURE = False
        return {"status":200, "result":config.LOGGER_FEATURE}

    @staticmethod
    def getVrcMicMuteSync(*args, **kwargs) -> dict:
        return {"status":200, "result":config.VRC_MIC_MUTE_SYNC}

    @staticmethod
    def setEnableVrcMicMuteSync(*args, **kwargs) -> dict:
        if config.VRC_MIC_MUTE_SYNC is False:
            if model.getIsOscQueryEnabled() is True:
                config.VRC_MIC_MUTE_SYNC = True
                model.setMuteSelfStatus()
                model.changeMicTranscriptStatus()
                response = {"status":200, "result":config.VRC_MIC_MUTE_SYNC}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.VRC_MIC_MUTE_SYNC_OSC_DISABLED,
                    data=config.VRC_MIC_MUTE_SYNC
                )
        else:
            response = {"status":200, "result":config.VRC_MIC_MUTE_SYNC}
        return response

    @staticmethod
    def setDisableVrcMicMuteSync(*args, **kwargs) -> dict:
        if config.VRC_MIC_MUTE_SYNC is True:
            config.VRC_MIC_MUTE_SYNC = False
            model.changeMicTranscriptStatus()
        return {"status":200, "result":config.VRC_MIC_MUTE_SYNC}

    def setEnableCheckSpeakerThreshold(self, *args, **kwargs) -> dict:
        if config.ENABLE_CHECK_ENERGY_RECEIVE is False:
            self.startThreadingCheckSpeakerEnergy()
            config.ENABLE_CHECK_ENERGY_RECEIVE = True
        return {"status":200, "result":config.ENABLE_CHECK_ENERGY_RECEIVE}

    def setDisableCheckSpeakerThreshold(self, *args, **kwargs) -> dict:
        if config.ENABLE_CHECK_ENERGY_RECEIVE is True:
            self.stopThreadingCheckSpeakerEnergy()
            config.ENABLE_CHECK_ENERGY_RECEIVE = False
        return {"status":200, "result":config.ENABLE_CHECK_ENERGY_RECEIVE}

    def setEnableCheckMicThreshold(self, *args, **kwargs) -> dict:
        if config.ENABLE_CHECK_ENERGY_SEND is False:
            self.startThreadingCheckMicEnergy()
            config.ENABLE_CHECK_ENERGY_SEND = True
        return {"status":200, "result":config.ENABLE_CHECK_ENERGY_SEND}

    def setDisableCheckMicThreshold(self, *args, **kwargs) -> dict:
        if config.ENABLE_CHECK_ENERGY_SEND is True:
            self.stopThreadingCheckMicEnergy()
            config.ENABLE_CHECK_ENERGY_SEND = False
        return {"status":200, "result":config.ENABLE_CHECK_ENERGY_SEND}

    @staticmethod
    def openFilepathLogs(*args, **kwargs) -> dict:
        Popen(['explorer', config.PATH_LOGS.replace('/', '\\')], shell=True)
        return {"status":200, "result":True}

    @staticmethod
    def openFilepathConfigFile(*args, **kwargs) -> dict:
        Popen(['explorer', config.PATH_DATA.replace('/', '\\')], shell=True)
        return {"status":200, "result":True}

    def setEnableTranscriptionSend(self, *args, **kwargs) -> dict:
        if config.ENABLE_TRANSCRIPTION_SEND is False:
            config.ENABLE_TRANSCRIPTION_SEND = True
            self.startThreadingTranscriptionSendMessage()
        return {"status":200, "result":config.ENABLE_TRANSCRIPTION_SEND}

    def setDisableTranscriptionSend(self, *args, **kwargs) -> dict:
        if config.ENABLE_TRANSCRIPTION_SEND is True:
            config.ENABLE_TRANSCRIPTION_SEND = False
            self.stopThreadingTranscriptionSendMessage()
        return {"status":200, "result":config.ENABLE_TRANSCRIPTION_SEND}

    def setEnableTranscriptionReceive(self, *args, **kwargs) -> dict:
        if config.ENABLE_TRANSCRIPTION_RECEIVE is False:
            config.ENABLE_TRANSCRIPTION_RECEIVE = True
            self.startThreadingTranscriptionReceiveMessage()
        return {"status":200, "result":config.ENABLE_TRANSCRIPTION_RECEIVE}

    def setDisableTranscriptionReceive(self, *args, **kwargs) -> dict:
        if config.ENABLE_TRANSCRIPTION_RECEIVE is True:
            config.ENABLE_TRANSCRIPTION_RECEIVE = False
            self.stopThreadingTranscriptionReceiveMessage()
        return {"status":200, "result":config.ENABLE_TRANSCRIPTION_RECEIVE}

    def sendMessageBox(self, data, *args, **kwargs) -> dict:
        response = self.chatMessage(data)
        return response

    @staticmethod
    def typingMessageBox(*args, **kwargs) -> dict:
        if config.SEND_MESSAGE_TO_VRC is True:
            model.oscStartSendTyping()
        return {"status":200, "result":True}

    @staticmethod
    def stopTypingMessageBox(*args, **kwargs) -> dict:
        if config.SEND_MESSAGE_TO_VRC is True:
            model.oscStopSendTyping()
        return {"status":200, "result":True}

    @staticmethod
    def sendTextOverlay(data, *args, **kwargs) -> dict:
        if config.OVERLAY_SMALL_LOG is True:
            overlay_image = model.createOverlayImageSmallMessage(data)
            model.updateOverlaySmallLog(overlay_image)

        if config.OVERLAY_LARGE_LOG is True:
            overlay_image = model.createOverlayImageLargeMessage(data)
            model.updateOverlayLargeLog(overlay_image)
        return {"status":200, "result":data}

    @staticmethod
    def getTelemetry(*args, **kwargs) -> dict:
        return {"status":200, "result":config.ENABLE_TELEMETRY}

    @staticmethod
    def setEnableTelemetry(*args, **kwargs) -> dict:
        if config.ENABLE_TELEMETRY is False:
            config.ENABLE_TELEMETRY = True
            model.telemetryInit(enabled=True, app_version=config.VERSION)
        return {"status":200, "result":config.ENABLE_TELEMETRY}

    @staticmethod
    def setDisableTelemetry(*args, **kwargs) -> dict:
        if config.ENABLE_TELEMETRY is True:
            config.ENABLE_TELEMETRY = False
            model.telemetryShutdown()
        return {"status":200, "result":config.ENABLE_TELEMETRY}

    def _restartActiveTranscription(self) -> None:
        """Restart any running transcription engines so they pick up new language settings.

        This is called after swapping Your Language and Target Language to ensure
        the transcription models are re-initialized with the updated config, matching
        the dynamic behavior of Google/Whisper engines.
        """
        needs_mic = config.ENABLE_TRANSCRIPTION_SEND
        needs_speaker = config.ENABLE_TRANSCRIPTION_RECEIVE

        if needs_mic:
            self.stopTranscriptionSendMessage()
        if needs_speaker:
            self.stopTranscriptionReceiveMessage()
        if needs_mic:
            self.startTranscriptionSendMessage()
        if needs_speaker:
            self.startTranscriptionReceiveMessage()

    def _requestCoordinatedTranscriptionRestart(self) -> None:
        self._restartActiveTranscription()

    def swapYourLanguageAndTargetLanguage(self, *args, **kwargs) -> dict:
        your_languages = config.SELECTED_YOUR_LANGUAGES
        your_language_temp = your_languages[config.SELECTED_TAB_NO]["1"]

        target_languages = config.SELECTED_TARGET_LANGUAGES
        target_language_temp = target_languages[config.SELECTED_TAB_NO]["1"]

        your_languages[config.SELECTED_TAB_NO]["1"] = target_language_temp
        target_languages[config.SELECTED_TAB_NO]["1"] = your_language_temp

        self.setSelectedYourLanguages(your_languages)
        self.setSelectedTargetLanguages(target_languages)

        # Restart active transcription engines so they re-initialize with the
        # swapped language settings (critical for Vosk/Parakeet which bind
        # language at model-init time, not per-call like Google/Whisper).
        th_restart = Thread(target=self._restartActiveTranscription)
        th_restart.daemon = True
        th_restart.start()

        return {
            "status":200,
            "result":{
                "your":config.SELECTED_YOUR_LANGUAGES,
                "your_translation":config.SELECTED_YOUR_TRANSLATION_LANGUAGES,
                "target":config.SELECTED_TARGET_LANGUAGES,
                }
            }

    def updateSoftware(self, *args, **kwargs) -> dict:
        th_start_update_software = Thread(target=model.updateSoftware)
        th_start_update_software.daemon = True
        th_start_update_software.start()
        return {"status":200, "result":True}

    def downloadCtranslate2Weight(self, data:str, asynchronous:bool=True, *args, **kwargs) -> dict:
        weight_type = str(data)
        download_ctranslate2 = self.DownloadCTranslate2(
            self.run_mapping,
            weight_type,
            self.run
            )

        if asynchronous is True:
            self.startThreadingDownloadCtranslate2Weight(
                weight_type,
                download_ctranslate2.progressBar,
                download_ctranslate2.downloaded,
                )
        else:
            if model.downloadCTranslate2ModelWeight(weight_type, download_ctranslate2.progressBar, None):
                model.downloadCTranslate2ModelTokenizer(weight_type)
            download_ctranslate2.downloaded()
        return {"status":200, "result":True}

    def downloadWhisperWeight(self, data:str, asynchronous:bool=True, *args, **kwargs) -> dict:
        weight_type = str(data)
        download_whisper = self.DownloadWhisper(
            self.run_mapping,
            weight_type,
            self.run
        )
        if asynchronous is True:
            self.startThreadingDownloadWhisperWeight(
                weight_type,
                download_whisper.progressBar,
                download_whisper.downloaded,
                )
        else:
            model.downloadWhisperModelWeight(weight_type, download_whisper.progressBar, download_whisper.downloaded)
        return {"status":200, "result":True}

    def downloadVoskWeight(self, data:str, asynchronous:bool=True, *args, **kwargs) -> dict:
        weight_type = str(data)
        dl = self.DownloadVosk(self.run_mapping, weight_type, self.run)
        if asynchronous is True:
            th = Thread(target=model.downloadVoskModelWeight, args=(weight_type, dl.progressBar, dl.downloaded))
            th.daemon = True
            th.start()
        else:
            model.downloadVoskModelWeight(weight_type, dl.progressBar, dl.downloaded)
        return {"status":200, "result":True}

    def downloadParakeetWeight(self, data:str, asynchronous:bool=True, *args, **kwargs) -> dict:
        weight_type = str(data)
        dl = self.DownloadParakeet(self.run_mapping, weight_type, self.run)
        if asynchronous is True:
            th = Thread(target=model.downloadParakeetModelWeight, args=(weight_type, dl.progressBar, dl.downloaded))
            th.daemon = True
            th.start()
        else:
            model.downloadParakeetModelWeight(weight_type, dl.progressBar, dl.downloaded)
        return {"status":200, "result":True}

    def downloadSenseVoiceWeight(self, data:str, asynchronous:bool=True, *args, **kwargs) -> dict:
        weight_type = str(data)
        dl = self.DownloadSenseVoice(self.run_mapping, weight_type, self.run)
        if asynchronous is True:
            th = Thread(target=model.downloadSenseVoiceModelWeight, args=(weight_type, dl.progressBar, dl.downloaded))
            th.daemon = True
            th.start()
        else:
            model.downloadSenseVoiceModelWeight(weight_type, dl.progressBar, dl.downloaded)
        return {"status":200, "result":True}

    @staticmethod
    def messageFormatter(format_type:str, translation:list, message:str) -> str:
        if format_type == "RECEIVED":
            format_parts = config.RECEIVED_MESSAGE_FORMAT_PARTS
        elif format_type == "SEND":
            format_parts = config.SEND_MESSAGE_FORMAT_PARTS
        else:
            raise ValueError("format_type is not found", format_type)

        message_part = format_parts["message"]["prefix"] + message + format_parts["message"]["suffix"]
        translation_part = format_parts["translation"]["prefix"] + format_parts["translation"]["separator"].join(translation) + format_parts["translation"]["suffix"]

        if len(translation) > 0 and message != "":
            # 翻訳とメッセージの順序を決定
            if format_parts["translation_first"]:
                osc_message = translation_part + format_parts["separator"] + message_part
            else:
                osc_message = message_part + format_parts["separator"] + translation_part
        elif len(translation) > 0 and message == "":
            osc_message = translation_part
        else:
            osc_message = message_part
        return osc_message

    def changeToCTranslate2Process(self) -> None:
        selected_engines = config.SELECTED_TRANSLATION_ENGINES[config.SELECTED_TAB_NO]
        for selected_engine in normalizeTranslationEngineSelection(selected_engines):
            config.SELECTABLE_TRANSLATION_ENGINE_STATUS[selected_engine] = False
        config.SELECTED_TRANSLATION_ENGINES[config.SELECTED_TAB_NO] = "CTranslate2"
        selectable_engines = self.getTranslationEngines()["result"]
        self.run(200, self.run_mapping["selected_translation_engines"], config.SELECTED_TRANSLATION_ENGINES)
        self.run(200, self.run_mapping["translation_engines"], selectable_engines)

    def startTranscriptionSendMessage(self) -> None:
        while self.device_access_status is False:
            sleep(1)
        self.device_access_status = False
        try:
            model.startMicTranscript(self.micMessage)
        except Exception as e:
            # VRAM不足エラーの検出
            is_vram_error, error_message = model.detectVRAMError(e)
            if is_vram_error:
                response = VRCTError.create_error_response(
                    ErrorCode.TRANSCRIPTION_VRAM_MIC,
                    data=error_message
                )
                self.run(
                    response["status"],
                    self.run_mapping["error_transcription_mic_vram_overflow"],
                    response["result"],
                )
                # ここでマイクの音声認識を停止
                self.stopTranscriptionSendMessage()
                config.ENABLE_TRANSCRIPTION_SEND = False
                disable_response = VRCTError.create_error_response(
                    ErrorCode.TRANSCRIPTION_SEND_DISABLED_VRAM,
                    data=False
                )
                self.run(
                    disable_response["status"],
                    self.run_mapping["enable_transcription_send"],
                    disable_response["result"],
                )
            else:
                # その他のエラーは通常通り処理
                errorLogging()
                config.ENABLE_TRANSCRIPTION_SEND = False
                self.run(200, self.run_mapping["enable_transcription_send"], False)
        finally:
            self.device_access_status = True

    @staticmethod
    def stopTranscriptionSendMessage() -> None:
        model.stopMicTranscript()

    def startThreadingTranscriptionSendMessage(self) -> None:
        th_startTranscriptionSendMessage = Thread(target=self.startTranscriptionSendMessage)
        th_startTranscriptionSendMessage.daemon = True
        th_startTranscriptionSendMessage.start()

    def stopThreadingTranscriptionSendMessage(self) -> None:
        th_stopTranscriptionSendMessage = Thread(target=self.stopTranscriptionSendMessage)
        th_stopTranscriptionSendMessage.daemon = True
        th_stopTranscriptionSendMessage.start()
        th_stopTranscriptionSendMessage.join()

    def startTranscriptionReceiveMessage(self) -> None:
        while self.device_access_status is False:
            sleep(1)
        self.device_access_status = False
        try:
            model.startSpeakerTranscript(self.speakerMessage)
        except Exception as e:
            # VRAM不足エラーの検出
            is_vram_error, error_message = model.detectVRAMError(e)
            if is_vram_error:
                response = VRCTError.create_error_response(
                    ErrorCode.TRANSCRIPTION_VRAM_SPEAKER,
                    data=error_message
                )
                self.run(
                    response["status"],
                    self.run_mapping["error_transcription_speaker_vram_overflow"],
                    response["result"],
                )
                # ここでスピーカーの音声認識を停止
                self.stopTranscriptionReceiveMessage()
                config.ENABLE_TRANSCRIPTION_RECEIVE = False
                disable_response = VRCTError.create_error_response(
                    ErrorCode.TRANSCRIPTION_RECEIVE_DISABLED_VRAM,
                    data=False
                )
                self.run(
                    disable_response["status"],
                    self.run_mapping["enable_transcription_receive"],
                    disable_response["result"],
                )
            else:
                # その他のエラーは通常通り処理
                errorLogging()
                config.ENABLE_TRANSCRIPTION_RECEIVE = False
                self.run(200, self.run_mapping["enable_transcription_receive"], False)
        finally:
            self.device_access_status = True

    @staticmethod
    def stopTranscriptionReceiveMessage() -> None:
        model.stopSpeakerTranscript()

    def startThreadingTranscriptionReceiveMessage(self) -> None:
        th_startTranscriptionReceiveMessage = Thread(target=self.startTranscriptionReceiveMessage)
        th_startTranscriptionReceiveMessage.daemon = True
        th_startTranscriptionReceiveMessage.start()

    def stopThreadingTranscriptionReceiveMessage(self) -> None:
        th_stopTranscriptionReceiveMessage = Thread(target=self.stopTranscriptionReceiveMessage)
        th_stopTranscriptionReceiveMessage.daemon = True
        th_stopTranscriptionReceiveMessage.start()
        th_stopTranscriptionReceiveMessage.join()

    @staticmethod
    def replaceExclamationsWithRandom(text):
        # ![...] にマッチする正規表現
        pattern = r'!\[(.*?)\]'

        # 乱数と置換部分を保存する辞書
        replacement_dict = {}

        num = 4096
        # マッチした部分を4096から始まる整数に置換する。置換毎に4097, 4098, ... と増える
        def replace(match):
            original = match.group(1)
            nonlocal num
            rand_value = hex(num)
            replacement_dict[rand_value] = original
            num += 1
            return f" ${rand_value} "

        # 文章内の ![] の部分を置換
        replaced_text = re.sub(pattern, replace, text)

        return replaced_text, replacement_dict

    @staticmethod
    def restoreText(escaped_text, escape_dict):
        # 大文字小文字を無視して置換するために、正規表現を使う
        for escape_seq, char in escape_dict.items():
            # escaped_text の部分を pattern で置換
            pattern = re.escape(f"${escape_seq}") + r"|\$\s+" + re.escape(escape_seq)
            escaped_text = re.sub(pattern, char, escaped_text, flags=re.IGNORECASE)
        return escaped_text

    @staticmethod
    def removeExclamations(text):
        # ![...] を [...] に置換する正規表現
        pattern = r'!\[(.*?)\]'
        # ![...] の部分を [] 内のテキストに置換
        cleaned_text = re.sub(pattern, r'\1', text)
        return cleaned_text

    def updateDownloadedCTranslate2ModelWeight(self, scan_all: bool = False) -> None:
        # キャッシュされた結果を使用（起動時の重複チェックを回避）
        if hasattr(self, '_ctranslate2_available_cache'):
            # 起動時のキャッシュを使用: 選択中の重みタイプのみ設定
            config.SELECTABLE_CTRANSLATE2_WEIGHT_TYPE_DICT[config.CTRANSLATE2_WEIGHT_TYPE] = self._ctranslate2_available_cache

        if scan_all is False:
            return

        # すべての重みタイプをチェック（キャッシュされていないものだけ）
        for weight_type in config.SELECTABLE_CTRANSLATE2_WEIGHT_TYPE_DICT.keys():
            # 選択中のウェイトはキャッシュで設定済みなのでスキップ
            if hasattr(self, '_ctranslate2_available_cache') and weight_type == config.CTRANSLATE2_WEIGHT_TYPE:
                continue
            config.SELECTABLE_CTRANSLATE2_WEIGHT_TYPE_DICT[weight_type] = model.checkTranslatorCTranslate2ModelWeight(weight_type)

    def updateTranslationEngineAndEngineList(self):
        engines = config.SELECTED_TRANSLATION_ENGINES
        selected_engines = normalizeTranslationEngineSelection(engines[config.SELECTED_TAB_NO])
        selectable_engines = self.getTranslationEngines()["result"]
        selected_engines = [engine for engine in selected_engines if engine in selectable_engines]
        if len(selected_engines) == 0:
            selected_engines = ["CTranslate2"]
        engines[config.SELECTED_TAB_NO] = collapseTranslationEngineSelection(selected_engines)
        config.SELECTED_TRANSLATION_ENGINES = engines

        self.run(200, self.run_mapping["selected_translation_engines"], config.SELECTED_TRANSLATION_ENGINES)
        self.run(200, self.run_mapping["translation_engines"], selectable_engines)

    def updateDownloadedWhisperModelWeight(self, scan_all: bool = False) -> None:
        # キャッシュされた結果を使用（起動時の重複チェックを回避）
        if hasattr(self, '_whisper_available_cache'):
            # 起動時のキャッシュを使用: 起動に必要な最小ウェイトのみ設定
            cached_weight_type = getattr(self, '_whisper_available_cache_key', config.WHISPER_WEIGHT_TYPE)
            config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT[cached_weight_type] = self._whisper_available_cache

            selected_weight_type = config.WHISPER_WEIGHT_TYPE
            if selected_weight_type != cached_weight_type:
                config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT[selected_weight_type] = model.checkTranscriptionWhisperModelWeight(selected_weight_type)

        if scan_all is False:
            return

        # すべての重みタイプをチェック（キャッシュされていないものだけ）
        for weight_type in config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT.keys():
            # 起動時に確認済みのウェイトはキャッシュで設定済みなのでスキップ
            if hasattr(self, '_whisper_available_cache') and weight_type == getattr(self, '_whisper_available_cache_key', config.WHISPER_WEIGHT_TYPE):
                continue
            config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT[weight_type] = model.checkTranscriptionWhisperModelWeight(weight_type)

    def updateDownloadedVoskModelWeight(self, scan_all: bool = False) -> None:
        selected_weight_type = config.VOSK_WEIGHT_TYPE
        config.SELECTABLE_VOSK_WEIGHT_TYPE_DICT[selected_weight_type] = model.checkTranscriptionVoskModelWeight(selected_weight_type)
        if scan_all is False:
            return
        for weight_type in config.SELECTABLE_VOSK_WEIGHT_TYPE_DICT.keys():
            if weight_type == selected_weight_type:
                continue
            config.SELECTABLE_VOSK_WEIGHT_TYPE_DICT[weight_type] = model.checkTranscriptionVoskModelWeight(weight_type)

    def updateDownloadedParakeetModelWeight(self, scan_all: bool = False) -> None:
        selected_weight_type = config.PARAKEET_WEIGHT_TYPE
        config.SELECTABLE_PARAKEET_WEIGHT_TYPE_DICT[selected_weight_type] = model.checkTranscriptionParakeetModelWeight(selected_weight_type)
        if scan_all is False:
            return
        for weight_type in config.SELECTABLE_PARAKEET_WEIGHT_TYPE_DICT.keys():
            if weight_type == selected_weight_type:
                continue
            config.SELECTABLE_PARAKEET_WEIGHT_TYPE_DICT[weight_type] = model.checkTranscriptionParakeetModelWeight(weight_type)

    def updateDownloadedSenseVoiceModelWeight(self, scan_all: bool = False) -> None:
        selected_weight_type = config.SENSEVOICE_WEIGHT_TYPE
        config.SELECTABLE_SENSEVOICE_WEIGHT_TYPE_DICT[selected_weight_type] = model.checkTranscriptionSenseVoiceModelWeight(selected_weight_type)
        if scan_all is False:
            return
        for weight_type in config.SELECTABLE_SENSEVOICE_WEIGHT_TYPE_DICT.keys():
            if weight_type == selected_weight_type:
                continue
            config.SELECTABLE_SENSEVOICE_WEIGHT_TYPE_DICT[weight_type] = model.checkTranscriptionSenseVoiceModelWeight(weight_type)

    def updateTranscriptionEngine(self):
        weight_type = config.WHISPER_WEIGHT_TYPE
        weight_type_dict = config.SELECTABLE_WHISPER_WEIGHT_TYPE_DICT
        weight_available = bool(weight_type_dict.get(weight_type))
        current_engine = config.SELECTED_TRANSCRIPTION_ENGINE
        selected_engines = [key for key, value in config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS.items() if value is True]

        if current_engine in selected_engines:
            self._normalizeSelectedYourLanguageForTranscription()
            return

        if weight_available and "Whisper" in selected_engines:
            config.SELECTED_TRANSCRIPTION_ENGINE = "Whisper"
        elif "Google" in selected_engines:
            config.SELECTED_TRANSCRIPTION_ENGINE = "Google"
        elif selected_engines:
            config.SELECTED_TRANSCRIPTION_ENGINE = selected_engines[0]
        else:
            config.SELECTED_TRANSCRIPTION_ENGINE = "Whisper"
        self._normalizeSelectedYourLanguageForTranscription()

    def startCheckMicEnergy(self) -> None:
        while self.device_access_status is False:
            sleep(1)
        self.device_access_status = False
        model.startCheckMicEnergy(self.progressBarMicEnergy)
        self.device_access_status = True

    def startThreadingCheckMicEnergy(self) -> None:
        th_startCheckMicEnergy = Thread(target=self.startCheckMicEnergy)
        th_startCheckMicEnergy.daemon = True
        th_startCheckMicEnergy.start()

    def stopCheckMicEnergy(self) -> None:
        model.stopCheckMicEnergy()

    def stopThreadingCheckMicEnergy(self) -> None:
        th_stopCheckMicEnergy = Thread(target=self.stopCheckMicEnergy)
        th_stopCheckMicEnergy.daemon = True
        th_stopCheckMicEnergy.start()
        th_stopCheckMicEnergy.join()

    def startCheckSpeakerEnergy(self) -> None:
        while self.device_access_status is False:
            sleep(1)
        self.device_access_status = False
        model.startCheckSpeakerEnergy(self.progressBarSpeakerEnergy)
        self.device_access_status = True

    def startThreadingCheckSpeakerEnergy(self) -> None:
        th_startCheckSpeakerEnergy = Thread(target=self.startCheckSpeakerEnergy)
        th_startCheckSpeakerEnergy.daemon = True
        th_startCheckSpeakerEnergy.start()

    def stopCheckSpeakerEnergy(self) -> None:
        model.stopCheckSpeakerEnergy()

    def stopThreadingCheckSpeakerEnergy(self) -> None:
        th_stopCheckSpeakerEnergy = Thread(target=self.stopCheckSpeakerEnergy)
        th_stopCheckSpeakerEnergy.daemon = True
        th_stopCheckSpeakerEnergy.start()
        th_stopCheckSpeakerEnergy.join()

    @staticmethod
    def startThreadingDownloadCtranslate2Weight(weight_type:str, callback:Callable[[float], None], end_callback:Optional[Callable[..., None]] = None) -> None:
        def run_download():
            if model.downloadCTranslate2ModelWeight(weight_type, callback, None):
                model.downloadCTranslate2ModelTokenizer(weight_type)
            if end_callback is not None:
                end_callback()

        th_download = Thread(target=run_download)
        th_download.daemon = True
        th_download.start()

    @staticmethod
    def startThreadingDownloadWhisperWeight(weight_type:str, callback:Callable[[float], None], end_callback:Optional[Callable[..., None]] = None) -> None:
        th_download = Thread(target=model.downloadWhisperModelWeight, args=(weight_type, callback, end_callback))
        th_download.daemon = True
        th_download.start()

    @staticmethod
    def startWatchdog(*args, **kwargs) -> dict:
        model.startWatchdog()
        return {"status":200, "result":True}

    @staticmethod
    def feedWatchdog(*args, **kwargs) -> dict:
        model.feedWatchdog()
        return {"status":200, "result":True}

    @staticmethod
    def setWatchdogCallback(callback) -> dict:
        model.setWatchdogCallback(callback)
        return {"status":200, "result":True}

    @staticmethod
    def stopWatchdog(*args, **kwargs) -> dict:
        model.stopWatchdog()
        return {"status":200, "result":True}

    @staticmethod
    def getWebSocketHost(*args, **kwargs) -> dict:
        return {"status":200, "result":config.WEBSOCKET_HOST}

    @staticmethod
    def setWebSocketHost(data, *args, **kwargs) -> dict:
        if isValidIpAddress(data) is False:
            response = VRCTError.create_error_response(
                ErrorCode.VALIDATION_INVALID_IP,
                data=config.WEBSOCKET_HOST
            )
        else:
            if model.checkWebSocketServerAlive() is False:
                config.WEBSOCKET_HOST = data
                response = {"status":200, "result":config.WEBSOCKET_HOST}
            else:
                if data == config.WEBSOCKET_HOST:
                    response = {"status":200, "result":config.WEBSOCKET_HOST}
                elif isAvailableWebSocketServer(data, config.WEBSOCKET_PORT):
                    model.stopWebSocketServer()
                    model.startWebSocketServer(data, config.WEBSOCKET_PORT)
                    config.WEBSOCKET_HOST = data
                    response = {"status":200, "result":config.WEBSOCKET_HOST}
                else:
                    response = VRCTError.create_error_response(
                        ErrorCode.WEBSOCKET_HOST_UNAVAILABLE,
                        data=config.WEBSOCKET_HOST
                    )

        return response

    @staticmethod
    def getWebSocketPort(*args, **kwargs) -> dict:
        return {"status":200, "result":config.WEBSOCKET_PORT}

    @staticmethod
    def setWebSocketPort(data, *args, **kwargs) -> dict:
        if model.checkWebSocketServerAlive() is False:
            config.WEBSOCKET_PORT = int(data)
            response = {"status":200, "result":config.WEBSOCKET_PORT}
        else:
            if int(data) == config.WEBSOCKET_PORT:
                return {"status":200, "result":config.WEBSOCKET_PORT}
            elif isAvailableWebSocketServer(config.WEBSOCKET_HOST, int(data)) is True:
                model.stopWebSocketServer()
                model.startWebSocketServer(config.WEBSOCKET_HOST, int(data))
                config.WEBSOCKET_PORT = int(data)
                response = {"status":200, "result":config.WEBSOCKET_PORT}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.WEBSOCKET_PORT_UNAVAILABLE,
                    data=config.WEBSOCKET_PORT
                )
        return response

    @staticmethod
    def getWebSocketServer(*args, **kwargs) -> dict:
        return {"status":200, "result":config.WEBSOCKET_SERVER}

    @staticmethod
    def setEnableWebSocketServer(*args, **kwargs) -> dict:
        if config.WEBSOCKET_SERVER is False:
            if isAvailableWebSocketServer(config.WEBSOCKET_HOST, config.WEBSOCKET_PORT) is True:
                model.startWebSocketServer(config.WEBSOCKET_HOST, config.WEBSOCKET_PORT)
                config.WEBSOCKET_SERVER = True
                response = {"status":200, "result":config.WEBSOCKET_SERVER}
            else:
                response = VRCTError.create_error_response(
                    ErrorCode.WEBSOCKET_SERVER_UNAVAILABLE,
                    data=config.WEBSOCKET_SERVER
                )
        else:
            response = {"status":200, "result":config.WEBSOCKET_SERVER}
        return response

    @staticmethod
    def setDisableWebSocketServer(*args, **kwargs) -> dict:
        if config.WEBSOCKET_SERVER is True:
            config.WEBSOCKET_SERVER = False
            model.stopWebSocketServer()
        return {"status":200, "result":config.WEBSOCKET_SERVER}

    # Clipboard control
    @staticmethod
    def getClipboard(*args, **kwargs) -> dict:
        return {"status":200, "result":config.ENABLE_CLIPBOARD}

    @staticmethod
    def setEnableClipboard(*args, **kwargs) -> dict:
        if config.ENABLE_CLIPBOARD is False:
            config.ENABLE_CLIPBOARD = True
        return {"status":200, "result":config.ENABLE_CLIPBOARD}

    @staticmethod
    def setDisableClipboard(*args, **kwargs) -> dict:
        if config.ENABLE_CLIPBOARD is True:
            config.ENABLE_CLIPBOARD = False
        return {"status":200, "result":config.ENABLE_CLIPBOARD}

    def initializationProgress(self, progress):
        self.run(200, self.run_mapping["initialization_progress"], progress)

    def initializationStatus(self, message: str, detail: str = "", visible: bool = True, phase: str = "starting"):
        self.run(
            200,
            self.run_mapping["initialization_status"],
            {
                "message": message,
                "detail": detail,
                "visible": visible,
                "phase": phase,
            },
        )

    def _applyFastStartupTranslationStatus(self, connected_network: bool, ctranslate2_available: bool) -> None:
        online_engines = {"Google", "Bing", "Papago", "DeepL"}
        for engine in config.SELECTABLE_TRANSLATION_ENGINE_STATUS.keys():
            if engine == "CTranslate2":
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[engine] = ctranslate2_available
            elif engine in online_engines:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[engine] = connected_network
            else:
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[engine] = False

    def _applyFastStartupTranscriptionStatus(self, connected_network: bool, whisper_available: bool) -> None:
        for engine in config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS.keys():
            match engine:
                case "Whisper":
                    config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS[engine] = whisper_available
                case "Vosk":
                    config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS[engine] = any(
                        model.checkTranscriptionVoskModelWeight(wt)
                        for wt in config.SELECTABLE_VOSK_WEIGHT_TYPE_DICT.keys()
                    )
                case "Parakeet":
                    config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS[engine] = any(
                        model.checkTranscriptionParakeetModelWeight(wt)
                        for wt in config.SELECTABLE_PARAKEET_WEIGHT_TYPE_DICT.keys()
                    )
                case "SenseVoice":
                    config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS[engine] = any(
                        model.checkTranscriptionSenseVoiceModelWeight(wt)
                        for wt in config.SELECTABLE_SENSEVOICE_WEIGHT_TYPE_DICT.keys()
                    )
                case _:
                    config.SELECTABLE_TRANSCRIPTION_ENGINE_STATUS[engine] = connected_network

    def _finishInitializationInBackground(self, connected_network: bool) -> None:
        try:
            self.initializationStatus(
                "Loading devices and local services",
                "Refreshing audio devices and optional local providers.",
                visible=True,
                phase="services",
            )
            self.sendDeferredConfigSettings()

            self.initializationStatus(
                "Checking translation services",
                "Verifying online engines and optional local providers.",
                visible=True,
                phase="services",
            )

            ctranslate2_available = getattr(self, "_ctranslate2_available_cache", False)
            engines_to_check = list(config.SELECTABLE_TRANSLATION_ENGINE_LIST)
            engine_results = {}

            def check_translation_engine(engine: str) -> tuple:
                status = False
                auth_key_invalid = False
                model_list = None
                selected_model = None

                try:
                    match engine:
                        case "CTranslate2":
                            status = ctranslate2_available
                        case "DeepL_API":
                            if config.AUTH_KEYS[engine] is None:
                                status = False
                            else:
                                if model.authenticationTranslatorDeepLAuthKey(auth_key=config.AUTH_KEYS[engine]) is True:
                                    status = True
                                else:
                                    auth_key_invalid = True
                        case "Plamo_API":
                            if config.AUTH_KEYS[engine] is None:
                                status = False
                            else:
                                if model.authenticationTranslatorPlamoAuthKey(auth_key=config.AUTH_KEYS[engine]) is True:
                                    model_list = model.getTranslatorPlamoModelList()
                                    selected_model = config.SELECTED_PLAMO_MODEL if config.SELECTED_PLAMO_MODEL in model_list else model_list[0]
                                    status = True
                                else:
                                    auth_key_invalid = True
                        case "Gemini_API":
                            if config.AUTH_KEYS[engine] is None:
                                status = False
                            else:
                                if model.authenticationTranslatorGeminiAuthKey(auth_key=config.AUTH_KEYS[engine]) is True:
                                    model_list = model.getTranslatorGeminiModelList()
                                    selected_model = config.SELECTED_GEMINI_MODEL if config.SELECTED_GEMINI_MODEL in model_list else model_list[0]
                                    status = True
                                else:
                                    auth_key_invalid = True
                        case "OpenAI_API":
                            if config.AUTH_KEYS[engine] is None:
                                status = False
                            else:
                                if model.authenticationTranslatorOpenAIAuthKey(auth_key=config.AUTH_KEYS[engine]) is True:
                                    model_list = model.getTranslatorOpenAIModelList()
                                    selected_model = config.SELECTED_OPENAI_MODEL if config.SELECTED_OPENAI_MODEL in model_list else model_list[0]
                                    status = True
                                else:
                                    auth_key_invalid = True
                        case "Groq_API":
                            if config.AUTH_KEYS[engine] is None:
                                status = False
                            else:
                                if model.authenticationTranslatorGroqAuthKey(auth_key=config.AUTH_KEYS[engine]) is True:
                                    model_list = model.getTranslatorGroqModelList()
                                    selected_model = config.SELECTED_GROQ_MODEL if config.SELECTED_GROQ_MODEL in model_list else model_list[0]
                                    status = True
                                else:
                                    auth_key_invalid = True
                        case "OpenRouter_API":
                            if config.AUTH_KEYS[engine] is None:
                                status = False
                            else:
                                if model.authenticationTranslatorOpenRouterAuthKey(auth_key=config.AUTH_KEYS[engine]) is True:
                                    model_list = model.getTranslatorOpenRouterModelList()
                                    selected_model = config.SELECTED_OPENROUTER_MODEL if config.SELECTED_OPENROUTER_MODEL in model_list else model_list[0]
                                    status = True
                                else:
                                    auth_key_invalid = True
                        case "LMStudio":
                            if config.LMSTUDIO_URL is not None:
                                if model.authenticationTranslatorLMStudio(base_url=config.LMSTUDIO_URL) is True:
                                    model_list = model.getTranslatorLMStudioModelList()
                                    if len(model_list) > 0:
                                        selected_model = config.SELECTED_LMSTUDIO_MODEL if config.SELECTED_LMSTUDIO_MODEL in model_list else model_list[0]
                                        status = True
                        case "Ollama":
                            if model.authenticationTranslatorOllama() is True:
                                model_list = model.getTranslatorOllamaModelList()
                                if len(model_list) > 0:
                                    selected_model = config.SELECTED_OLLAMA_MODEL if config.SELECTED_OLLAMA_MODEL in model_list else model_list[0]
                                    status = True
                        case _:
                            status = connected_network is True
                except Exception as e:
                    printLog(f"Error checking engine {engine}: {str(e)}")
                    errorLogging()
                    status = False

                return engine, status, auth_key_invalid, model_list, selected_model

            with ThreadPoolExecutor(max_workers=4) as executor:
                future_to_engine = {executor.submit(check_translation_engine, engine): engine for engine in engines_to_check}
                for future in as_completed(future_to_engine):
                    engine, status, auth_key_invalid, model_list, selected_model = future.result()
                    engine_results[engine] = (status, auth_key_invalid, model_list, selected_model)

            for engine in engines_to_check:
                if engine not in engine_results:
                    continue

                status, auth_key_invalid, model_list, selected_model = engine_results[engine]
                config.SELECTABLE_TRANSLATION_ENGINE_STATUS[engine] = status

                if auth_key_invalid:
                    auth_keys = config.AUTH_KEYS
                    auth_keys[engine] = None
                    config.AUTH_KEYS = auth_keys
                    printLog(f"{engine} auth key is invalid")

                if engine == "LMStudio" and not status:
                    config.SELECTABLE_LMSTUDIO_MODEL_LIST = []
                    config.SELECTED_LMSTUDIO_MODEL = None
                if engine == "Ollama" and not status:
                    config.SELECTABLE_OLLAMA_MODEL_LIST = []
                    config.SELECTED_OLLAMA_MODEL = None

                if model_list is not None and status:
                    match engine:
                        case "Plamo_API":
                            config.SELECTABLE_PLAMO_MODEL_LIST = model_list
                            config.SELECTED_PLAMO_MODEL = selected_model
                            model.setTranslatorPlamoModel(selected_model)
                            model.updateTranslatorPlamoClient()
                        case "Gemini_API":
                            config.SELECTABLE_GEMINI_MODEL_LIST = model_list
                            config.SELECTED_GEMINI_MODEL = selected_model
                            model.setTranslatorGeminiModel(selected_model)
                            model.updateTranslatorGeminiClient()
                        case "OpenAI_API":
                            config.SELECTABLE_OPENAI_MODEL_LIST = model_list
                            config.SELECTED_OPENAI_MODEL = selected_model
                            model.setTranslatorOpenAIModel(selected_model)
                            model.updateTranslatorOpenAIClient()
                        case "Groq_API":
                            config.SELECTABLE_GROQ_MODEL_LIST = model_list
                            config.SELECTED_GROQ_MODEL = selected_model
                            model.setTranslatorGroqModel(selected_model)
                            model.updateTranslatorGroqClient()
                        case "OpenRouter_API":
                            config.SELECTABLE_OPENROUTER_MODEL_LIST = model_list
                            config.SELECTED_OPENROUTER_MODEL = selected_model
                            model.setTranslatorOpenRouterModel(selected_model)
                            model.updateTranslatorOpenRouterClient()
                        case "LMStudio":
                            config.SELECTABLE_LMSTUDIO_MODEL_LIST = model_list
                            config.SELECTED_LMSTUDIO_MODEL = selected_model
                            model.setTranslatorLMStudioModel(selected_model)
                            model.updateTranslatorLMStudioClient()
                        case "Ollama":
                            config.SELECTABLE_OLLAMA_MODEL_LIST = model_list
                            config.SELECTED_OLLAMA_MODEL = selected_model
                            model.setTranslatorOllamaModel(selected_model)
                            model.updateTranslatorOllamaClient()

            self.updateTranslationEngineAndEngineList()

            self.initializationStatus(
                "Starting background services",
                "Bringing up transliteration, OSC, and overlay.",
                visible=True,
                phase="services",
            )
            self.initializationProgress(4)

            if config.CONVERT_MESSAGE_TO_ROMAJI is True or config.CONVERT_MESSAGE_TO_HIRAGANA is True:
                model.startTransliteration()

            model.addKeywords()

            if config.LOGGER_FEATURE is True:
                model.startLogger()

            def init_osc_receive_background():
                try:
                    model.startReceiveOSC()
                    osc_query_enabled = model.getIsOscQueryEnabled()
                    if osc_query_enabled is True:
                        self.enableOscQuery()
                        if config.VRC_MIC_MUTE_SYNC is True:
                            self.setEnableVrcMicMuteSync()
                    else:
                        mute_sync_info_flag = False
                        if config.VRC_MIC_MUTE_SYNC is True:
                            self.setDisableVrcMicMuteSync()
                            mute_sync_info_flag = True
                        self.disableOscQuery(mute_sync_info=mute_sync_info_flag)
                    printLog("[Background] OSC Receive initialization completed")
                except Exception:
                    errorLogging()
                    printLog("[Background] OSC Receive initialization failed")

            bg_thread = Thread(target=init_osc_receive_background)
            bg_thread.daemon = True
            bg_thread.start()

            device_manager.setCallbackHostList(self.updateMicHostList)
            device_manager.setCallbackMicDeviceList(self.updateMicDeviceList)
            device_manager.setCallbackSpeakerDeviceList(self.updateSpeakerDeviceList)

            if config.AUTO_MIC_SELECT is True:
                self.applyAutoMicSelect()
            if config.AUTO_SPEAKER_SELECT is True:
                self.applyAutoSpeakerSelect()

            if (config.OVERLAY_SMALL_LOG is True or config.OVERLAY_LARGE_LOG is True):
                model.startOverlay()

            if config.WEBSOCKET_SERVER is True:
                if isAvailableWebSocketServer(config.WEBSOCKET_HOST, config.WEBSOCKET_PORT) is True:
                    model.startWebSocketServer(config.WEBSOCKET_HOST, config.WEBSOCKET_PORT)
                else:
                    config.WEBSOCKET_SERVER = False
                    model.stopWebSocketServer()
                    printLog("WebSocket server host or port is not available")

            config.revalidate_selected_models()

            if config.ENABLE_TELEMETRY is True:
                model.telemetryInit(enabled=config.ENABLE_TELEMETRY, app_version=config.VERSION)

            if connected_network is True:
                self.checkSoftwareUpdated()

            self.updateConfigSettings()
            self.initializationStatus("", "", visible=False, phase="done")
            self.startWatchdog()
        except Exception:
            errorLogging()
            self.initializationStatus(
                "Startup hit a background error",
                "Some services may need another second or a restart.",
                visible=True,
                phase="error",
            )

    def enableOscQuery(self):
        self.run(
            200,
            self.run_mapping["enable_osc_query"],
            {
                "data": True,
                "disabled_functions": []
            }
        )

    def disableOscQuery(self, mute_sync_info:bool=False):
        disabled_functions = []
        if mute_sync_info is True:
            disabled_functions.append("vrc_mic_mute_sync")
        self.run(200, self.run_mapping["enable_osc_query"], {
            "data": False,
            "disabled_functions": disabled_functions
        })

    def init(self, *args, **kwargs) -> None:
        removeLog()
        printLog("Start Initialization")
        self.initializationStatus("Starting VRCNT-Next", "Preparing the core app and local settings.", visible=True, phase="starting")

        # Network check
        connected_network = isConnectedNetwork()
        if connected_network is True:
            self.connectedNetwork()
        else:
            self.disconnectedNetwork()
        printLog(f"Connected Network: {connected_network}")
        self.initializationStatus(
            "Checking local environment",
            "Detecting connectivity, local models, and startup defaults.",
            visible=True,
            phase="local",
        )

        self.initializationProgress(1)

        # Download weights
        startup_whisper_weight_type = self._startupWhisperWeightType()
        if connected_network is True:
            printLog("Download CTranslate2 Model Weight")
            # 後方互換用
            model.backwardCompatibleTranslatorCTranslate2ModelRenameWeightsDir()

            download_threads = []
            weight_type = config.CTRANSLATE2_WEIGHT_TYPE
            if (
                model.checkTranslatorCTranslate2ModelWeight(weight_type) is False
                or model.checkTranslatorCTranslate2ModelTokenizer(weight_type) is False
            ):
                th_download_ctranslate2 = Thread(target=self.downloadCtranslate2Weight, args=(weight_type, False))
                th_download_ctranslate2.daemon = True
                th_download_ctranslate2.start()
                download_threads.append(th_download_ctranslate2)

            printLog("Download Whisper Model Weight")
            weight_type = startup_whisper_weight_type
            if model.checkTranscriptionWhisperModelWeight(weight_type) is False:
                th_download_whisper = Thread(target=self.downloadWhisperWeight, args=(weight_type, False))
                th_download_whisper.daemon = True
                th_download_whisper.start()
                download_threads.append(th_download_whisper)

            if len(download_threads) > 0:
                self.initializationStatus(
                    "Downloading required AI models",
                    "Preparing the selected local translation and Whisper models.",
                    visible=True,
                    phase="download",
                )
                for download_thread in download_threads:
                    download_thread.join()

        # Check and disable/enable AI models (parallel)

        def check_ctranslate2() -> bool:
            return (
                model.checkTranslatorCTranslate2ModelWeight(config.CTRANSLATE2_WEIGHT_TYPE) is True
                and model.checkTranslatorCTranslate2ModelTokenizer(config.CTRANSLATE2_WEIGHT_TYPE) is True
            )

        def check_whisper() -> bool:
            return model.checkTranscriptionWhisperModelWeight(startup_whisper_weight_type) is True

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_ctranslate2 = executor.submit(check_ctranslate2)
            future_whisper = executor.submit(check_whisper)
            ctranslate2_available = future_ctranslate2.result()
            whisper_available = future_whisper.result()

        # インスタンス変数にキャッシュ（後続の処理で再利用）
        self._ctranslate2_available_cache = ctranslate2_available
        self._whisper_available_cache_key = startup_whisper_weight_type
        self._whisper_available_cache = whisper_available
        self._fallbackSelectedWhisperWeight(startup_whisper_weight_type, whisper_available)

        if not ctranslate2_available or not whisper_available:
            self.disableAiModels()
        else:
            self.enableAiModels()

        self._applyFastStartupTranslationStatus(connected_network, ctranslate2_available)
        self._applyFastStartupTranscriptionStatus(connected_network, whisper_available)

        self.updateDownloadedCTranslate2ModelWeight()
        self.updateDownloadedWhisperModelWeight()
        self.updateDownloadedVoskModelWeight()
        self.updateDownloadedParakeetModelWeight()
        self.updateDownloadedSenseVoiceModelWeight()
        self.updateTranslationEngineAndEngineList()
        self.updateTranscriptionEngine()
        device_manager.setCallbackHostList(self.updateMicHostList)
        device_manager.setCallbackMicDeviceList(self.updateMicDeviceList)
        device_manager.setCallbackSpeakerDeviceList(self.updateSpeakerDeviceList)

        if config.AUTO_MIC_SELECT is True:
            self.applyAutoMicSelect()
        if config.AUTO_SPEAKER_SELECT is True:
            self.applyAutoSpeakerSelect()

        self.initializationProgress(2)
        self.initializationStatus(
            "Opening interface",
            "The main window is ready. Finishing optional startup tasks in the background.",
            visible=True,
            phase="readying_ui",
        )
        self.updateConfigSettings()
        self.initializationProgress(3)

        bg_thread = Thread(target=self._finishInitializationInBackground, args=(connected_network,))
        bg_thread.daemon = True
        bg_thread.start()

        printLog("End Initialization (core ready, background tasks running)")
