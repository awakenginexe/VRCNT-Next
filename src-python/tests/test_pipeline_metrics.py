import os
import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import (
    AudioChunk,
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
import controller as controller_module
import model as model_module
from model import Model, _MetricAudioQueue


def make_format() -> MessageFormatSnapshot:
    return MessageFormatSnapshot(
        message_prefix="<",
        message_suffix=">",
        translation_prefix="[",
        translation_suffix="]",
        translation_separator=" / ",
        message_translation_separator=" | ",
        translation_first=False,
    )


def make_output_config() -> OutputConfigSnapshot:
    return OutputConfigSnapshot(
        selected_tab_no="1",
        translation_enabled=True,
        send_message_to_vrc=True,
        send_received_message_to_vrc=False,
        send_only_translated_messages=False,
        overlay_small_log=True,
        overlay_large_log=False,
        overlay_show_only_translated_messages=False,
        enable_clipboard=True,
        logger_feature=False,
        convert_message_to_hiragana=False,
        convert_message_to_romaji=True,
        websocket_requested=True,
        your_languages=(
            LanguageSlotSnapshot("your-1", "English", "United States", True),
            LanguageSlotSnapshot("your-2", "Japanese", "Japan", False),
        ),
        your_translation_languages=(
            LanguageSlotSnapshot("your-translation-1", "Thai", "Thailand", False),
        ),
        target_languages=(
            LanguageSlotSnapshot("target-1", "French", "France", True),
            LanguageSlotSnapshot("target-2", "German", "Germany", False),
        ),
        send_format=make_format(),
        received_format=make_format(),
    )


class PipelineMetricsTests(unittest.TestCase):
    @staticmethod
    def _source_metric(
        stage: str,
        outcome: str,
        *,
        observed_at_ms: int = 1,
    ) -> PipelineStatusEvent:
        return PipelineStatusEvent(
            schema_version=1,
            trace_id=None,
            source=PipelineSource.MIC,
            stage=stage,
            engine="Whisper" if stage == "transcription" else None,
            target_slot=None,
            outcome=outcome,
            queue_age_ms=None,
            duration_ms=None,
            queue_depth=0,
            dropped_count=0,
            observed_at_ms=observed_at_ms,
            error_code=None,
        )

    def test_controller_registration_forwards_source_metrics_to_status_route(self):
        instance = object.__new__(Model)
        events = []

        with patch.object(controller_module, "model", instance):
            controller = controller_module.Controller()
            controller.setRunMapping({"pipeline_status": "/run/pipeline_status"})
            controller.setRun(
                lambda status, endpoint, payload: events.append(
                    (status, endpoint, payload)
                )
            )
            try:
                instance._emitTranscriptionLifecycleMetric(
                    PipelineSource.MIC,
                    stage="capture",
                    outcome="running",
                )
                queue = _MetricAudioQueue(
                    PipelineSource.MIC,
                    instance._emitTranscriptionLifecycleMetric,
                )
                queue.offer(
                    AudioChunk(
                        b"audio",
                        datetime(2026, 7, 13, tzinfo=timezone.utc),
                        1.0,
                    )
                )
                context = instance._makeTranscriberPipelineContext(
                    PipelineSource.MIC,
                    None,
                    generation=1,
                )
                context.emit_metric(
                    self._source_metric("transcription", "running")
                )
            finally:
                controller.shutdown()
        instance._recordTranscriptionPipelineMetric(
            self._source_metric("capture", "recovered", observed_at_ms=2)
        )

        self.assertEqual(
            [(status, endpoint) for status, endpoint, _payload in events],
            [
                (200, "/run/pipeline_status"),
                (200, "/run/pipeline_status"),
                (200, "/run/pipeline_status"),
            ],
        )
        self.assertEqual(
            [payload["stage"] for _status, _endpoint, payload in events],
            ["capture", "queue", "transcription"],
        )
        for _status, _endpoint, payload in events:
            self.assertTrue(
                {"message", "original", "translation", "text"}.isdisjoint(
                    payload
                )
            )

    def test_metric_callback_failure_cannot_break_capture_or_transcriber_paths(self):
        instance = object.__new__(Model)
        instance.setTranscriptionPipelineMetricCallback(
            lambda _event: (_ for _ in ()).throw(
                RuntimeError("controlled status transport failure")
            )
        )
        queue = _MetricAudioQueue(
            PipelineSource.SPEAKER,
            instance._emitTranscriptionLifecycleMetric,
        )

        with patch.object(model_module, "errorLogging"):
            offer = queue.offer(
                AudioChunk(
                    b"audio",
                    datetime(2026, 7, 13, tzinfo=timezone.utc),
                    1.0,
                )
            )
            context = instance._makeTranscriberPipelineContext(
                PipelineSource.SPEAKER,
                None,
                generation=1,
            )
            context.emit_metric(
                self._source_metric("transcription", "success")
            )

        self.assertTrue(offer.accepted)
        self.assertEqual(
            [event.stage for event in instance.transcription_pipeline_metrics],
            ["queue", "transcription"],
        )

    def test_controller_shutdown_unregisters_only_its_metric_callback(self):
        instance = object.__new__(Model)
        events = []
        replacement_events = []

        with patch.object(controller_module, "model", instance):
            controller = controller_module.Controller()
            controller.setRun(
                lambda status, endpoint, payload: events.append(payload)
            )
            instance._recordTranscriptionPipelineMetric(
                self._source_metric("capture", "running")
            )
            self.assertEqual(len(events), 1)
            instance.setTranscriptionPipelineMetricCallback(
                replacement_events.append
            )

            self.assertEqual(
                controller.shutdown(),
                {"status": 200, "result": True},
            )
            self.assertEqual(
                controller.shutdown(),
                {"status": 200, "result": True},
            )
            instance._recordTranscriptionPipelineMetric(
                self._source_metric("capture", "recovered", observed_at_ms=2)
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(len(replacement_events), 1)

    def test_production_metric_retention_is_bounded_and_keeps_newest_events(self):
        instance = object.__new__(Model)
        instance._recordTranscriptionPipelineMetric(
            self._source_metric("capture", "running", observed_at_ms=0)
        )
        retention_limit = getattr(
            instance.transcription_pipeline_metrics,
            "maxlen",
            None,
        )
        self.assertIsNotNone(retention_limit)

        for observed_at_ms in range(1, retention_limit + 4):
            instance._recordTranscriptionPipelineMetric(
                self._source_metric(
                    "capture",
                    "running",
                    observed_at_ms=observed_at_ms,
                )
            )

        self.assertEqual(
            len(instance.transcription_pipeline_metrics),
            retention_limit,
        )
        self.assertEqual(
            instance.transcription_pipeline_metrics[-1].observed_at_ms,
            retention_limit + 3,
        )
        self.assertGreater(
            instance.transcription_pipeline_metrics[0].observed_at_ms,
            0,
        )

    def test_lifecycle_metric_matrix_uses_null_traces_and_never_emits_text(self):
        instance = object.__new__(Model)
        instance.transcription_pipeline_metrics = []
        queue = _MetricAudioQueue(
            PipelineSource.MIC,
            instance._emitTranscriptionLifecycleMetric,
        )
        spoken_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
        for index in range(5):
            result = queue.offer(AudioChunk(bytes([index]), spoken_at, float(index)))
            if result.dropped is not None:
                queue.record_drop()

        instance._emitTranscriptionLifecycleMetric(
            PipelineSource.MIC,
            stage="capture",
            outcome="running",
        )
        instance._emitTranscriptionLifecycleMetric(
            PipelineSource.SPEAKER,
            stage="capture",
            outcome="error",
            error_code="recorder_construction_failed",
        )
        instance._emitTranscriptionLifecycleMetric(
            PipelineSource.MIC,
            stage="queue",
            outcome="success",
        )
        instance._emitTranscriptionLifecycleMetric(
            PipelineSource.MIC,
            stage="capture",
            outcome="recovered",
        )
        instance.recordTranscriptionRecovery(
            PipelineSource.SPEAKER,
            "whisper_inference_failed",
        )
        instance.recordTranscriptionRecoveryFailure(
            PipelineSource.MIC,
            "whisper_inference_failed",
        )

        matrix = {
            (event.source, event.stage, event.outcome)
            for event in instance.transcription_pipeline_metrics
        }
        self.assertTrue(
            {
                (PipelineSource.MIC, "capture", "running"),
                (PipelineSource.SPEAKER, "capture", "error"),
                (PipelineSource.MIC, "queue", "waiting"),
                (PipelineSource.MIC, "queue", "success"),
                (PipelineSource.MIC, "queue", "skipped_overload"),
                (PipelineSource.MIC, "capture", "recovered"),
                (PipelineSource.SPEAKER, "transcription", "recovered"),
                (PipelineSource.MIC, "transcription", "error"),
            }.issubset(matrix)
        )
        overload = next(
            event
            for event in instance.transcription_pipeline_metrics
            if event.outcome == "skipped_overload"
        )
        self.assertEqual(overload.queue_depth, 4)
        self.assertEqual(overload.dropped_count, 1)
        recovery_failure = next(
            event
            for event in instance.transcription_pipeline_metrics
            if event.stage == "transcription"
            and event.outcome == "error"
            and event.error_code == "recovery_failed"
        )
        self.assertEqual(recovery_failure.source, PipelineSource.MIC)
        for event in instance.transcription_pipeline_metrics:
            payload = event.to_payload()
            self.assertIsNone(payload["trace_id"])
            self.assertTrue(
                {"message", "original", "translation", "text"}.isdisjoint(
                    payload
                )
            )

    def test_pipeline_enums_have_exact_members_and_values(self):
        self.assertEqual(
            {
                name: member.value
                for name, member in PipelineSource.__members__.items()
            },
            {"MIC": "mic", "SPEAKER": "speaker"},
        )
        self.assertEqual(
            {
                name: member.value
                for name, member in TranslationStatus.__members__.items()
            },
            {
                "QUEUED": "queued",
                "SENDING": "sending",
                "FALLBACK": "fallback",
                "SUCCESS": "success",
                "TIMEOUT": "timeout",
                "ERROR": "error",
                "SKIPPED_OVERLOAD": "skipped_overload",
            },
        )

    def test_status_payload_emits_complete_schema_without_transcript_text(self):
        event = PipelineStatusEvent(
            schema_version=1,
            trace_id="trace-1",
            source=PipelineSource.MIC,
            stage="translation",
            engine="DeepL",
            target_slot="target-1",
            outcome="success",
            queue_age_ms=12,
            duration_ms=34,
            queue_depth=2,
            dropped_count=1,
            observed_at_ms=1_700_000_000_000,
            error_code=None,
        )

        payload = event.to_payload()

        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "trace_id": "trace-1",
                "source": "mic",
                "stage": "translation",
                "engine": "DeepL",
                "target_slot": "target-1",
                "outcome": "success",
                "queue_age_ms": 12,
                "duration_ms": 34,
                "queue_depth": 2,
                "dropped_count": 1,
                "observed_at_ms": 1_700_000_000_000,
                "error_code": None,
            },
        )
        self.assertTrue(
            {"message", "original", "translation", "text"}.isdisjoint(payload)
        )

    def test_translation_update_payload_exposes_only_progressive_schema(self):
        update = TranslationUpdate(
            trace_id="trace-1",
            target_slot="target-2",
            status=TranslationStatus.SUCCESS,
            engine="CTranslate2",
            message="bonjour",
            transliteration=({"text": "bonjour", "reading": "bonjour"},),
            duration_ms=45,
            queue_position=0,
            error_code=None,
        )

        payload = update.to_payload()

        self.assertEqual(
            payload,
            {
                "trace_id": "trace-1",
                "target_slot": "target-2",
                "status": "success",
                "engine": "CTranslate2",
                "message": "bonjour",
                "transliteration": [
                    {"text": "bonjour", "reading": "bonjour"}
                ],
                "duration_ms": 45,
                "queue_position": 0,
                "error_code": None,
            },
        )
        self.assertTrue(
            {
                "target",
                "language",
                "country",
                "providers",
                "output_config",
            }.isdisjoint(payload)
        )

    def test_audio_chunk_keeps_two_value_transcriber_compatibility(self):
        spoken_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
        chunk = AudioChunk(b"raw audio", spoken_at, 123.5)

        self.assertEqual(tuple(chunk), (b"raw audio", spoken_at))
        self.assertEqual(chunk.captured_at_monotonic, 123.5)

    def test_output_snapshot_keeps_disabled_slots_and_countries(self):
        snapshot = make_output_config()

        self.assertEqual(len(snapshot.your_languages), 2)
        self.assertEqual(snapshot.your_languages[1].country, "Japan")
        self.assertFalse(snapshot.your_languages[1].enabled)
        self.assertEqual(snapshot.your_translation_languages[0].country, "Thailand")
        self.assertFalse(snapshot.your_translation_languages[0].enabled)
        self.assertEqual(snapshot.target_languages[1].country, "Germany")
        self.assertFalse(snapshot.target_languages[1].enabled)
        with self.assertRaises(FrozenInstanceError):
            snapshot.translation_enabled = False

    def test_trace_and_final_task_preserve_capture_time_and_generation(self):
        output_config = make_output_config()
        target = TranslationTarget("target-1", "French", "France")
        trace = TranscriptionTrace(
            trace_id="trace-1",
            generation=7,
            source=PipelineSource.SPEAKER,
            original_message="hello",
            source_language="English",
            original_transliteration=(),
            targets=(target,),
            providers=("DeepL", "CTranslate2"),
            ctranslate2_weight_type="Small",
            context_history=({"role": "user", "content": "previous"},),
            started_at_monotonic=987.25,
            output_config=output_config,
        )
        update = TranslationUpdate(
            trace_id="trace-1",
            target_slot="target-1",
            status=TranslationStatus.SUCCESS,
            engine="DeepL",
            message="bonjour",
            transliteration=(),
            duration_ms=20,
            queue_position=0,
            error_code=None,
        )
        final_task = FinalOutputTask(
            trace_id=trace.trace_id,
            generation=trace.generation,
            source=trace.source,
            original_message=trace.original_message,
            source_language=trace.source_language,
            original_transliteration=trace.original_transliteration,
            targets=trace.targets,
            translations=(update,),
            output_config=trace.output_config,
            started_at_monotonic=trace.started_at_monotonic,
        )

        self.assertEqual(final_task.started_at_monotonic, trace.started_at_monotonic)
        self.assertEqual(final_task.generation, trace.generation)
        self.assertEqual(final_task.source, trace.source)


if __name__ == "__main__":
    unittest.main()
