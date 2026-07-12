import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, call, patch


SRC_PYTHON = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SRC_PYTHON)

_MISSING = object()
_saved_modules = {}
_temp_data = None
_local_app_data_patcher = None
_get_compute_device_list_patcher = None
_transcription_package = None
_previous_whisper_attribute = _MISSING
whisper = None
config_module = None
controller_module = None
mainloop = None


class _DeviceManagerStub:
    def getDefaultMicDevice(self):
        return {"host": {"name": "NoHost"}, "device": {"name": "NoDevice"}}

    def getDefaultSpeakerDevice(self):
        return {"device": {"name": "NoDevice"}}


def _remember_module(name):
    if name not in _saved_modules:
        _saved_modules[name] = sys.modules.get(name, _MISSING)


def _module(name, **attributes):
    _remember_module(name)
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _remove_module(name):
    _remember_module(name)
    sys.modules.pop(name, None)


def _import_whisper_with_real_requests():
    requests_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "requests" or name.startswith("requests.")
    }
    for name in requests_modules:
        sys.modules.pop(name, None)
    try:
        importlib.import_module("requests")
        return importlib.import_module("models.transcription.transcription_whisper")
    finally:
        for name in list(sys.modules):
            if name == "requests" or name.startswith("requests."):
                sys.modules.pop(name, None)
        sys.modules.update(requests_modules)


def _restore_test_environment():
    global _temp_data

    if config_module is not None:
        timer = getattr(config_module.config, "_timer", None)
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()

    if _get_compute_device_list_patcher is not None:
        _get_compute_device_list_patcher.stop()

    if _transcription_package is not None:
        if _previous_whisper_attribute is _MISSING:
            try:
                delattr(_transcription_package, "transcription_whisper")
            except AttributeError:
                pass
        else:
            setattr(
                _transcription_package,
                "transcription_whisper",
                _previous_whisper_attribute,
            )

    for name, previous in reversed(list(_saved_modules.items())):
        if previous is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    _saved_modules.clear()

    if _local_app_data_patcher is not None:
        _local_app_data_patcher.stop()
    if _temp_data is not None:
        _temp_data.cleanup()
        _temp_data = None


def setUpModule():
    global _temp_data
    global _local_app_data_patcher
    global _get_compute_device_list_patcher
    global _transcription_package
    global _previous_whisper_attribute
    global whisper
    global config_module
    global controller_module
    global mainloop

    try:
        _temp_data = tempfile.TemporaryDirectory()
        _local_app_data_patcher = patch.dict(
            os.environ,
            {"LOCALAPPDATA": _temp_data.name},
        )
        _local_app_data_patcher.start()

        _remember_module("models")
        _remember_module("models.transcription")
        _remember_module("models.transcription.transcription_whisper")
        _remember_module("utils")
        _transcription_package = importlib.import_module("models.transcription")
        _previous_whisper_attribute = getattr(
            _transcription_package,
            "transcription_whisper",
            _MISSING,
        )
        whisper = _import_whisper_with_real_requests()

        # Config initialization must not inspect Torch/CTranslate2 devices or
        # audio hardware during this focused unit test.
        utils = importlib.import_module("utils")
        _get_compute_device_list_patcher = patch.object(
            utils,
            "getComputeDeviceList",
            return_value=[
                {
                    "device": "cpu",
                    "device_index": 0,
                    "device_name": "cpu",
                    "compute_types": ["auto", "float32"],
                }
            ],
        )
        _get_compute_device_list_patcher.start()
        _module("torch", cuda=types.SimpleNamespace(is_available=lambda: False))
        _module("device_manager", device_manager=_DeviceManagerStub())
        _module(
            "models.translation.translation_languages",
            translation_lang={},
            loadTranslationLanguages=lambda *args, **kwargs: {},
        )
        _module("models.translation.translation_utils", ctranslate2_weights={})
        _module("models.transcription.transcription_languages", transcription_lang={})
        _module(
            "models.transcription.transcription_vosk",
            _MODELS={},
            getVoskModelMeta=lambda *args, **kwargs: {},
        )
        _module(
            "models.transcription.transcription_parakeet",
            _MODELS={},
            getParakeetModelMeta=lambda *args, **kwargs: {},
        )
        _module(
            "models.transcription.transcription_sensevoice",
            _MODELS={},
            getSenseVoiceModelMeta=lambda *args, **kwargs: {},
        )

        _remove_module("config")
        config_module = importlib.import_module("config")

        _module(
            "model",
            model=types.SimpleNamespace(),
            collapseTranslationEngineSelection=lambda value: value,
            normalizeTranslationEngineSelection=lambda value: value,
        )
        _module("resource_usage", collect_resource_usage=lambda: {})
        _remove_module("controller")
        controller_module = importlib.import_module("controller")
        _remove_module("mainloop")
        mainloop = importlib.import_module("mainloop")
    except Exception:
        _restore_test_environment()
        raise


def tearDownModule():
    _restore_test_environment()


class WhisperDecodingHelperTests(unittest.TestCase):
    def test_profiles_map_to_expected_beam_sizes(self):
        expected = {"fast": 1, "balanced": 2, "accurate": 5}

        for profile, beam_size in expected.items():
            with self.subTest(profile=profile):
                self.assertEqual(whisper.getWhisperBeamSize(profile), beam_size)

    def test_unknown_or_missing_beam_profile_defaults_to_balanced(self):
        self.assertEqual(whisper.getWhisperBeamSize("unsupported"), 2)
        self.assertEqual(whisper.getWhisperBeamSize(None), 2)
        self.assertEqual(whisper.getWhisperBeamSize("FAST"), 1)

    def test_cuda_auto_and_int8_resolve_to_int8_float16(self):
        for requested in ("auto", "int8", "AUTO", "INT8"):
            with self.subTest(requested=requested):
                self.assertEqual(
                    whisper.resolveWhisperComputeType("cuda", 2, requested),
                    "int8_float16",
                )

    def test_explicit_supported_cuda_compute_type_is_preserved(self):
        with patch.object(whisper, "getBestComputeType") as get_best:
            result = whisper.resolveWhisperComputeType("CUDA", 0, "FLOAT16")

        self.assertEqual(result, "float16")
        get_best.assert_not_called()

    def test_non_cuda_auto_uses_best_compute_type_for_exact_device(self):
        with patch.object(whisper, "getBestComputeType", return_value="float32") as get_best:
            result = whisper.resolveWhisperComputeType("CPU", 7, "auto")

        self.assertEqual(result, "float32")
        get_best.assert_called_once_with(device="CPU", device_index=7)

    def test_model_construction_receives_resolved_compute_type(self):
        constructor = Mock(return_value=object())
        with (
            patch.object(whisper, "_getWhisperModelClass", return_value=constructor),
            patch.object(
                whisper,
                "getBestComputeType",
                side_effect=AssertionError("CUDA auto must resolve deterministically"),
            ),
        ):
            whisper.getWhisperModel(
                "unused-root",
                "tiny",
                device="cuda",
                device_index=1,
                compute_type="auto",
            )

        self.assertEqual(constructor.call_args.kwargs["compute_type"], "int8_float16")


class WhisperDecodingConfigTests(unittest.TestCase):
    def _load_profile(self, payload):
        instance = object.__new__(config_module.Config)
        instance._WHISPER_DECODING_PROFILE = "balanced"
        instance._PATH_CONFIG = os.path.join(_temp_data.name, "isolated-config.json")
        instance._config_data = {}
        instance._timer = None
        instance._SELECTED_YOUR_LANGUAGES = {}
        instance.saveConfig = lambda key, value, immediate_save=False: instance._config_data.update(
            {key: value}
        )
        instance.saveConfigToFile = lambda: None
        with open(instance._PATH_CONFIG, "w", encoding="utf-8") as config_file:
            json.dump(payload, config_file)
        config_module.Config.load_config(instance)
        return instance

    def test_fresh_config_without_saved_profile_retains_balanced(self):
        instance = self._load_profile({})

        self.assertEqual(instance.WHISPER_DECODING_PROFILE, "balanced")

    def test_saved_mixed_case_profile_is_migrated_to_lowercase(self):
        instance = self._load_profile({"WHISPER_DECODING_PROFILE": "ACCURATE"})

        self.assertEqual(instance.WHISPER_DECODING_PROFILE, "accurate")

    def test_invalid_profile_is_normalized_to_balanced(self):
        instance = self._load_profile({"WHISPER_DECODING_PROFILE": "cinematic"})

        self.assertEqual(instance.WHISPER_DECODING_PROFILE, "balanced")
        instance.WHISPER_DECODING_PROFILE = object()
        self.assertEqual(instance.WHISPER_DECODING_PROFILE, "balanced")


class WhisperDecodingControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = controller_module.Controller()
        config_module.config._WHISPER_DECODING_PROFILE = "balanced"

    def test_get_and_set_profile_routes_exchange_lowercase_and_restart(self):
        restart = Mock()
        self.controller._requestCoordinatedTranscriptionRestart = restart

        self.assertEqual(
            self.controller.getWhisperDecodingProfile(),
            {"status": 200, "result": "balanced"},
        )
        self.assertEqual(
            self.controller.setWhisperDecodingProfile("ACCURATE"),
            {"status": 200, "result": "accurate"},
        )
        restart.assert_called_once_with()

    def test_invalid_route_value_returns_balanced_and_requests_restart(self):
        self.controller._requestCoordinatedTranscriptionRestart = Mock()

        result = self.controller.setWhisperDecodingProfile("not-a-profile")

        self.assertEqual(result, {"status": 200, "result": "balanced"})
        self.controller._requestCoordinatedTranscriptionRestart.assert_called_once_with()

    def test_coordinated_restart_delegates_to_existing_restart_sequence(self):
        config_module.config._ENABLE_TRANSCRIPTION_SEND = True
        config_module.config._ENABLE_TRANSCRIPTION_RECEIVE = True
        events = Mock()
        self.controller.stopTranscriptionSendMessage = lambda: events("stop-send")
        self.controller.stopTranscriptionReceiveMessage = lambda: events("stop-receive")
        self.controller.startTranscriptionSendMessage = lambda: events("start-send")
        self.controller.startTranscriptionReceiveMessage = lambda: events("start-receive")

        self.controller._requestCoordinatedTranscriptionRestart()

        self.assertEqual(
            events.call_args_list,
            [
                call("stop-send"),
                call("stop-receive"),
                call("start-send"),
                call("start-receive"),
            ],
        )


class WhisperDecodingMainloopRouteTests(unittest.TestCase):
    def test_mainloop_maps_profile_get_and_set_routes(self):
        self.assertIs(
            mainloop.mapping["/get/data/whisper_decoding_profile"]["variable"],
            mainloop.controller.getWhisperDecodingProfile,
        )
        self.assertIs(
            mainloop.mapping["/set/data/whisper_decoding_profile"]["variable"].__self__,
            mainloop.controller,
        )


if __name__ == "__main__":
    unittest.main()
