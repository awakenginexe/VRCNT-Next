"""Runtime transcriber that wraps Google SpeechRecognition and faster-whisper.

This class focuses on converting incoming raw audio buffers into text using
either the Google web recognizer (online) or a local Whisper model (offline).
"""

import time
import importlib
import gc
from io import BytesIO
from threading import Event
from queue import Empty
import wave
from typing import Any, Dict, List, Optional, Union
from speech_recognition import Recognizer, AudioData, AudioFile
from speech_recognition.exceptions import UnknownValueError
from datetime import timedelta
from pyaudiowpatch import get_sample_size, paInt16
from .transcription_languages import transcription_lang

import json
import numpy as np
from pydub import AudioSegment
from utils import errorLogging

import warnings
warnings.simplefilter('ignore', RuntimeWarning)

PHRASE_TIMEOUT = 3
MAX_PHRASES = 10
MAX_AUDIO_BUFFER_SECONDS = 30
MAX_WHISPER_LIVE_AUDIO_SECONDS = 6
GOOGLE_RECOGNITION_TIMEOUT_SECONDS = 15
ENGINE_RECOVERY_FAILURE_THRESHOLD = 3


def _getTorch():
    try:
        return importlib.import_module("torch")
    except Exception:
        return None


def _getWhisperHelpers():
    module = importlib.import_module(".transcription_whisper", __package__)
    return module.getWhisperModel, module.checkWhisperWeight


def _getVoskHelpers():
    module = importlib.import_module(".transcription_vosk", __package__)
    return module.getVoskRecognizer, module.checkVoskWeight


def _getParakeetHelpers():
    module = importlib.import_module(".transcription_parakeet", __package__)
    return module.getParakeetModel, module.checkParakeetWeight


def _getSenseVoiceHelpers():
    module = importlib.import_module(".transcription_sensevoice", __package__)
    return module.getSenseVoiceModel, module.checkSenseVoiceWeight


def _languageCode(engine: str, language: str, country: str) -> str:
    try:
        return transcription_lang[language][country].get(engine, "")
    except Exception:
        return ""


def _languageForCode(engine: str, code: Optional[str], languages: List[str], countries: List[str]) -> Optional[str]:
    if not code:
        return None
    for language, country in zip(languages, countries):
        if _languageCode(engine, language, country) == code:
            return language
    return None


class AudioTranscriber:
    """Convert queued audio buffers into transcripts.

    Public attributes set by the constructor:
    - speaker: bool
    - phrase_timeout: int
    - max_phrases: int

    Methods are intentionally permissive about input types to match the
    existing codebase; this wrapper adds typing for clarity.
    """

    def __init__(
        self,
        speaker: bool,
        source: Any,
        phrase_timeout: int,
        max_phrases: int,
        transcription_engine: str,
        root: Optional[str] = None,
        whisper_weight_type: Optional[str] = None,
        vosk_weight_type: Optional[str] = None,
        parakeet_weight_type: Optional[str] = None,
        sensevoice_weight_type: Optional[str] = None,
        device: str = "cpu",
        device_index: int = 0,
        compute_type: str = "auto",
    ) -> None:
        self.speaker = speaker
        self.phrase_timeout = phrase_timeout
        self.max_phrases = max_phrases
        self.transcript_data: List[Dict[str, Any]] = []
        self.transcript_changed_event = Event()
        self.audio_recognizer = Recognizer()
        self.audio_recognizer.operation_timeout = GOOGLE_RECOGNITION_TIMEOUT_SECONDS
        self.transcription_engine = "Google"
        self.whisper_model = None
        self.vosk_recognizer = None
        self.parakeet_model = None
        self.sensevoice_model = None
        self.root = root
        self.whisper_weight_type = whisper_weight_type
        self.device = device
        self.device_index = device_index
        self.compute_type = compute_type
        self._recognition_failure_count = 0
        self.audio_sources: Dict[str, Any] = {
            "sample_rate": source.SAMPLE_RATE,
            "sample_width": source.SAMPLE_WIDTH,
            "channels": source.channels,
            "last_sample": bytes(),
            "last_spoken": None,
            "new_phrase": True,
            "process_data_func": self.processSpeakerData if speaker else self.processMicData,
        }

        if transcription_engine == "Whisper":
            getWhisperModel, checkWhisperWeight = _getWhisperHelpers()
        elif transcription_engine == "Vosk":
            getVoskRecognizer, checkVoskWeight = _getVoskHelpers()
        elif transcription_engine == "Parakeet":
            getParakeetModel, checkParakeetWeight = _getParakeetHelpers()
        elif transcription_engine == "SenseVoice":
            getSenseVoiceModel, checkSenseVoiceWeight = _getSenseVoiceHelpers()

        if transcription_engine == "Whisper" and checkWhisperWeight(root, whisper_weight_type) is True:
            self.whisper_model = getWhisperModel(
                root, whisper_weight_type, device=device, device_index=device_index, compute_type=compute_type
            )
            self.transcription_engine = "Whisper"
        elif transcription_engine == "Vosk" and vosk_weight_type and checkVoskWeight(root, vosk_weight_type) is True:
            try:
                self.vosk_recognizer = getVoskRecognizer(root, vosk_weight_type)
                self.transcription_engine = "Vosk"
            except Exception:
                errorLogging()
        elif transcription_engine == "Parakeet" and parakeet_weight_type and checkParakeetWeight(root, parakeet_weight_type) is True:
            try:
                self.parakeet_model = getParakeetModel(
                    root, parakeet_weight_type, device=device, device_index=device_index
                )
                self.transcription_engine = "Parakeet"
            except Exception:
                errorLogging()
        elif transcription_engine == "SenseVoice" and sensevoice_weight_type and checkSenseVoiceWeight(root, sensevoice_weight_type) is True:
            try:
                self.sensevoice_model = getSenseVoiceModel(
                    root, sensevoice_weight_type, device="cpu", device_index=0
                )
                self.transcription_engine = "SenseVoice"
            except Exception:
                errorLogging()

    def _resetRecognizer(self) -> None:
        self.audio_recognizer = Recognizer()
        self.audio_recognizer.operation_timeout = GOOGLE_RECOGNITION_TIMEOUT_SECONDS

    def _releaseWhisperModel(self) -> None:
        old_model = self.whisper_model
        self.whisper_model = None
        try:
            del old_model
        except Exception:
            pass
        gc.collect()
        torch = _getTorch()
        if torch is not None:
            try:
                if hasattr(torch, "cuda") and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    def _reloadWhisperModel(self) -> None:
        if not self.root or not self.whisper_weight_type:
            return
        try:
            getWhisperModel, checkWhisperWeight = _getWhisperHelpers()
            if checkWhisperWeight(self.root, self.whisper_weight_type) is not True:
                return
            self._releaseWhisperModel()
            self.whisper_model = getWhisperModel(
                self.root,
                self.whisper_weight_type,
                device=self.device,
                device_index=self.device_index,
                compute_type=self.compute_type,
            )
        except Exception:
            errorLogging()

    def _handleRecognitionFailure(self) -> None:
        self._recognition_failure_count += 1
        if self._recognition_failure_count < ENGINE_RECOVERY_FAILURE_THRESHOLD:
            return
        self._recognition_failure_count = 0
        self.clearTranscriptData()
        match self.transcription_engine:
            case "Google":
                self._resetRecognizer()
            case "Whisper":
                self._reloadWhisperModel()
            case _:
                pass

    def _handleRecognitionSuccess(self) -> None:
        self._recognition_failure_count = 0

    def clearLiveAudioSample(self) -> None:
        self.audio_sources["last_sample"] = bytes()

    def transcribeAudioQueue(
        self,
        audio_queue: Any,
        languages: List[str],
        countries: List[str],
        avg_logprob: float = -0.8,
        no_speech_prob: float = 0.6,
        no_repeat_ngram_size: int = 0,
        vad_filter: bool = False,
        vad_parameters: Optional[Union[dict, Any]] = None,
    ) -> bool:
        if audio_queue.empty():
            time.sleep(0.01)
            return False
        audio, time_spoken = audio_queue.get()
        self.updateLastSampleAndPhraseStatus(audio, time_spoken)
        self.drainAudioQueue(audio_queue)

        confidences: List[Dict[str, Any]] = [{"confidence": 0, "text": "", "language": None}]
        try:
            if not languages or not countries:
                return True
            audio_data = self.audio_sources["process_data_func"]()
            match self.transcription_engine:
                case "Google":
                    google_error_count = 0
                    for language, country in zip(languages, countries):
                        try:
                            text, confidence = self.audio_recognizer.recognize_google(
                                audio_data,
                                language=transcription_lang[language][country][self.transcription_engine],
                                with_confidence=True
                                )
                            confidences.append({"confidence": confidence, "text": text, "language": language})
                        except UnknownValueError:
                            pass
                        except Exception:
                            google_error_count += 1
                            errorLogging()
                            pass
                    if len(languages) > 0 and google_error_count >= len(languages):
                        self._handleRecognitionFailure()
                case "Whisper":
                    try:
                        if self.whisper_model is None:
                            self._handleRecognitionFailure()
                            return True
                        audio_data = np.frombuffer(
                            audio_data.get_raw_data(convert_rate=16000, convert_width=2), np.int16
                        ).flatten().astype(np.float32) / 32768.0
                        torch = _getTorch()
                        if torch is not None and isinstance(audio_data, torch.Tensor):
                            audio_data = audio_data.detach().numpy()
                        if audio_data.size == 0 or not np.any(audio_data):
                            return True
                        max_samples = 16000 * MAX_WHISPER_LIVE_AUDIO_SECONDS
                        if audio_data.size > max_samples:
                            audio_data = audio_data[-max_samples:]

                        source_language = _languageCode("Whisper", languages[0], countries[0]) if len(languages) == 1 else None
                        segments, info = self.whisper_model.transcribe(
                            audio_data,
                            beam_size=5,
                            temperature=0.0,
                            log_prob_threshold=avg_logprob,
                            no_speech_threshold=no_speech_prob,
                            language=source_language,
                            word_timestamps=False,
                            without_timestamps=True,
                            task="transcribe",
                            no_repeat_ngram_size=no_repeat_ngram_size,
                            vad_filter=vad_filter,
                            vad_parameters=vad_parameters,
                        )
                        text = ""
                        try:
                            for s in segments:
                                if s.avg_logprob < avg_logprob or s.no_speech_prob > no_speech_prob:
                                    continue
                                text += s.text
                        except Exception:
                            errorLogging()
                            self._handleRecognitionFailure()
                            return True

                        result_language = (
                            languages[0] if len(languages) == 1
                            else _languageForCode("Whisper", getattr(info, "language", None), languages, countries)
                        )
                        if result_language:
                            confidences.append({
                                "confidence": info.language_probability,
                                "text": text,
                                "language": result_language,
                            })
                    finally:
                        self.clearLiveAudioSample()
                case "Vosk":
                    if self.vosk_recognizer is None:
                        pass
                    else:
                        try:
                            pcm16 = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
                            result_text = self.vosk_recognizer.transcribe(pcm16, sample_rate=16000)
                        except Exception:
                            result_text = ""
                        if result_text:
                            primary = languages[0] if languages else None
                            confidences.append({"confidence": 1.0, "text": result_text, "language": primary})
                case "Parakeet":
                    if self.parakeet_model is None:
                        pass
                    else:
                        pcm = np.frombuffer(
                            audio_data.get_raw_data(convert_rate=16000, convert_width=2), np.int16
                        ).flatten().astype(np.float32) / 32768.0
                        try:
                            result_text = self.parakeet_model.transcribe(pcm, sample_rate=16000)
                        except Exception:
                            result_text = ""
                        if result_text:
                            primary = languages[0] if languages else None
                            confidences.append({"confidence": 1.0, "text": result_text, "language": primary})
                case "SenseVoice":
                    if self.sensevoice_model is None:
                        pass
                    else:
                        pcm = np.frombuffer(
                            audio_data.get_raw_data(convert_rate=16000, convert_width=2), np.int16
                        ).flatten().astype(np.float32) / 32768.0
                        try:
                            source_language = (
                                _languageCode("SenseVoice", languages[0], countries[0])
                                if len(languages) == 1
                                else "auto"
                            )
                            recognize = getattr(self.sensevoice_model, "recognize", None)
                            if callable(recognize):
                                result = recognize(pcm, sample_rate=16000, language=source_language)
                                result_text = result.get("text", "")
                                result_language_code = result.get("language", "")
                            else:
                                result_text = self.sensevoice_model.transcribe(
                                    pcm, sample_rate=16000, language=source_language
                                )
                                result_language_code = source_language if source_language != "auto" else ""
                        except Exception:
                            result_text = ""
                            result_language_code = ""
                        if result_text:
                            primary = (
                                languages[0] if len(languages) == 1
                                else _languageForCode("SenseVoice", result_language_code, languages, countries)
                            )
                            if primary:
                                confidences.append({"confidence": 1.0, "text": result_text, "language": primary})

        except UnknownValueError:
            pass
        except Exception:
            errorLogging()
            self._handleRecognitionFailure()
        finally:
            pass

        result = max(confidences, key=lambda x: x["confidence"])
        if result["text"] != "":
            self._handleRecognitionSuccess()
            self.updateTranscript(result)
        return True

    def resetAudioSource(self, source: Any) -> None:
        self.audio_sources["sample_rate"] = source.SAMPLE_RATE
        self.audio_sources["sample_width"] = source.SAMPLE_WIDTH
        self.audio_sources["channels"] = source.channels
        self.audio_sources["last_sample"] = bytes()
        self.audio_sources["last_spoken"] = None
        self.audio_sources["new_phrase"] = True

    def drainAudioQueue(self, audio_queue: Any) -> None:
        while True:
            try:
                audio, time_spoken = audio_queue.get_nowait()
            except Empty:
                break
            except Exception:
                break
            self.updateLastSampleAndPhraseStatus(audio, time_spoken)

    def updateLastSampleAndPhraseStatus(self, data: bytes, time_spoken) -> None:
        source_info = self.audio_sources
        if source_info["last_spoken"] and time_spoken - source_info["last_spoken"] > timedelta(seconds=self.phrase_timeout):
            source_info["last_sample"] = bytes()
            source_info["new_phrase"] = True
        else:
            source_info["new_phrase"] = False

        source_info["last_sample"] += data
        source_info["last_spoken"] = time_spoken
        self.trimLastSampleToMaxDuration()

    def trimLastSampleToMaxDuration(self) -> None:
        source_info = self.audio_sources
        try:
            frame_width = max(1, int(source_info["sample_width"]) * int(source_info["channels"]))
            max_frames = int(source_info["sample_rate"]) * MAX_AUDIO_BUFFER_SECONDS
            max_bytes = max(frame_width, max_frames * frame_width)
            if len(source_info["last_sample"]) > max_bytes:
                source_info["last_sample"] = source_info["last_sample"][-max_bytes:]
        except Exception:
            errorLogging()

    def processMicData(self) -> AudioData:
        audio_data = AudioData(
            self.audio_sources["last_sample"], self.audio_sources["sample_rate"], self.audio_sources["sample_width"]
        )
        return audio_data

    def processSpeakerData(self) -> AudioData:
        temp_file = BytesIO()
        with wave.open(temp_file, 'wb') as wf:
            wf.setnchannels(self.audio_sources["channels"])
            wf.setsampwidth(get_sample_size(paInt16))
            wf.setframerate(self.audio_sources["sample_rate"])
            wf.writeframes(self.audio_sources["last_sample"])
        temp_file.seek(0)

        if self.audio_sources["channels"] > 2:
            audio = AudioSegment.from_file(temp_file, format="wav")
            mono_audio = audio.set_channels(1)
            temp_file = BytesIO()
            mono_audio.export(temp_file, format="wav")
            temp_file.seek(0)

        with AudioFile(temp_file) as source:
            audio = self.audio_recognizer.record(source)
        return audio

    def updateTranscript(self, result: dict) -> None:
        source_info = self.audio_sources
        transcript = self.transcript_data

        if source_info["new_phrase"] or len(transcript) == 0:
            if len(transcript) > self.max_phrases:
                transcript.pop(-1)
            transcript.insert(0, result)
        else:
            transcript[0] = result

    def getTranscript(self) -> dict:
        if len(self.transcript_data) > 0:
            result = self.transcript_data.pop(-1)
        else:
            result = {"confidence": 0, "text": "", "language": None}
        return result

    def clearTranscriptData(self) -> None:
        self.transcript_data.clear()
        self.audio_sources["last_sample"] = bytes()
        self.audio_sources["new_phrase"] = True
