import os
import sys
import unittest
from threading import Event
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
    TranscriptionTrace,
    TranslationStatus,
    TranslationTarget,
    TranslationUpdate,
)
from models.pipeline.source_pipeline import SourcePipeline


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
                        "language": "Japanese",
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
            [
                (item["target_slot"], item["language"], item["engine"])
                for item in payload["translations"]
            ],
            [("1", "Japanese", "Google"), ("2", "French", "Google")],
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

    @staticmethod
    def _trace_for_task(task):
        return TranscriptionTrace(
            trace_id=task.trace_id,
            generation=task.generation,
            source=task.source,
            original_message=task.original_message,
            source_language=task.source_language,
            original_transliteration=task.original_transliteration,
            targets=task.targets,
            providers=("Google",),
            ctranslate2_weight_type="unused",
            context_history=(),
            started_at_monotonic=task.started_at_monotonic,
            output_config=task.output_config,
        )

    def test_mic_finalizer_isolates_first_telemetry_failure_and_raises_after_all_sinks(self):
        events = []
        controller = controller_module.Controller()
        controller.run_mapping = {
            "transcription_mic": "/transcription/mic",
            "error_translation_engine": "/translation/error",
        }

        def run(status, endpoint, payload):
            events.append(
                "source"
                if endpoint == "/transcription/mic"
                else "translation_error"
            )

        controller.run = run
        controller._is_overlay_available = lambda: events.append("overlay_available") or True
        fake_model = self._effect_model()

        def telemetry(feature):
            events.append(f"telemetry:{feature}")
            if feature == "mic_speech_to_text":
                raise RuntimeError("sensitive telemetry detail")

        fake_model.telemetryTrackCoreFeature.side_effect = telemetry
        fake_model.oscSendMessage.side_effect = lambda message: events.append("osc")
        fake_model.createOverlayImageLargeLog.side_effect = (
            lambda *args: events.append("overlay_render") or "image"
        )
        fake_model.updateOverlayLargeLog.side_effect = lambda image: events.append("overlay_update")
        fake_model.setCopyToClipboardAndPasteFromClipboard.side_effect = (
            lambda message: events.append("clipboard")
        )
        fake_model.checkWebSocketServerAlive.side_effect = (
            lambda: events.append("websocket_alive") or True
        )
        fake_model.websocketSendMessage.side_effect = lambda payload: events.append("websocket")
        fake_model.logger.info.side_effect = lambda message: events.append("logger")
        fake_model.addTranslationHistory.side_effect = lambda *args: events.append("history")
        task = FinalOutputTask(
            "mic-isolation",
            0,
            PipelineSource.MIC,
            "spoken",
            "English",
            (),
            (
                TranslationTarget("1", "Japanese", "Japan"),
                TranslationTarget("2", "French", "France"),
            ),
            (
                TranslationUpdate(
                    "mic-isolation", "1", TranslationStatus.ERROR, "Google", None, (), 1, 0, "failed"
                ),
                TranslationUpdate(
                    "mic-isolation", "2", TranslationStatus.SUCCESS, "Google", "deux", (), 1, 0, None
                ),
            ),
            _output_snapshot(),
            1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._emitInitialTranscriptionTrace(self._trace_for_task(task))
            with self.assertRaisesRegex(RuntimeError, "final output sinks failed: telemetry") as raised:
                controller._finalizeMicOutput(task)

        self.assertNotIn("sensitive telemetry detail", str(raised.exception))
        self.assertEqual(
            events,
            [
                "source",
                "telemetry:mic_speech_to_text",
                "telemetry:translation",
                "translation_error",
                "osc",
                "overlay_available",
                "overlay_render",
                "overlay_update",
                "clipboard",
                "websocket_alive",
                "websocket",
                "logger",
                "history",
            ],
        )

    def test_speaker_finalizer_isolates_mid_overlay_failure_and_continues(self):
        events = []
        controller = controller_module.Controller()
        controller.run_mapping = {"transcription_speaker": "/transcription/speaker"}
        controller.run = lambda status, endpoint, payload: events.append("source")
        controller._is_overlay_available = lambda: events.append("overlay_available") or True
        fake_model = self._effect_model()
        fake_model.telemetryTrackCoreFeature.side_effect = (
            lambda feature: events.append(f"telemetry:{feature}")
        )
        fake_model.createOverlayImageSmallLog.side_effect = (
            lambda *args: events.append("small_render") or "small"
        )

        def fail_small_update(image):
            events.append("small_update")
            raise RuntimeError("overlay driver detail")

        fake_model.updateOverlaySmallLog.side_effect = fail_small_update
        fake_model.createOverlayImageLargeLog.side_effect = (
            lambda *args: events.append("large_render") or "large"
        )
        fake_model.updateOverlayLargeLog.side_effect = lambda image: events.append("large_update")
        fake_model.oscSendMessage.side_effect = lambda message: events.append("osc")
        fake_model.checkWebSocketServerAlive.side_effect = (
            lambda: events.append("websocket_alive") or True
        )
        fake_model.websocketSendMessage.side_effect = lambda payload: events.append("websocket")
        fake_model.logger.info.side_effect = lambda message: events.append("logger")
        fake_model.addTranslationHistory.side_effect = lambda *args: events.append("history")
        task = FinalOutputTask(
            "speaker-isolation",
            0,
            PipelineSource.SPEAKER,
            "heard",
            "English",
            (),
            (TranslationTarget("1", "Japanese", "Japan"),),
            (
                TranslationUpdate(
                    "speaker-isolation", "1", TranslationStatus.SUCCESS, "Google", "ichi", (), 1, 0, None
                ),
            ),
            _output_snapshot(enable_clipboard=False),
            1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._emitInitialTranscriptionTrace(self._trace_for_task(task))
            with self.assertRaisesRegex(RuntimeError, "final output sinks failed: overlay_small"):
                controller._finalizeSpeakerOutput(task)

        self.assertEqual(
            events,
            [
                "source",
                "telemetry:speaker_speech_to_text",
                "telemetry:translation",
                "overlay_available",
                "small_render",
                "small_update",
                "overlay_available",
                "large_render",
                "large_update",
                "osc",
                "websocket_alive",
                "websocket",
                "logger",
                "history",
            ],
        )

    def test_source_pipeline_reports_one_output_error_after_isolated_sink_failure(self):
        output_error = Event()
        metrics = []
        controller = controller_module.Controller()
        fake_model = self._effect_model()
        fake_model.telemetryTrackCoreFeature.side_effect = RuntimeError("telemetry detail")

        def metric(event):
            metrics.append(event)
            if event.stage == "output" and event.outcome == "error":
                output_error.set()

        pipeline = SourcePipeline(
            source=PipelineSource.MIC,
            translator=Mock(),
            transliterate=lambda *args: (),
            emit_initial=lambda trace: None,
            emit_update=lambda update: None,
            emit_metric=metric,
            emit_final=controller._finalizeMicOutput,
            is_generation_current=lambda generation: True,
        )
        trace = TranscriptionTrace(
            "mic-output-error",
            0,
            PipelineSource.MIC,
            "spoken",
            "English",
            (),
            (),
            (),
            "unused",
            (),
            1.0,
            _output_snapshot(
                translation_enabled=False,
                send_message_to_vrc=False,
                overlay_large_log=False,
                enable_clipboard=False,
                logger_feature=False,
                websocket_requested=False,
            ),
        )

        with patch.object(controller_module, "model", fake_model):
            pipeline.start(0)
            try:
                self.assertTrue(pipeline.submit_trace(trace))
                self.assertTrue(output_error.wait(2.0))
            finally:
                pipeline.stop(0)

        outcomes = [event.outcome for event in metrics if event.stage == "output"]
        self.assertEqual(outcomes, ["running", "error"])
        fake_model.addTranslationHistory.assert_called_once_with("mic", "spoken")
        self.assertNotIn(trace.trace_id, pipeline._records)

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
        self.assertEqual(overlay_args[7], ["2"])
        self.assertEqual(
            overlay_args[4],
            {
                "1": {"language": "Japanese", "country": "Japan", "enable": True},
                "2": {"language": "French", "country": "France", "enable": True},
                "3": {"language": "Thai", "country": "Thailand", "enable": False},
            },
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
        self.assertEqual(websocket_payload["translation_target_slots"], ["2"])
        self.assertEqual(
            [call.args[0] for call in fake_model.telemetryTrackCoreFeature.call_args_list],
            ["mic_speech_to_text", "translation"],
        )
        fake_model.logger.info.assert_called_once_with("[SENT] spoken (deux)")
        fake_model.addTranslationHistory.assert_called_once_with("mic", "spoken")
        controller.run.assert_called_once()
        self.assertEqual(controller.run.call_args.args[1], "/run/error_translation_engine")

    def test_speaker_partial_success_keeps_complete_overlay_and_websocket_language_maps(self):
        controller = controller_module.Controller()
        controller.run_mapping = {
            "error_translation_engine": "/run/error_translation_engine"
        }
        controller.run = Mock()
        controller._is_overlay_available = Mock(return_value=True)
        fake_model = self._effect_model()
        transliteration = ({"text": "deux", "reading": "deu"},)
        task = FinalOutputTask(
            trace_id="speaker-partial",
            generation=0,
            source=PipelineSource.SPEAKER,
            original_message="heard",
            source_language="English",
            original_transliteration=(),
            targets=(
                TranslationTarget("1", "Japanese", "Japan"),
                TranslationTarget("2", "French", "France"),
            ),
            translations=(
                TranslationUpdate(
                    "speaker-partial",
                    "2",
                    TranslationStatus.SUCCESS,
                    "Bing",
                    "deux",
                    transliteration,
                    11,
                    0,
                    None,
                ),
                TranslationUpdate(
                    "speaker-partial",
                    "1",
                    TranslationStatus.ERROR,
                    "Google",
                    None,
                    (),
                    10,
                    0,
                    "failed",
                ),
            ),
            output_config=_output_snapshot(),
            started_at_monotonic=1.0,
        )

        with patch.object(controller_module, "model", fake_model):
            controller._finalizeSpeakerOutput(task)

        complete_destinations = {
            "1": {"language": "Japanese", "country": "Japan", "enable": True},
            "2": {"language": "French", "country": "France", "enable": True},
            "3": {"language": "Thai", "country": "Thailand", "enable": False},
        }
        small_overlay_args = fake_model.createOverlayImageSmallLog.call_args.args
        self.assertEqual(small_overlay_args[2], ["deux"])
        self.assertEqual(small_overlay_args[3], complete_destinations)
        self.assertEqual(small_overlay_args[5], [list(transliteration)])
        self.assertEqual(small_overlay_args[6], ["2"])
        large_overlay_args = fake_model.createOverlayImageLargeLog.call_args.args
        self.assertEqual(large_overlay_args[3], ["deux"])
        self.assertEqual(large_overlay_args[4], complete_destinations)
        self.assertEqual(large_overlay_args[6], [list(transliteration)])
        self.assertEqual(large_overlay_args[7], ["2"])
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["dst_languages"], complete_destinations)
        self.assertEqual(websocket_payload["translation"], ["deux"])
        self.assertEqual(websocket_payload["translation_target_slots"], ["2"])
        self.assertEqual(
            websocket_payload["transliteration"],
            [list(transliteration)],
        )
        self.assertEqual(
            websocket_payload["src_languages"]["3"],
            {"language": "Thai", "country": "Thailand", "enable": False},
        )

    def test_overlay_adapters_pair_compact_translations_with_explicit_target_slots(self):
        instance = object.__new__(model_module.Model)
        instance._inited = True
        instance.overlay_image = Mock()
        destinations = {
            "1": {"language": "Japanese", "country": "Japan", "enable": True},
            "2": {"language": "French", "country": "France", "enable": True},
            "3": {"language": "Thai", "country": "Thailand", "enable": False},
        }

        instance.createOverlayImageSmallLog(
            "heard",
            "English",
            ["deux"],
            destinations,
            [],
            [[]],
            ["2"],
        )
        small_args = instance.overlay_image.createOverlayImageSmallLog.call_args.args
        self.assertEqual(small_args[2], ["deux"])
        self.assertEqual(small_args[3], ["French"])

        instance.createOverlayImageLargeLog(
            "receive",
            "heard",
            "English",
            ["deux"],
            destinations,
            [],
            [[]],
            ["2"],
        )
        large_args = instance.overlay_image.createOverlayImageLargeLog.call_args.args
        self.assertEqual(large_args[3], ["deux"])
        self.assertEqual(large_args[4], ["French"])

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
            return True

        def start_speaker(callback):
            self.assertEqual(events[-1][0], "ensure")
            events.append(("callback", PipelineSource.SPEAKER))
            callback({"text": "", "language": "English"})
            return True

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

    def test_controller_rolls_back_ensured_pipeline_on_nonstart_and_exception(self):
        for source in (PipelineSource.MIC, PipelineSource.SPEAKER):
            for outcome in (False, RuntimeError("start failed"), True):
                with self.subTest(source=source.value, outcome=repr(outcome)):
                    events = []
                    controller = controller_module.Controller()
                    controller.run_mapping = {
                        "enable_transcription_send": "/enable/mic",
                        "enable_transcription_receive": "/enable/speaker",
                    }
                    controller.run = Mock()
                    fake_model = Mock()
                    fake_model.ensureSourcePipeline.side_effect = (
                        lambda candidate, callbacks, generation: events.append(
                            ("ensure", candidate)
                        )
                    )

                    def start(callback):
                        events.append(("start", source))
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome

                    if source is PipelineSource.MIC:
                        fake_model.startMicTranscript.side_effect = start
                    else:
                        fake_model.startSpeakerTranscript.side_effect = start
                    fake_model.stopSourcePipeline.side_effect = (
                        lambda candidate: events.append(("stop", candidate))
                    )
                    fake_model.detectVRAMError.return_value = (False, None)

                    with patch.object(controller_module, "model", fake_model), _config_patch():
                        if isinstance(outcome, Exception):
                            with self.assertRaisesRegex(RuntimeError, "start failed"):
                                if source is PipelineSource.MIC:
                                    controller.startTranscriptionSendMessage()
                                else:
                                    controller.startTranscriptionReceiveMessage()
                        elif source is PipelineSource.MIC:
                            controller.startTranscriptionSendMessage()
                        else:
                            controller.startTranscriptionReceiveMessage()

                    expected = [("ensure", source), ("start", source)]
                    if outcome is not True:
                        expected.append(("stop", source))
                    self.assertEqual(events, expected)
                    self.assertEqual(
                        fake_model.stopSourcePipeline.call_count,
                        0 if outcome is True else 1,
                    )

    def test_model_disabled_starts_return_false_and_missing_devices_raise_without_workers(self):
        instance = object.__new__(model_module.Model)
        instance._inited = True
        instance.mic_print_transcript = None
        instance.mic_audio_recorder = None
        instance.mic_whisper_runtime_lease = None
        instance.speaker_print_transcript = None
        instance.speaker_audio_recorder = None
        instance.speaker_whisper_runtime_lease = None
        instance.mic_source_pipeline = None
        instance.speaker_source_pipeline = None
        instance._source_pipeline_generations = {}

        with patch.multiple(
            model_module.config,
            _ENABLE_TRANSCRIPTION_SEND=False,
            _ENABLE_TRANSCRIPTION_RECEIVE=False,
        ):
            self.assertIs(instance.startMicTranscript(Mock()), False)
            self.assertIs(instance.startSpeakerTranscript(Mock()), False)

        mic_results = []
        speaker_results = []
        with patch.multiple(
            model_module.config,
            _ENABLE_TRANSCRIPTION_SEND=True,
            _ENABLE_TRANSCRIPTION_RECEIVE=True,
            _SELECTED_MIC_HOST="host",
            _SELECTED_MIC_DEVICE="NoDevice",
            _SELECTED_SPEAKER_DEVICE="NoDevice",
        ), patch.object(
            model_module.device_manager,
            "getMicDevices",
            return_value={"host": [{"name": "NoDevice"}]},
        ), patch.object(
            model_module.device_manager,
            "getSpeakerDevices",
            return_value=[{"name": "NoDevice"}],
        ):
            with self.assertRaises(model_module.DeviceUnavailableError) as mic_error:
                instance.startMicTranscript(mic_results.append)
            with self.assertRaises(model_module.DeviceUnavailableError) as speaker_error:
                instance.startSpeakerTranscript(speaker_results.append)

        self.assertEqual(
            mic_error.exception.error_code,
            model_module.ErrorCode.DEVICE_NO_MIC,
        )
        self.assertEqual(
            speaker_error.exception.error_code,
            model_module.ErrorCode.DEVICE_NO_SPEAKER,
        )
        self.assertEqual(mic_results, [])
        self.assertEqual(speaker_results, [])
        self.assertIsNone(instance.mic_print_transcript)
        self.assertIsNone(instance.speaker_print_transcript)
        self.assertIsNone(instance.mic_source_pipeline)
        self.assertIsNone(instance.speaker_source_pipeline)

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
            instance.stopSourcePipeline(PipelineSource.MIC)
            replacement = instance.ensureSourcePipeline(PipelineSource.MIC, callbacks, 4)
            instance.stopSourcePipeline(PipelineSource.MIC)
            instance.stopSourcePipeline(PipelineSource.MIC)

        self.assertEqual(pipeline.stopped, [(4, True)])
        self.assertIsNot(replacement, pipeline)
        self.assertEqual(replacement.started, [4])
        self.assertEqual(replacement.stopped, [(4, True)])
        self.assertIsNone(instance.getSourcePipeline(PipelineSource.MIC))
        self.assertFalse(instance.isSourcePipelineGenerationCurrent(PipelineSource.MIC, 4))


if __name__ == "__main__":
    unittest.main()
