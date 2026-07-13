import importlib
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import controller as controller_module
import errors as errors_module
import model as model_module
from controller import Controller
from model import Model
from models.pipeline.pipeline_types import PipelineSource


WAIT_SECONDS = 2.0


def _controller_for_activation():
    controller = object.__new__(Controller)
    controller.device_access_status = True
    controller._transcription_restart_lock = threading.RLock()
    controller._transcription_shutdown_requested = threading.Event()
    controller._transcription_shutdown_state = "running"
    controller.run = Mock()
    controller.run_mapping = {
        "error_translation_enable_vram_overflow": "/run/error/translation",
        "enable_translation": "/run/enable_translation",
        "error_transcription_mic_vram_overflow": "/run/error/mic",
        "enable_transcription_send": "/run/enable_transcription_send",
        "error_transcription_speaker_vram_overflow": "/run/error/speaker",
        "enable_transcription_receive": "/run/enable_transcription_receive",
        "initialization_status": "/run/initialization_status",
    }
    return controller


def _transcription_model_patches(source, start):
    start_name = (
        "startMicTranscript"
        if source is PipelineSource.MIC
        else "startSpeakerTranscript"
    )
    return (
        patch.object(model_module.model, "ensureSourcePipeline", return_value=object()),
        patch.object(model_module.model, "nextSourcePipelineGeneration", return_value=1),
        patch.object(model_module.model, start_name, side_effect=start),
        patch.object(model_module.model, "stopSourcePipeline"),
        patch.object(model_module.model, "stopMicTranscript"),
        patch.object(model_module.model, "stopSpeakerTranscript"),
    )


class MainFunctionActivationTests(unittest.TestCase):
    def _assert_activation_error(self, response, error_code, status=None):
        if status is not None:
            self.assertEqual(response["status"], status)
        else:
            self.assertNotEqual(response["status"], 200)
        self.assertIsInstance(response["result"], dict)
        self.assertEqual(response["result"]["error_code"], error_code)
        self.assertIsInstance(response["result"]["message"], str)
        self.assertIs(response["result"]["data"], False)

    def _assert_transcription_activation_waits(self, source):
        controller = _controller_for_activation()
        start_entered = threading.Event()
        release_start = threading.Event()
        results = []

        def blocked_start(_callback):
            start_entered.set()
            self.assertTrue(release_start.wait(WAIT_SECONDS))
            return True

        config_name = (
            "_ENABLE_TRANSCRIPTION_SEND"
            if source is PipelineSource.MIC
            else "_ENABLE_TRANSCRIPTION_RECEIVE"
        )
        setter = (
            controller.setEnableTranscriptionSend
            if source is PipelineSource.MIC
            else controller.setEnableTranscriptionReceive
        )
        patches = _transcription_model_patches(source, blocked_start)
        try:
            with (
                patch.object(controller_module.config, config_name, False),
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
            ):
                thread = threading.Thread(target=lambda: results.append(setter()))
                thread.start()
                self.assertTrue(start_entered.wait(WAIT_SECONDS))
                self.assertEqual(results, [])
                release_start.set()
                thread.join(WAIT_SECONDS)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [{"status": 200, "result": True}])
        finally:
            release_start.set()

    def test_transcription_send_enable_waits_for_real_readiness(self):
        self._assert_transcription_activation_waits(PipelineSource.MIC)

    def test_transcription_receive_enable_waits_for_real_readiness(self):
        self._assert_transcription_activation_waits(PipelineSource.SPEAKER)

    def test_controller_wrapper_returns_model_readiness_and_restores_device_gate(self):
        controller = _controller_for_activation()
        patches = _transcription_model_patches(PipelineSource.MIC, lambda _callback: True)
        with (
            patch.object(controller_module.config, "_ENABLE_TRANSCRIPTION_SEND", True),
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
        ):
            self.assertIs(controller.startTranscriptionSendMessage(), True)
        self.assertIs(controller.device_access_status, True)

    def test_controller_wrapper_restores_device_gate_when_model_start_raises(self):
        controller = _controller_for_activation()
        error = RuntimeError("start failed")
        patches = _transcription_model_patches(PipelineSource.SPEAKER, error)
        with (
            patch.object(controller_module.config, "_ENABLE_TRANSCRIPTION_RECEIVE", True),
            patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
        ):
            with self.assertRaisesRegex(RuntimeError, "start failed"):
                controller.startTranscriptionReceiveMessage()
        self.assertIs(controller.device_access_status, True)

    def test_shutdown_intent_during_source_load_cancels_readiness_and_unwinds_source(self):
        for source in (PipelineSource.MIC, PipelineSource.SPEAKER):
            with self.subTest(source=source):
                start_entered = threading.Event()
                release_start = threading.Event()
                source_stopped = threading.Event()
                shutdown_finished = threading.Event()
                endpoint_results = []
                shutdown_results = []
                fake_model = Mock()
                fake_model.nextSourcePipelineGeneration.return_value = 1
                fake_model.detectVRAMError.return_value = (False, None)

                def blocked_start(_callback):
                    start_entered.set()
                    self.assertTrue(release_start.wait(WAIT_SECONDS))
                    return True

                if source is PipelineSource.MIC:
                    fake_model.startMicTranscript.side_effect = blocked_start
                    fake_model.stopMicTranscript.side_effect = source_stopped.set
                    setter_name = "setEnableTranscriptionSend"
                    config_name = "_ENABLE_TRANSCRIPTION_SEND"
                else:
                    fake_model.startSpeakerTranscript.side_effect = blocked_start
                    fake_model.stopSpeakerTranscript.side_effect = source_stopped.set
                    setter_name = "setEnableTranscriptionReceive"
                    config_name = "_ENABLE_TRANSCRIPTION_RECEIVE"

                def run_shutdown(controller):
                    shutdown_results.append(controller.shutdown())
                    shutdown_finished.set()

                with (
                    patch.object(controller_module, "model", fake_model),
                    patch.object(controller_module.config, config_name, False),
                ):
                    controller = Controller()
                    enable_thread = threading.Thread(
                        target=lambda: endpoint_results.append(
                            getattr(controller, setter_name)()
                        )
                    )
                    shutdown_thread = threading.Thread(
                        target=run_shutdown,
                        args=(controller,),
                    )
                    enable_thread.start()
                    self.assertTrue(start_entered.wait(WAIT_SECONDS))
                    shutdown_thread.start()
                    self.assertTrue(
                        controller._transcription_shutdown_requested.wait(
                            WAIT_SECONDS
                        )
                    )
                    release_start.set()
                    enable_thread.join(WAIT_SECONDS)
                    shutdown_thread.join(WAIT_SECONDS)

                self.assertFalse(enable_thread.is_alive())
                self.assertFalse(shutdown_thread.is_alive())
                self.assertTrue(shutdown_finished.is_set())
                self.assertTrue(source_stopped.is_set())
                self.assertEqual(len(endpoint_results), 1)
                self.assertNotEqual(endpoint_results[0]["status"], 200)
                self.assertIs(endpoint_results[0]["result"]["data"], False)
                self.assertIs(
                    getattr(controller_module.config, config_name[1:]),
                    False,
                )
                self.assertEqual(
                    shutdown_results,
                    [{"status": 200, "result": True}],
                )

    def test_missing_device_is_rejected_before_pipeline_allocation(self):
        exception_type = getattr(errors_module, "DeviceUnavailableError", RuntimeError)
        for source, error_code in (
            (PipelineSource.MIC, errors_module.ErrorCode.DEVICE_NO_MIC),
            (PipelineSource.SPEAKER, errors_module.ErrorCode.DEVICE_NO_SPEAKER),
        ):
            with self.subTest(source=source):
                controller = _controller_for_activation()
                config_name = (
                    "_ENABLE_TRANSCRIPTION_SEND"
                    if source is PipelineSource.MIC
                    else "_ENABLE_TRANSCRIPTION_RECEIVE"
                )
                setter = (
                    controller.setEnableTranscriptionSend
                    if source is PipelineSource.MIC
                    else controller.setEnableTranscriptionReceive
                )
                validation_name = (
                    "validateMicTranscriptDevice"
                    if source is PipelineSource.MIC
                    else "validateSpeakerTranscriptDevice"
                )
                start_name = (
                    "startMicTranscript"
                    if source is PipelineSource.MIC
                    else "startSpeakerTranscript"
                )
                with (
                    patch.object(controller_module.config, config_name, False),
                    patch.object(
                        model_module.model,
                        validation_name,
                        create=True,
                        side_effect=exception_type(error_code),
                    ) as validate,
                    patch.object(
                        model_module.model,
                        "ensureSourcePipeline",
                    ) as ensure_pipeline,
                    patch.object(
                        model_module.model,
                        start_name,
                        side_effect=exception_type(error_code),
                    ) as start,
                    patch.object(model_module.model, "stopSourcePipeline"),
                    patch.object(model_module.model, "stopMicTranscript"),
                    patch.object(model_module.model, "stopSpeakerTranscript"),
                ):
                    response = setter()

                self._assert_activation_error(response, error_code.value)
                validate.assert_called_once_with()
                ensure_pipeline.assert_not_called()
                start.assert_not_called()

    def _assert_direct_transcription_error(self, source, error, expected_code):
        controller = _controller_for_activation()
        config_name = (
            "_ENABLE_TRANSCRIPTION_SEND"
            if source is PipelineSource.MIC
            else "_ENABLE_TRANSCRIPTION_RECEIVE"
        )
        setter = (
            controller.setEnableTranscriptionSend
            if source is PipelineSource.MIC
            else controller.setEnableTranscriptionReceive
        )
        stop_name = (
            "stopTranscriptionSendMessage"
            if source is PipelineSource.MIC
            else "stopTranscriptionReceiveMessage"
        )
        start_name = (
            "startTranscriptionSendMessage"
            if source is PipelineSource.MIC
            else "startTranscriptionReceiveMessage"
        )
        with (
            patch.object(controller_module.config, config_name, False),
            patch.object(controller, start_name, side_effect=error),
            patch.object(controller, stop_name) as stop,
            patch.object(model_module.model, "detectVRAMError", return_value=(False, None)),
        ):
            response = setter()
            self._assert_activation_error(response, expected_code)
            self.assertIs(getattr(controller_module.config, config_name[1:]), False)
            stop.assert_called_once_with()

    def test_generic_send_start_error_is_structured_and_cleans_up(self):
        self._assert_direct_transcription_error(
            PipelineSource.MIC,
            RuntimeError("boom"),
            "TRANSCRIPTION_START_FAILED",
        )

    def test_generic_receive_start_error_is_structured_and_cleans_up(self):
        self._assert_direct_transcription_error(
            PipelineSource.SPEAKER,
            RuntimeError("boom"),
            "TRANSCRIPTION_START_FAILED",
        )

    def test_device_errors_are_source_specific_structured_failures(self):
        exception_type = getattr(errors_module, "DeviceUnavailableError", RuntimeError)
        for source, code in (
            (PipelineSource.MIC, errors_module.ErrorCode.DEVICE_NO_MIC),
            (PipelineSource.SPEAKER, errors_module.ErrorCode.DEVICE_NO_SPEAKER),
        ):
            with self.subTest(source=source):
                self._assert_direct_transcription_error(
                    source,
                    exception_type(code),
                    code.value,
                )

    def test_vram_errors_are_source_specific_and_restore_flags(self):
        for source, code in (
            (PipelineSource.MIC, "TRANSCRIPTION_VRAM_MIC"),
            (PipelineSource.SPEAKER, "TRANSCRIPTION_VRAM_SPEAKER"),
        ):
            controller = _controller_for_activation()
            config_name = (
                "_ENABLE_TRANSCRIPTION_SEND"
                if source is PipelineSource.MIC
                else "_ENABLE_TRANSCRIPTION_RECEIVE"
            )
            setter = (
                controller.setEnableTranscriptionSend
                if source is PipelineSource.MIC
                else controller.setEnableTranscriptionReceive
            )
            start_name = (
                "startTranscriptionSendMessage"
                if source is PipelineSource.MIC
                else "startTranscriptionReceiveMessage"
            )
            stop_name = (
                "stopTranscriptionSendMessage"
                if source is PipelineSource.MIC
                else "stopTranscriptionReceiveMessage"
            )
            with (
                self.subTest(source=source),
                patch.object(controller_module.config, config_name, False),
                patch.object(controller, start_name, side_effect=ValueError("vram")),
                patch.object(controller, stop_name),
                patch.object(model_module.model, "detectVRAMError", return_value=(True, "low vram")),
            ):
                response = setter()
                self._assert_activation_error(response, code)
                self.assertIs(getattr(controller_module.config, config_name[1:]), False)

    def test_cleanup_failure_does_not_hide_original_activation_error(self):
        controller = _controller_for_activation()
        with (
            patch.object(controller_module.config, "_ENABLE_TRANSCRIPTION_SEND", False),
            patch.object(controller, "startTranscriptionSendMessage", side_effect=RuntimeError("original")),
            patch.object(controller, "stopTranscriptionSendMessage", side_effect=RuntimeError("cleanup")),
            patch.object(model_module.model, "detectVRAMError", return_value=(False, None)),
        ):
            response = controller.setEnableTranscriptionSend()
        self._assert_activation_error(response, "TRANSCRIPTION_START_FAILED", status=500)

    def test_missing_devices_raise_before_recorder_or_transcriber_creation(self):
        exception_type = getattr(errors_module, "DeviceUnavailableError", RuntimeError)
        instance = object.__new__(Model)
        instance._inited = True
        instance._ensureTranscriptionLifecycleState()
        instance.mic_print_transcript = None
        instance.mic_audio_recorder = None
        instance.mic_whisper_runtime_lease = None
        instance.speaker_print_transcript = None
        instance.speaker_audio_recorder = None
        instance.speaker_whisper_runtime_lease = None
        recorder = Mock()
        transcriber = Mock()
        config_values = {
            "_ENABLE_TRANSCRIPTION_SEND": True,
            "_ENABLE_TRANSCRIPTION_RECEIVE": True,
            "_SELECTED_MIC_HOST": "Host",
            "_SELECTED_MIC_DEVICE": "NoDevice",
            "_SELECTED_SPEAKER_DEVICE": "NoDevice",
        }
        with (
            patch.multiple(model_module.config, **config_values),
            patch.object(model_module.device_manager, "getMicDevices", return_value={"Host": []}),
            patch.object(model_module.device_manager, "getSpeakerDevices", return_value=[]),
            patch.object(model_module, "SelectedMicEnergyAndAudioRecorder", recorder),
            patch.object(model_module, "SelectedSpeakerEnergyAndAudioRecorder", recorder),
            patch.object(model_module, "AudioTranscriber", transcriber),
        ):
            with self.assertRaises(exception_type) as mic_error:
                instance._startMicTranscript(lambda _result: None, generation=1)
            with self.assertRaises(exception_type) as speaker_error:
                instance._startSpeakerTranscript(lambda _result: None, generation=2)
        self.assertEqual(mic_error.exception.error_code, errors_module.ErrorCode.DEVICE_NO_MIC)
        self.assertEqual(speaker_error.exception.error_code, errors_module.ErrorCode.DEVICE_NO_SPEAKER)
        recorder.assert_not_called()
        transcriber.assert_not_called()

    def test_google_and_bing_translation_enable_without_local_model_loading(self):
        controller = _controller_for_activation()
        with (
            patch.multiple(
                controller_module.config,
                _ENABLE_TRANSLATION=False,
                _SELECTED_TAB_NO="1",
                _SELECTED_TRANSLATION_ENGINES={"1": ["Google", "Bing"]},
            ),
            patch.object(model_module.model, "changeTranslatorCTranslate2Model") as change_model,
        ):
            self.assertEqual(
                controller.setEnableTranslation(),
                {"status": 200, "result": True},
            )
            change_model.assert_not_called()

    def test_ctranslate2_translation_enable_waits_for_model_readiness(self):
        controller = _controller_for_activation()
        start_entered = threading.Event()
        release_start = threading.Event()
        results = []

        def blocked_change():
            start_entered.set()
            self.assertTrue(release_start.wait(WAIT_SECONDS))

        try:
            with (
                patch.multiple(
                    controller_module.config,
                    _ENABLE_TRANSLATION=False,
                    _SELECTED_TAB_NO="1",
                    _SELECTED_TRANSLATION_ENGINES={"1": "CTranslate2"},
                ),
                patch.object(model_module.model, "isLoadedCTranslate2Model", return_value=False),
                patch.object(model_module.model, "isChangedTranslatorParameters", return_value=False),
                patch.object(model_module.model, "changeTranslatorCTranslate2Model", side_effect=blocked_change),
                patch.object(model_module.model, "setChangedTranslatorParameters"),
            ):
                thread = threading.Thread(
                    target=lambda: results.append(controller.setEnableTranslation())
                )
                thread.start()
                self.assertTrue(start_entered.wait(WAIT_SECONDS))
                self.assertEqual(results, [])
                release_start.set()
                thread.join(WAIT_SECONDS)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [{"status": 200, "result": True}])
        finally:
            release_start.set()

    def test_translation_generic_and_vram_errors_are_structured_and_restore_flag(self):
        for is_vram, code, expected_status in (
            (False, "TRANSLATION_ENABLE_FAILED", 500),
            (True, "TRANSLATION_VRAM_ENABLE", None),
        ):
            controller = _controller_for_activation()
            with (
                self.subTest(is_vram=is_vram),
                patch.multiple(
                    controller_module.config,
                    _ENABLE_TRANSLATION=False,
                    _SELECTED_TAB_NO="1",
                    _SELECTED_TRANSLATION_ENGINES={"1": "CTranslate2"},
                ),
                patch.object(model_module.model, "isLoadedCTranslate2Model", return_value=False),
                patch.object(model_module.model, "isChangedTranslatorParameters", return_value=False),
                patch.object(model_module.model, "changeTranslatorCTranslate2Model", side_effect=RuntimeError("load failed")),
                patch.object(model_module.model, "setChangedTranslatorParameters"),
                patch.object(model_module.model, "detectVRAMError", return_value=(is_vram, "low vram" if is_vram else None)),
            ):
                response = controller.setEnableTranslation()
                self._assert_activation_error(response, code, expected_status)
                self.assertIs(controller_module.config.ENABLE_TRANSLATION, False)

    def test_initialization_status_includes_localization_keys(self):
        controller = _controller_for_activation()
        controller.initializationStatus(
            "Starting",
            "Detail",
            visible=True,
            phase="error",
            message_key="blocking_operation.startup_failed",
            detail_key="blocking_operation.startup_failed_detail",
        )
        controller.run.assert_called_once_with(
            200,
            "/run/initialization_status",
            {
                "message": "Starting",
                "detail": "Detail",
                "visible": True,
                "phase": "error",
                "message_key": "blocking_operation.startup_failed",
                "detail_key": "blocking_operation.startup_failed_detail",
            },
        )

    def test_core_startup_failure_emits_terminal_localized_status_and_stays_unready(self):
        mainloop = importlib.import_module("mainloop")
        run_initialization = getattr(mainloop, "runControllerInitialization", None)
        self.assertTrue(callable(run_initialization))
        order = []
        fake_controller = SimpleNamespace(
            init=Mock(side_effect=RuntimeError("core failed")),
            initializationStatus=Mock(side_effect=lambda *args, **kwargs: order.append("status")),
        )
        fake_mapping = {
            "/run/shutdown": {"status": False},
            "/get/data/version": {"status": False},
        }
        fake_main = SimpleNamespace(controller=fake_controller, mapping=fake_mapping)
        with patch.object(mainloop, "errorLogging", side_effect=lambda: order.append("log")):
            result = run_initialization(fake_main)
        self.assertIs(result, False)
        self.assertEqual(order, ["status", "log"])
        fake_controller.initializationStatus.assert_called_once_with(
            "",
            "",
            visible=True,
            phase="error",
            message_key="blocking_operation.startup_failed",
            detail_key="blocking_operation.startup_failed_detail",
        )
        self.assertTrue(all(item["status"] is False for item in fake_mapping.values()))

    def test_core_startup_success_marks_mapping_ready_and_returns_true(self):
        mainloop = importlib.import_module("mainloop")
        run_initialization = getattr(mainloop, "runControllerInitialization", None)
        self.assertTrue(callable(run_initialization))
        fake_controller = SimpleNamespace(init=Mock())
        fake_mapping = {
            "/run/shutdown": {"status": False},
            "/get/data/version": {"status": False},
        }
        fake_main = SimpleNamespace(controller=fake_controller, mapping=fake_mapping)
        self.assertIs(run_initialization(fake_main), True)
        fake_controller.init.assert_called_once_with()
        self.assertTrue(all(item["status"] is True for item in fake_mapping.values()))


if __name__ == "__main__":
    unittest.main()
