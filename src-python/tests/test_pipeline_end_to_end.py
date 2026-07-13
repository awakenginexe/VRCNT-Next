import os
import sys
import threading
import unittest
from datetime import datetime, timezone


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import (
    AudioChunk,
    LanguageSlotSnapshot,
    MessageFormatSnapshot,
    OutputConfigSnapshot,
    PipelineSource,
    TranscriptionTrace,
    TranslationAttempt,
    TranslationStatus,
    TranslationTarget,
)
from models.pipeline.source_pipeline import SourcePipeline


WAIT_SECONDS = 2.0


def _output_config():
    fmt = MessageFormatSnapshot("", "", "", "", " / ", " | ", False)
    return OutputConfigSnapshot(
        selected_tab_no="1",
        translation_enabled=True,
        send_message_to_vrc=False,
        send_received_message_to_vrc=False,
        send_only_translated_messages=False,
        overlay_small_log=False,
        overlay_large_log=False,
        overlay_show_only_translated_messages=False,
        enable_clipboard=False,
        logger_feature=False,
        convert_message_to_hiragana=False,
        convert_message_to_romaji=False,
        websocket_requested=False,
        your_languages=(LanguageSlotSnapshot("1", "English", "US", True),),
        your_translation_languages=(),
        target_languages=(LanguageSlotSnapshot("1", "Japanese", "Japan", True),),
        send_format=fmt,
        received_format=fmt,
    )


class _ControlledTranslator:
    def __init__(self):
        self.a_entered = threading.Event()
        self.release_a = threading.Event()
        self.attempts = []

    def translateAttempt(
        self,
        *,
        translator_name,
        weight_type,
        source_language,
        target_language,
        target_country,
        message,
        context_history,
        timeout_seconds,
    ):
        self.attempts.append(
            (translator_name, message, target_language, timeout_seconds)
        )
        if message == "A":
            self.a_entered.set()
            if not self.release_a.wait(WAIT_SECONDS):
                raise RuntimeError("controlled provider was not released")
        return TranslationAttempt(
            status=TranslationStatus.SUCCESS,
            engine=translator_name,
            message=f"{message}-{target_language}",
            duration_ms=1,
            error_code=None,
        )


class PipelineEndToEndTests(unittest.TestCase):
    def test_slow_a_provider_does_not_hide_b_original_and_a_finishes_once(self):
        translator = _ControlledTranslator()
        initial = []
        updates = []
        finals = []
        metrics = []
        final_ready = threading.Event()
        generation = 11
        pipeline = SourcePipeline(
            source=PipelineSource.MIC,
            translator=translator,
            transliterate=lambda *args: (),
            emit_initial=lambda trace: initial.append((trace.trace_id, trace.original_message)),
            emit_update=updates.append,
            emit_metric=metrics.append,
            emit_final=lambda task: (finals.append(task), final_ready.set()),
            is_generation_current=lambda candidate: candidate == generation,
        )
        pipeline.start(generation)
        self.addCleanup(lambda: pipeline.stop(generation))
        target = TranslationTarget("1", "Japanese", "Japan")
        config = _output_config()

        def fake_transcribe(chunk, message):
            return {
                "text": message,
                "language": "English",
                "started_at_monotonic": chunk.captured_at_monotonic,
            }

        def trace(trace_id, result):
            return TranscriptionTrace(
                trace_id=trace_id,
                generation=generation,
                source=PipelineSource.MIC,
                original_message=result["text"],
                source_language=result["language"],
                original_transliteration=(),
                targets=(target,),
                providers=("Google",),
                ctranslate2_weight_type="Small",
                context_history=(),
                started_at_monotonic=result["started_at_monotonic"],
                output_config=config,
            )

        import time

        chunk_a = AudioChunk(
            b"audio-a",
            datetime.now(timezone.utc),
            time.monotonic() - 0.02,
        )
        chunk_b = AudioChunk(
            b"audio-b",
            datetime.now(timezone.utc),
            time.monotonic(),
        )
        self.assertTrue(
            pipeline.submit_trace(trace("trace-a", fake_transcribe(chunk_a, "A")))
        )
        self.assertTrue(translator.a_entered.wait(WAIT_SECONDS))
        self.assertEqual(
            translator.attempts[0],
            ("Google", "A", "Japanese", 5.0),
        )
        self.assertTrue(
            pipeline.submit_trace(trace("trace-b", fake_transcribe(chunk_b, "B")))
        )
        self.assertIn(("trace-b", "B"), initial)
        self.assertFalse(final_ready.is_set())

        translator.release_a.set()
        self.assertTrue(final_ready.wait(WAIT_SECONDS))
        pipeline.stop(generation)

        a_finals = [task for task in finals if task.trace_id == "trace-a"]
        self.assertEqual(len(a_finals), 1)
        a_updates = [update for update in updates if update.trace_id == "trace-a"]
        self.assertEqual(
            [
                (update.trace_id, update.target_slot, update.status)
                for update in a_updates
            ],
            [
                ("trace-a", "1", TranslationStatus.QUEUED),
                ("trace-a", "1", TranslationStatus.SENDING),
                ("trace-a", "1", TranslationStatus.SUCCESS),
            ],
        )
        a_final = a_finals[0]
        self.assertEqual(a_final.trace_id, "trace-a")
        self.assertEqual(len(a_final.translations), 1)
        self.assertEqual(a_final.translations[0].trace_id, "trace-a")
        self.assertEqual(a_final.translations[0].target_slot, "1")
        self.assertEqual(
            a_final.translations[0].status,
            TranslationStatus.SUCCESS,
        )
        a_output = next(
            event
            for event in metrics
            if event.trace_id == "trace-a"
            and event.stage == "output"
            and event.outcome == "success"
        )
        self.assertGreaterEqual(a_output.duration_ms, 20)


if __name__ == "__main__":
    unittest.main()
