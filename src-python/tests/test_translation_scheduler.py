import os
import sys
import threading
import time
import unittest
from collections import deque


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import (
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


def make_output_config(**overrides):
    message_format = MessageFormatSnapshot("", "", "", "", " / ", " | ", False)
    values = dict(
        selected_tab_no="1",
        translation_enabled=True,
        send_message_to_vrc=True,
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
        your_languages=(LanguageSlotSnapshot("your-1", "English", "US", True),),
        your_translation_languages=(),
        target_languages=(),
        send_format=message_format,
        received_format=message_format,
    )
    values.update(overrides)
    return OutputConfigSnapshot(**values)


def make_trace(trace_id, *, message=None, targets=None, providers=("primary",), config=None):
    targets = targets if targets is not None else (
        TranslationTarget("target-1", "French", "France"),
    )
    return TranscriptionTrace(
        trace_id=trace_id,
        generation=7,
        source=PipelineSource.MIC,
        original_message=message or trace_id,
        source_language="English",
        original_transliteration=(),
        targets=targets,
        providers=providers,
        ctranslate2_weight_type="Small",
        context_history=({"trace": trace_id},),
        started_at_monotonic=time.monotonic(),
        output_config=config or make_output_config(),
    )


class Recorder:
    def __init__(self):
        self.condition = threading.Condition()
        self.initial = []
        self.updates = []
        self.metrics = []
        self.finals = []
        self.timeline = []

    def _append(self, kind, collection, value):
        with self.condition:
            collection.append(value)
            self.timeline.append((kind, value))
            self.condition.notify_all()

    def emit_initial(self, trace):
        self._append("initial", self.initial, trace)

    def emit_update(self, update):
        self._append("update", self.updates, update)

    def emit_metric(self, metric):
        self._append("metric", self.metrics, metric)

    def emit_final(self, task):
        self._append("final", self.finals, task)

    def wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True


class ScriptedTranslator:
    def __init__(self, attempts=()):
        self.attempts = deque(attempts)
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_message = None

    def translateAttempt(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["message"] == self.block_message:
            self.entered.set()
            if not self.release.wait(timeout=2.0):
                raise AssertionError("test did not release blocked provider")
        if self.attempts:
            return self.attempts.popleft()
        return TranslationAttempt(
            TranslationStatus.SUCCESS,
            kwargs["translator_name"],
            f"translated:{kwargs['message']}",
            1,
            None,
        )


class TranslationSchedulerTests(unittest.TestCase):
    def make_pipeline(self, translator, recorder, transliterate=lambda *_: ()):
        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            transliterate,
            recorder.emit_initial,
            recorder.emit_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))
        return pipeline

    def test_submit_emits_second_initial_while_first_provider_is_blocked(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "A"
        pipeline = self.make_pipeline(translator, recorder)

        pipeline.submit_trace(make_trace("trace-A", message="A"))
        self.assertTrue(translator.entered.wait(timeout=1.0))
        pipeline.submit_trace(make_trace("trace-B", message="B"))

        self.assertEqual([trace.trace_id for trace in recorder.initial], ["trace-A", "trace-B"])
        self.assertEqual(
            [(item.trace_id, item.status) for item in recorder.updates[:2]],
            [
                ("trace-A", TranslationStatus.QUEUED),
                ("trace-A", TranslationStatus.SENDING),
            ],
        )
        self.assertIn(
            ("trace-B", TranslationStatus.QUEUED),
            [(item.trace_id, item.status) for item in recorder.updates],
        )
        self.assertEqual(recorder.finals, [])
        translator.release.set()
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 2))

    def test_success_and_fallback_state_order_and_attempt_metrics(self):
        attempts = [
            TranslationAttempt(TranslationStatus.TIMEOUT, "primary", None, 9, "provider_timeout"),
            TranslationAttempt(TranslationStatus.SUCCESS, "alternate", "bonjour", 4, None),
        ]
        recorder = Recorder()
        translator = ScriptedTranslator(attempts)
        pipeline = self.make_pipeline(translator, recorder)

        pipeline.submit_trace(make_trace("trace-1", providers=("primary", "alternate", "third")))
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual(
            [update.status for update in recorder.updates],
            [
                TranslationStatus.QUEUED,
                TranslationStatus.SENDING,
                TranslationStatus.TIMEOUT,
                TranslationStatus.FALLBACK,
                TranslationStatus.SENDING,
                TranslationStatus.SUCCESS,
            ],
        )
        self.assertEqual(
            [call["translator_name"] for call in translator.calls],
            ["primary", "alternate"],
        )
        self.assertNotIn("CTranslate2", [call["translator_name"] for call in translator.calls])
        timeout_metric_index = next(
            index for index, (kind, item) in enumerate(recorder.timeline)
            if kind == "metric" and item.stage == "translation" and item.outcome == "timeout"
        )
        fallback_update_index = next(
            index for index, (kind, item) in enumerate(recorder.timeline)
            if kind == "update" and item.status is TranslationStatus.FALLBACK
        )
        self.assertLess(timeout_metric_index, fallback_update_index)

    def test_last_provider_failure_is_the_only_terminal_and_context_is_snapshotted(self):
        attempts = [
            TranslationAttempt(TranslationStatus.ERROR, "one", None, 1, "first_error"),
            TranslationAttempt(TranslationStatus.TIMEOUT, "two", None, 2, "provider_timeout"),
        ]
        recorder = Recorder()
        translator = ScriptedTranslator(attempts)
        translator.block_message = "snapshot"
        pipeline = self.make_pipeline(translator, recorder)
        trace = make_trace("trace-fail", message="snapshot", providers=("one", "two", "three"))

        pipeline.submit_trace(trace)
        self.assertTrue(translator.entered.wait(timeout=1.0))
        trace.context_history[0]["trace"] = "mutated"
        translator.release.set()
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual([call["translator_name"] for call in translator.calls], ["one", "two"])
        self.assertEqual(translator.calls[0]["context_history"], [{"trace": "trace-fail"}])
        statuses = [item.status for item in recorder.updates]
        self.assertEqual(
            statuses,
            [
                TranslationStatus.QUEUED,
                TranslationStatus.SENDING,
                TranslationStatus.ERROR,
                TranslationStatus.FALLBACK,
                TranslationStatus.SENDING,
                TranslationStatus.TIMEOUT,
            ],
        )
        self.assertEqual(len(recorder.finals[0].translations), 1)
        self.assertEqual(recorder.finals[0].translations[0].status, TranslationStatus.TIMEOUT)

    def test_empty_provider_snapshot_errors_each_slot_and_finalizes_once(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline = self.make_pipeline(translator, recorder)
        targets = (
            TranslationTarget("target-1", "French", "France"),
            TranslationTarget("target-2", "German", "Germany"),
        )

        pipeline.submit_trace(make_trace("trace-empty", targets=targets, providers=()))
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual(translator.calls, [])
        errors = [update for update in recorder.updates if update.status is TranslationStatus.ERROR]
        self.assertEqual([item.target_slot for item in errors], ["target-1", "target-2"])
        self.assertEqual([item.error_code for item in errors], ["no_provider_configured"] * 2)
        self.assertEqual(len(recorder.initial), 1)
        self.assertEqual(len(recorder.finals), 1)

    def test_success_invokes_injected_transliteration_with_target_and_snapshot(self):
        recorder = Recorder()
        translator = ScriptedTranslator([
            TranslationAttempt(TranslationStatus.SUCCESS, "primary", "nihongo", 2, None)
        ])
        calls = []
        tokens = ({"text": "日", "reading": "にち"},)

        def transliterate(message, language, output_config):
            calls.append((message, language, output_config))
            return tokens

        pipeline = self.make_pipeline(translator, recorder, transliterate)
        trace = make_trace(
            "trace-ja",
            targets=(TranslationTarget("target-ja", "Japanese", "Japan"),),
        )
        pipeline.submit_trace(trace)
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual(calls, [("nihongo", "Japanese", trace.output_config)])
        success = next(
            item for item in recorder.updates
            if item.status is TranslationStatus.SUCCESS
        )
        self.assertEqual(success.transliteration, tokens)
        self.assertEqual(recorder.finals[0].translations[0].transliteration, tokens)


if __name__ == "__main__":
    unittest.main()
