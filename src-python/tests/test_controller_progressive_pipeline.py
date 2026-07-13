import os
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import controller as controller_module
import model as model_module
from models.pipeline.pipeline_types import (
    FinalOutputTask,
    LanguageSlotSnapshot,
    MessageFormatSnapshot,
    OutputConfigSnapshot,
    PipelineSource,
    PipelineStatusEvent,
    TranslationStatus,
    TranslationTarget,
    TranslationUpdate,
)


class _ImmediatePipeline:
    def __init__(self, emit_initial):
        self.emit_initial = emit_initial
        self.traces = []

    def submit_trace(self, trace):
        self.traces.append(trace)
        self.emit_initial(trace)
        return True


def _format_snapshot():
    return MessageFormatSnapshot(
        message_prefix="<m>",
        message_suffix="</m>",
        translation_prefix="<t>",
        translation_suffix="</t>",
        translation_separator=" / ",
        message_translation_separator=" | ",
        translation_first=False,
    )


def _output_snapshot(**overrides):
    values = {
        "selected_tab_no": "1",
        "translation_enabled": True,
        "send_message_to_vrc": True,
        "send_received_message_to_vrc": True,
        "send_only_translated_messages": False,
        "overlay_small_log": True,
        "overlay_large_log": True,
        "overlay_show_only_translated_messages": False,
        "enable_clipboard": True,
        "logger_feature": True,
        "convert_message_to_hiragana": False,
        "convert_message_to_romaji": False,
        "websocket_requested": True,
        "your_languages": (
            LanguageSlotSnapshot("1", "English", "United States", True),
            LanguageSlotSnapshot("2", "Japanese", "Japan", False),
        ),
        "your_translation_languages": (
            LanguageSlotSnapshot("1", "Japanese", "Japan", True),
            LanguageSlotSnapshot("2", "French", "France", True),
            LanguageSlotSnapshot("3", "Thai", "Thailand", False),
        ),
        "target_languages": (
            LanguageSlotSnapshot("1", "Japanese", "Japan", True),
            LanguageSlotSnapshot("2", "French", "France", True),
            LanguageSlotSnapshot("3", "Thai", "Thailand", False),
        ),
        "send_format": _format_snapshot(),
        "received_format": _format_snapshot(),
    }
    values.update(overrides)
    return OutputConfigSnapshot(**values)


def _config_patch(**overrides):
    values = {
        "_SELECTED_TAB_NO": "1",
        "_SELECTED_TRANSLATION_ENGINES": {"1": ["Google", "Bing", "CTranslate2"]},
        "_CTRANSLATE2_WEIGHT_TYPE": "unused",
        "_ENABLE_TRANSLATION": True,
        "_CONVERT_MESSAGE_TO_HIRAGANA": False,
        "_CONVERT_MESSAGE_TO_ROMAJI": False,
        "_SELECTED_YOUR_LANGUAGES": {
            "1": {
                "1": {
                    "enable": True,
                    "language": "English",
                    "country": "United States",
                },
                "2": {"enable": False, "language": "Japanese", "country": "Japan"},
            }
        },
        "_SELECTED_YOUR_TRANSLATION_LANGUAGES": {
            "1": {
                "1": {"enable": True, "language": "Japanese", "country": "Japan"},
                "2": {"enable": True, "language": "French", "country": "France"},
                "3": {"enable": False, "language": "Thai", "country": "Thailand"},
            }
        },
        "_SELECTED_TARGET_LANGUAGES": {
            "1": {
                "1": {"enable": True, "language": "Japanese", "country": "Japan"},
                "2": {"enable": True, "language": "French", "country": "France"},
                "3": {"enable": False, "language": "Thai", "country": "Thailand"},
            }
        },
        "_SEND_MESSAGE_TO_VRC": False,
        "_SEND_RECEIVED_MESSAGE_TO_VRC": False,
        "_SEND_ONLY_TRANSLATED_MESSAGES": False,
        "_OVERLAY_SMALL_LOG": False,
        "_OVERLAY_LARGE_LOG": False,
        "_OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES": False,
        "_ENABLE_CLIPBOARD": False,
        "_LOGGER_FEATURE": False,
        "_WEBSOCKET_SERVER": False,
        "_SEND_MESSAGE_FORMAT_PARTS": {
            "message": {"prefix": "", "suffix": ""},
            "separator": "\n",
            "translation": {"prefix": "", "separator": "\n", "suffix": ""},
            "translation_first": False,
        },
        "_RECEIVED_MESSAGE_FORMAT_PARTS": {
            "message": {"prefix": "", "suffix": ""},
            "separator": "\n",
            "translation": {"prefix": "", "separator": "\n", "suffix": ""},
            "translation_first": False,
        },
        "_ENABLE_TRANSCRIPTION_SEND": True,
        "_ENABLE_TRANSCRIPTION_RECEIVE": True,
    }
    values.update(overrides)
    return patch.multiple(controller_module.config, **values)


class ControllerProgressivePipelineTests(unittest.TestCase):
    def test_mic_initial_event_precedes_translation_and_final_effects(self):
        events = []
        controller = controller_module.Controller()
        controller.run_mapping = {
            "transcription_mic": "/run/transcription_send_mic_message",
            "word_filter": "/run/word_filter",
            "error_device": "/run/error_device",
        }
        controller.run = lambda status, endpoint, payload: events.append(
            ("run", status, endpoint, payload)
        )
        fake_model = Mock()
        fake_model.checkKeywords.return_value = False
        fake_model.detectRepeatSendMessage.return_value = False
        fake_model.getInputTranslate.side_effect = lambda *args, **kwargs: (
            events.append(("translation",)),
            (["translated"], [True]),
        )[1]
        fake_model.getTranslationHistory.return_value = []
        fake_model.transliterateTranscriptionMessage.return_value = ()
        pipeline = _ImmediatePipeline(controller._emitInitialTranscriptionTrace)
        fake_model.getSourcePipeline.return_value = pipeline
        fake_model.getSourcePipelineGeneration.return_value = 0

        with patch.object(controller_module, "model", fake_model), patch.multiple(
            controller_module.config,
            _SELECTED_TAB_NO="1",
            _SELECTED_TRANSLATION_ENGINES={"1": ["Google", "Bing"]},
            _CTRANSLATE2_WEIGHT_TYPE="unused",
            _ENABLE_TRANSLATION=True,
            _CONVERT_MESSAGE_TO_HIRAGANA=False,
            _CONVERT_MESSAGE_TO_ROMAJI=False,
            _SELECTED_YOUR_LANGUAGES={
                "1": {
                    "1": {
                        "enable": True,
                        "language": "English",
                        "country": "United States",
                    }
                }
            },
            _SELECTED_YOUR_TRANSLATION_LANGUAGES={
                "1": {
                    "1": {
                        "enable": True,
                        "language": "Japanese",
                        "country": "Japan",
                    }
                }
            },
            _SELECTED_TARGET_LANGUAGES={
                "1": {
                    "1": {
                        "enable": True,
                        "language": "Japanese",
                        "country": "Japan",
                    }
                }
            },
            _SEND_MESSAGE_TO_VRC=False,
            _SEND_RECEIVED_MESSAGE_TO_VRC=False,
            _SEND_ONLY_TRANSLATED_MESSAGES=False,
            _OVERLAY_SMALL_LOG=False,
            _OVERLAY_LARGE_LOG=False,
            _OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES=False,
            _ENABLE_CLIPBOARD=False,
            _LOGGER_FEATURE=False,
            _WEBSOCKET_SERVER=False,
            _SEND_MESSAGE_FORMAT_PARTS={
                "message": {"prefix": "", "suffix": ""},
                "separator": "\n",
                "translation": {"prefix": "", "separator": "\n", "suffix": ""},
                "translation_first": False,
            },
            _RECEIVED_MESSAGE_FORMAT_PARTS={
                "message": {"prefix": "", "suffix": ""},
                "separator": "\n",
                "translation": {"prefix": "", "separator": "\n", "suffix": ""},
                "translation_first": False,
            },
            _ENABLE_TRANSCRIPTION_SEND=True,
        ):
            controller.micMessage(
                {
                    "text": "recognized",
                    "language": "English",
                    "started_at_monotonic": 123.5,
                }
            )

        self.assertEqual(events[0][0], "run")
        payload = events[0][3]
        self.assertRegex(payload["trace_id"], r"^mic-[0-9a-f-]{36}$")
        self.assertEqual(
            payload,
            {
                "trace_id": payload["trace_id"],
                "original": {"message": "recognized", "transliteration": []},
                "translations": [
                    {
                        "target_slot": "1",
                        "message": None,
                        "transliteration": [],
                        "status": "queued",
                        "engine": "Google",
                        "duration_ms": None,
                    }
                ],
            },
        )
        fake_model.getInputTranslate.assert_not_called()
        self.assertEqual(len(pipeline.traces), 1)

    def test_speaker_initial_event_uses_all_enabled_targets_and_complete_snapshot(self):
        events = []
        controller = controller_module.Controller()
        controller.run_mapping = {
            "transcription_speaker": "/run/transcription_receive_speaker_message",
        }
        controller.run = lambda status, endpoint, payload: events.append(
            (status, endpoint, payload)
        )
        fake_model = Mock()
        fake_model.checkKeywords.return_value = False
        fake_model.detectRepeatReceiveMessage.return_value = False
        fake_model.getTranslationHistory.return_value = [{"source": "mic", "text": "prior"}]
        fake_model.transliterateTranscriptionMessage.return_value = ()
        fake_model.getSourcePipelineGeneration.return_value = 0
        pipeline = _ImmediatePipeline(controller._emitInitialTranscriptionTrace)
        fake_model.getSourcePipeline.return_value = pipeline

        with patch.object(controller_module, "model", fake_model), _config_patch():
            controller.speakerMessage(
                {
                    "text": "heard",
                    "language": "English",
                    "started_at_monotonic": 77.25,
                }
            )

        self.assertEqual(len(events), 1)
        payload = events[0][2]
        self.assertRegex(payload["trace_id"], r"^speaker-[0-9a-f-]{36}$")
        self.assertEqual(
            [(item["target_slot"], item["engine"]) for item in payload["translations"]],
            [("1", "Google"), ("2", "Google")],
        )
        trace = pipeline.traces[0]
        self.assertEqual(trace.providers, ("Google", "Bing"))
        self.assertEqual(trace.started_at_monotonic, 77.25)
        self.assertEqual(trace.context_history, ({"source": "mic", "text": "prior"},))
        self.assertEqual(
            [(target.target_slot, target.country) for target in trace.targets],
            [("1", "Japan"), ("2", "France")],
        )
        self.assertEqual(
            [
                (slot.target_slot, slot.country, slot.enabled)
                for slot in trace.output_config.your_translation_languages
            ],
            [
                ("1", "Japan", True),
                ("2", "France", True),
                ("3", "Thailand", False),
            ],
        )
        fake_model.getOutputTranslate.assert_not_called()

    def test_progressive_and_status_routes_use_exact_serializers(self):
        calls = []
        controller = controller_module.Controller()
        controller.run_mapping = {
            "transcription_translation_update": "/run/transcription_translation_update",
            "pipeline_status": "/run/pipeline_status",
        }
        controller.run = lambda *arguments: calls.append(arguments)
        update = TranslationUpdate(
            trace_id="mic-trace",
            target_slot="2",
            status=TranslationStatus.SUCCESS,
            engine="Bing",
            message="translated",
            transliteration=(),
            duration_ms=12,
            queue_position=0,
            error_code=None,
        )
        status = PipelineStatusEvent(
            schema_version=1,
            trace_id="mic-trace",
            source=PipelineSource.MIC,
            stage="translation",
            engine="Bing",
            target_slot="2",
            outcome="success",
            queue_age_ms=3,
            duration_ms=12,
            queue_depth=0,
            dropped_count=0,
            observed_at_ms=100,
            error_code=None,
        )

        controller._emitTranslationUpdate(update)
        controller._emitPipelineStatus(status)

        self.assertEqual(calls[0], (200, "/run/transcription_translation_update", update.to_payload()))
        self.assertEqual(calls[1], (200, "/run/pipeline_status", status.to_payload()))
        self.assertTrue(
            {"message", "original", "translation", "text"}.isdisjoint(calls[1][2])
        )

    def _effect_model(self):
        fake_model = Mock()
        fake_model.isSourcePipelineGenerationCurrent.return_value = True
        fake_model.checkWebSocketServerAlive.return_value = True
        fake_model.createOverlayImageLargeLog.return_value = "large-image"
        fake_model.createOverlayImageSmallLog.return_value = "small-image"
        fake_model.logger = Mock()
        return fake_model

    def test_mic_finalizer_preserves_partial_success_effects_and_slot_alignment(self):
        controller = controller_module.Controller()
        controller.run_mapping = {"error_translation_engine": "/run/error_translation_engine"}
        controller.run = Mock()
        controller._is_overlay_available = Mock(return_value=True)
        fake_model = self._effect_model()
        task = FinalOutputTask(
            trace_id="mic-trace",
            generation=0,
            source=PipelineSource.MIC,
            original_message="spoken",
            source_language="English",
            original_transliteration=(),
            targets=(
                TranslationTarget("1", "Japanese", "Japan"),
                TranslationTarget("2", "French", "France"),
            ),
            translations=(
                TranslationUpdate(
                    "mic-trace", "1", TranslationStatus.ERROR, "Google", None, (), 10, 0, "failed"
                ),
                TranslationUpdate(
                    "mic-trace",
                    "2",
                    TranslationStatus.SUCCESS,
                    "Bing",
                    "deux",
                    (),
                    11,
                    0,
                    None,
                ),
            ),
            output_config=_output_snapshot(),
            started_at_monotonic=1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._finalizeMicOutput(task)

        fake_model.oscSendMessage.assert_called_once_with("<m>spoken</m> | <t>deux</t>")
        fake_model.setCopyToClipboardAndPasteFromClipboard.assert_called_once_with(
            "<m>spoken</m> | <t>deux</t>"
        )
        overlay_args = fake_model.createOverlayImageLargeLog.call_args.args
        self.assertEqual(overlay_args[3], ["deux"])
        self.assertEqual(
            overlay_args[4],
            {"2": {"language": "French", "country": "France", "enable": True}},
        )
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(
            websocket_payload["src_languages"],
            {
                "1": {"language": "English", "country": "United States", "enable": True},
                "2": {"language": "Japanese", "country": "Japan", "enable": False},
            },
        )
        self.assertEqual(websocket_payload["dst_languages"], overlay_args[4])
        self.assertEqual(websocket_payload["translation"], ["deux"])
        self.assertEqual(
            [call.args[0] for call in fake_model.telemetryTrackCoreFeature.call_args_list],
            ["mic_speech_to_text", "translation"],
        )
        fake_model.logger.info.assert_called_once_with("[SENT] spoken (deux)")
        fake_model.addTranslationHistory.assert_called_once_with("mic", "spoken")
        controller.run.assert_called_once()
        self.assertEqual(controller.run.call_args.args[1], "/run/error_translation_engine")

    def test_speaker_failure_keeps_original_only_metadata_without_translated_effects(self):
        controller = controller_module.Controller()
        controller.run_mapping = {"error_translation_engine": "/run/error_translation_engine"}
        controller.run = Mock()
        controller._is_overlay_available = Mock(return_value=True)
        fake_model = self._effect_model()
        task = FinalOutputTask(
            trace_id="speaker-trace",
            generation=0,
            source=PipelineSource.SPEAKER,
            original_message="heard",
            source_language="English",
            original_transliteration=(),
            targets=(TranslationTarget("1", "Japanese", "Japan"),),
            translations=(
                TranslationUpdate(
                    "speaker-trace",
                    "1",
                    TranslationStatus.TIMEOUT,
                    "Google",
                    None,
                    (),
                    5000,
                    0,
                    "provider_timeout",
                ),
            ),
            output_config=_output_snapshot(
                send_only_translated_messages=True,
                overlay_show_only_translated_messages=True,
            ),
            started_at_monotonic=1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._finalizeSpeakerOutput(task)

        fake_model.oscSendMessage.assert_not_called()
        fake_model.createOverlayImageSmallLog.assert_not_called()
        fake_model.createOverlayImageLargeLog.assert_not_called()
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], [])
        self.assertEqual(
            websocket_payload["src_languages"]["3"],
            {"language": "Thai", "country": "Thailand", "enable": False},
        )
        fake_model.logger.info.assert_called_once_with("[RECEIVED] heard")
        fake_model.addTranslationHistory.assert_called_once_with("speaker", "heard")
        self.assertEqual(controller.run.call_args.args[1], "/run/error_translation_engine")

    def test_translation_disabled_preserves_send_only_original_rule(self):
        controller = controller_module.Controller()
        controller.run = Mock()
        controller._is_overlay_available = Mock(return_value=True)
        fake_model = self._effect_model()
        task = FinalOutputTask(
            trace_id="speaker-original",
            generation=0,
            source=PipelineSource.SPEAKER,
            original_message="heard",
            source_language="English",
            original_transliteration=(),
            targets=(),
            translations=(),
            output_config=_output_snapshot(
                translation_enabled=False,
                send_only_translated_messages=True,
                overlay_show_only_translated_messages=False,
            ),
            started_at_monotonic=1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._finalizeSpeakerOutput(task)

        fake_model.oscSendMessage.assert_called_once_with("<m>heard</m>")
        self.assertEqual(
            [call.args[0] for call in fake_model.telemetryTrackCoreFeature.call_args_list],
            ["speaker_speech_to_text"],
        )
        controller.run.assert_not_called()

    def test_stale_generation_suppresses_every_final_effect(self):
        controller = controller_module.Controller()
        controller.run = Mock()
        fake_model = self._effect_model()
        fake_model.isSourcePipelineGenerationCurrent.return_value = False
        task = FinalOutputTask(
            "mic-stale",
            0,
            PipelineSource.MIC,
            "spoken",
            "English",
            (),
            (),
            (),
            _output_snapshot(),
            1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._finalizeMicOutput(task)

        fake_model.telemetryTrackCoreFeature.assert_not_called()
        fake_model.oscSendMessage.assert_not_called()
        fake_model.updateOverlayLargeLog.assert_not_called()
        fake_model.websocketSendMessage.assert_not_called()
        fake_model.addTranslationHistory.assert_not_called()
        controller.run.assert_not_called()

    def test_source_pipeline_is_ensured_before_recorder_callback(self):
        controller = controller_module.Controller()
        events = []
        fake_model = Mock()

        def ensure(source, callbacks, generation):
            events.append(("ensure", source, generation, callbacks))

        def start_mic(callback):
            self.assertEqual(events[0][0], "ensure")
            events.append(("callback", PipelineSource.MIC))
            callback({"text": "", "language": "English"})

        def start_speaker(callback):
            self.assertEqual(events[-1][0], "ensure")
            events.append(("callback", PipelineSource.SPEAKER))
            callback({"text": "", "language": "English"})

        fake_model.ensureSourcePipeline.side_effect = ensure
        fake_model.startMicTranscript.side_effect = start_mic
        fake_model.startSpeakerTranscript.side_effect = start_speaker

        with patch.object(controller_module, "model", fake_model):
            controller.startTranscriptionSendMessage()
            controller.startTranscriptionReceiveMessage()

        self.assertEqual(
            [(event[0], event[1]) for event in events],
            [
                ("ensure", PipelineSource.MIC),
                ("callback", PipelineSource.MIC),
                ("ensure", PipelineSource.SPEAKER),
                ("callback", PipelineSource.SPEAKER),
            ],
        )

    def test_model_owns_started_pipeline_and_stops_matching_source(self):
        instances = []

        class FakeSourcePipeline:
            def __init__(self, **arguments):
                self.arguments = arguments
                self.started = []
                self.stopped = []
                instances.append(self)

            def start(self, generation):
                self.started.append(generation)

            def stop(self, generation, discard_pending=True):
                self.stopped.append((generation, discard_pending))

        instance = object.__new__(model_module.Model)
        instance._inited = True
        instance.translator = object()
        instance.mic_source_pipeline = None
        instance.speaker_source_pipeline = None
        instance._source_pipeline_generations = {}
        callbacks = {
            "emit_initial": Mock(),
            "emit_update": Mock(),
            "emit_metric": Mock(),
            "emit_final": Mock(),
        }

        with patch.object(model_module, "SourcePipeline", FakeSourcePipeline):
            pipeline = instance.ensureSourcePipeline(PipelineSource.MIC, callbacks, 4)
            self.assertIs(instance.getSourcePipeline(PipelineSource.MIC), pipeline)
            self.assertEqual(pipeline.started, [4])
            self.assertTrue(instance.isSourcePipelineGenerationCurrent(PipelineSource.MIC, 4))
            instance.stopSourcePipeline(PipelineSource.MIC)

        self.assertEqual(pipeline.stopped, [(4, True)])
        self.assertIsNone(instance.getSourcePipeline(PipelineSource.MIC))
        self.assertFalse(instance.isSourcePipelineGenerationCurrent(PipelineSource.MIC, 4))


if __name__ == "__main__":
    unittest.main()
