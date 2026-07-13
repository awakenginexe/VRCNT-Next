import os
import sys
import threading
import time
import unittest
from collections import deque
from unittest.mock import patch


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
from models.pipeline import source_pipeline as source_pipeline_module


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


class ControlledStartThread(threading.Thread):
    start_lock = threading.Lock()
    start_count = 0
    first_start_entered = threading.Event()
    release_first_start = threading.Event()

    @classmethod
    def reset(cls):
        with cls.start_lock:
            cls.start_count = 0
        cls.first_start_entered.clear()
        cls.release_first_start.clear()

    def start(self):
        with self.start_lock:
            type(self).start_count += 1
            start_number = type(self).start_count
        if start_number == 1:
            self.first_start_entered.set()
            if not self.release_first_start.wait(timeout=2.0):
                raise AssertionError("test did not release first Thread.start")
        return super().start()


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

    def test_queued_update_preserves_snapshotted_primary_provider(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "provider-gate"
        self.addCleanup(translator.release.set)
        pipeline = self.make_pipeline(translator, recorder)

        pipeline.submit_trace(
            make_trace(
                "provider-trace",
                message="provider-gate",
                providers=("Google", "Bing", "ignored"),
            )
        )
        self.assertTrue(translator.entered.wait(timeout=1.0))

        queued = next(
            item for item in recorder.updates
            if item.trace_id == "provider-trace"
            and item.status is TranslationStatus.QUEUED
        )
        self.assertEqual(queued.engine, "Google")
        self.assertEqual(queued.to_payload()["engine"], "Google")

        translator.release.set()
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

    def test_queue_positions_sending_zero_and_metric_depth_are_authoritative(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "blocked"
        pipeline = self.make_pipeline(translator, recorder)

        pipeline.submit_trace(make_trace("blocked", message="blocked"))
        self.assertTrue(translator.entered.wait(timeout=1.0))
        pipeline.submit_trace(make_trace("one-waiting"))
        pipeline.submit_trace(
            make_trace(
                "two-slots",
                targets=(
                    TranslationTarget("slot-1", "French", "France"),
                    TranslationTarget("slot-2", "German", "Germany"),
                ),
            )
        )

        queued = {
            (item.trace_id, item.target_slot): item.queue_position
            for item in recorder.updates
            if item.status is TranslationStatus.QUEUED
        }
        self.assertEqual(queued[("one-waiting", "target-1")], 1)
        self.assertEqual(queued[("two-slots", "slot-1")], 2)
        self.assertEqual(queued[("two-slots", "slot-2")], 3)

        translator.release.set()
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 3))
        sending = [
            item for item in recorder.updates
            if item.status is TranslationStatus.SENDING
        ]
        self.assertEqual(len(sending), 4)
        self.assertTrue(all(item.queue_position == 0 for item in sending))
        success_metrics = [
            item for item in recorder.metrics
            if item.stage == "translation" and item.outcome == "success"
        ]
        self.assertEqual([item.queue_depth for item in success_metrics], [3, 2, 1, 0])

    def test_translation_disabled_finalizes_original_only_once(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        transliterator_called = threading.Event()

        def forbidden_transliteration(*_args):
            transliterator_called.set()
            raise AssertionError("disabled translation called transliteration")

        pipeline = self.make_pipeline(translator, recorder, forbidden_transliteration)
        pipeline.submit_trace(
            make_trace(
                "disabled",
                config=make_output_config(translation_enabled=False),
            )
        )
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual(translator.calls, [])
        self.assertFalse(transliterator_called.is_set())
        self.assertEqual(recorder.updates, [])
        self.assertEqual(len(recorder.finals), 1)
        final = recorder.finals[0]
        self.assertEqual(final.original_message, "disabled")
        self.assertEqual(final.translations, ())

    def test_start_owns_exactly_two_daemons_and_stop_waits_for_provider(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "in-flight"
        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            recorder.emit_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )

        pipeline.start(7)
        translation_thread = pipeline._translation_thread
        output_thread = pipeline._output_thread
        self.assertTrue(translation_thread.daemon)
        self.assertTrue(output_thread.daemon)
        self.assertTrue(translation_thread.is_alive())
        self.assertTrue(output_thread.is_alive())
        with self.assertRaises(RuntimeError):
            pipeline.start(7)
        self.assertIs(pipeline._translation_thread, translation_thread)
        self.assertIs(pipeline._output_thread, output_thread)

        pipeline.submit_trace(make_trace("in-flight", message="in-flight"))
        self.assertTrue(translator.entered.wait(timeout=1.0))
        stop_returned = threading.Event()

        def stop_pipeline():
            pipeline.stop(7, discard_pending=True)
            stop_returned.set()

        stopper = threading.Thread(target=stop_pipeline, daemon=True)
        stopper.start()
        self.assertTrue(pipeline._stop_event.wait(timeout=1.0))
        self.assertIsNone(pipeline._generation)
        self.assertFalse(stop_returned.is_set())
        translator.release.set()
        self.assertTrue(stop_returned.wait(timeout=1.0))
        stopper.join(timeout=1.0)

        self.assertFalse(translation_thread.is_alive())
        self.assertFalse(output_thread.is_alive())
        self.assertEqual(
            [item.status for item in recorder.updates],
            [TranslationStatus.QUEUED, TranslationStatus.SENDING],
        )
        self.assertEqual(recorder.finals, [])

    def test_translation_job_is_a_complete_deep_snapshot(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "gate"
        pipeline = self.make_pipeline(translator, recorder)
        pipeline.submit_trace(make_trace("gate", message="gate"))
        self.assertTrue(translator.entered.wait(timeout=1.0))

        target = TranslationTarget("slot-x", "Thai", "Thailand")
        targets = [target]
        providers = ["one", "two", "three"]
        context = [{"role": "user", "content": "snapshot"}]
        snapshot_trace = TranscriptionTrace(
            trace_id="snapshot-job",
            generation=7,
            source=PipelineSource.SPEAKER,
            original_message="original text",
            source_language="Japanese",
            original_transliteration=(),
            targets=targets,
            providers=providers,
            ctranslate2_weight_type="Large",
            context_history=context,
            started_at_monotonic=time.monotonic(),
            output_config=make_output_config(),
        )
        before_enqueue = time.monotonic()
        pipeline.submit_trace(snapshot_trace)
        after_enqueue = time.monotonic()

        providers[:] = ["mutated"]
        context[0]["content"] = "mutated"
        targets[0] = TranslationTarget("other", "German", "Germany")
        with pipeline._translation_queue._condition:
            job = next(
                item for item in pipeline._translation_queue._items
                if item.trace_id == "snapshot-job"
            )

        self.assertEqual(job.trace_id, "snapshot-job")
        self.assertEqual(job.generation, 7)
        self.assertEqual(job.source, PipelineSource.SPEAKER)
        self.assertEqual(job.original_message, "original text")
        self.assertEqual(job.source_language, "Japanese")
        self.assertEqual(job.target, target)
        self.assertEqual(job.target.target_slot, "slot-x")
        self.assertEqual(job.target.language, "Thai")
        self.assertEqual(job.target.country, "Thailand")
        self.assertEqual(job.providers, ("one", "two"))
        self.assertEqual(job.ctranslate2_weight_type, "Large")
        self.assertEqual(
            job.context_history,
            ({"role": "user", "content": "snapshot"},),
        )
        self.assertGreaterEqual(job.enqueued_at_monotonic, before_enqueue)
        self.assertLessEqual(job.enqueued_at_monotonic, after_enqueue)

        translator.release.set()
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 2))
        snapshot_call = next(
            call for call in translator.calls
            if call["message"] == "original text"
        )
        self.assertEqual(snapshot_call["translator_name"], "one")
        self.assertEqual(
            snapshot_call["context_history"],
            [{"role": "user", "content": "snapshot"}],
        )

    def test_initial_callback_can_reenter_submit_and_stop_without_deadlock(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline_holder = {}
        nested_results = []

        def reentrant_initial(trace):
            recorder.emit_initial(trace)
            if trace.trace_id == "outer":
                nested_results.append(
                    pipeline_holder["pipeline"].submit_trace(make_trace("inner"))
                )
                pipeline_holder["pipeline"].stop(7, discard_pending=True)

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            reentrant_initial,
            recorder.emit_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        outer_result = []
        submit_returned = threading.Event()

        def submit_outer():
            outer_result.append(pipeline.submit_trace(make_trace("outer")))
            submit_returned.set()

        submitter = threading.Thread(target=submit_outer, daemon=True)
        submitter.start()
        self.assertTrue(submit_returned.wait(timeout=1.0))
        submitter.join(timeout=1.0)

        self.assertEqual(nested_results, [True])
        self.assertEqual(outer_result, [False])
        self.assertEqual(
            [item.trace_id for item in recorder.initial],
            ["outer", "inner"],
        )
        with pipeline._records_lock:
            self.assertEqual(pipeline._records, {})

    def test_initial_callback_failure_rolls_back_record_and_jobs(self):
        recorder = Recorder()

        def failing_initial(_trace):
            raise RuntimeError("initial transport failed")

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            ScriptedTranslator(),
            lambda *_: (),
            failing_initial,
            recorder.emit_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))

        with self.assertRaisesRegex(RuntimeError, "initial transport failed"):
            pipeline.submit_trace(make_trace("initial-failure"))

        with pipeline._records_lock:
            self.assertNotIn("initial-failure", pipeline._records)
        self.assertTrue(pipeline._translation_queue.empty())
        self.assertEqual(recorder.updates, [])

    def test_duplicate_target_slots_preserve_first_and_finalize_once(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline = self.make_pipeline(translator, recorder)
        pipeline.submit_trace(
            make_trace(
                "duplicate-slot",
                targets=(
                    TranslationTarget("same", "French", "France"),
                    TranslationTarget("same", "German", "Germany"),
                ),
            )
        )
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        final = recorder.finals[0]
        self.assertEqual(len(recorder.initial[0].targets), 1)
        self.assertEqual(
            [(item.target_slot, item.language) for item in final.targets],
            [("same", "French")],
        )
        self.assertEqual(len(final.translations), 1)
        self.assertEqual(
            [call["target_language"] for call in translator.calls],
            ["French"],
        )

    def test_active_duplicate_trace_id_is_rejected_without_replacement(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "original"
        pipeline = self.make_pipeline(translator, recorder)
        original = make_trace("same-trace", message="original")
        duplicate = make_trace("same-trace", message="duplicate")
        self.assertTrue(pipeline.submit_trace(original))
        self.assertTrue(translator.entered.wait(timeout=1.0))

        self.assertFalse(pipeline.submit_trace(duplicate))
        duplicate_metrics = [
            item for item in recorder.metrics
            if item.trace_id == "same-trace"
            and item.error_code == "duplicate_trace_id"
        ]
        self.assertEqual(len(duplicate_metrics), 1)
        with pipeline._records_lock:
            self.assertEqual(
                pipeline._records["same-trace"].trace.original_message,
                "original",
            )
        translator.release.set()
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual(
            [call["message"] for call in translator.calls],
            ["original"],
        )
        self.assertEqual(recorder.finals[0].original_message, "original")

    def test_callback_failures_are_sanitized_and_workers_continue(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        update_count = 0
        metric_count = 0
        transliterate_count = 0

        def flaky_update(update):
            nonlocal update_count
            update_count += 1
            if update_count == 1:
                raise RuntimeError("first update failed")
            recorder.emit_update(update)

        def flaky_metric(metric):
            nonlocal metric_count
            metric_count += 1
            if metric_count == 1:
                raise RuntimeError("first metric failed")
            recorder.emit_metric(metric)

        def flaky_transliterate(_message, _language, _config):
            nonlocal transliterate_count
            transliterate_count += 1
            if transliterate_count == 1:
                raise RuntimeError("first transliterator failed")
            return ({"text": "ok", "reading": "ok"},)

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            flaky_transliterate,
            recorder.emit_initial,
            flaky_update,
            flaky_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))

        with patch.object(source_pipeline_module.logger, "exception") as log_exception:
            self.assertTrue(pipeline.submit_trace(make_trace("first-callback")))
            self.assertTrue(pipeline.submit_trace(make_trace("second-callback")))
            self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 2))
            self.assertEqual(log_exception.call_count, 3)

        self.assertTrue(pipeline._translation_thread.is_alive())
        finals = {item.trace_id: item for item in recorder.finals}
        self.assertEqual(finals["first-callback"].translations[0].transliteration, ())
        self.assertEqual(
            finals["second-callback"].translations[0].transliteration,
            ({"text": "ok", "reading": "ok"},),
        )

    def test_callback_payloads_are_detached_from_internal_aggregation(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        internal_final_readings = []
        pipeline_holder = {}

        def mutating_initial(trace):
            trace.original_transliteration[0]["reading"] = "mutated-initial"
            trace.context_history[0]["trace"] = "mutated-context"
            recorder.emit_initial(trace)

        def mutating_update(update):
            if update.transliteration:
                update.transliteration[0]["reading"] = "mutated-update"
            recorder.emit_update(update)

        def mutating_final(task):
            task.translations[0].transliteration[0]["reading"] = "mutated-final"
            pipeline = pipeline_holder["pipeline"]
            with pipeline._records_lock:
                record = pipeline._records[task.trace_id]
            with record.lock:
                internal_final_readings.append(
                    record.translations["target-1"].transliteration[0]["reading"]
                )
            recorder.emit_final(task)

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: ({"text": "bonjour", "reading": "original-reading"},),
            mutating_initial,
            mutating_update,
            recorder.emit_metric,
            mutating_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))
        base = make_trace("detached")
        detached_trace = TranscriptionTrace(
            trace_id=base.trace_id,
            generation=base.generation,
            source=base.source,
            original_message=base.original_message,
            source_language=base.source_language,
            original_transliteration=({"text": "hello", "reading": "original"},),
            targets=base.targets,
            providers=base.providers,
            ctranslate2_weight_type=base.ctranslate2_weight_type,
            context_history=base.context_history,
            started_at_monotonic=base.started_at_monotonic,
            output_config=base.output_config,
        )
        pipeline.submit_trace(detached_trace)
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        final = recorder.finals[0]
        self.assertEqual(final.original_transliteration[0]["reading"], "original")
        self.assertEqual(
            final.translations[0].transliteration[0]["reading"],
            "mutated-final",
        )
        self.assertEqual(
            translator.calls[0]["context_history"],
            [{"trace": "detached"}],
        )
        self.assertEqual(internal_final_readings, ["original-reading"])

    def test_stop_during_starting_waits_until_both_threads_have_started(self):
        recorder = Recorder()
        ControlledStartThread.reset()
        pipeline = SourcePipeline(
            PipelineSource.MIC,
            ScriptedTranslator(),
            lambda *_: (),
            recorder.emit_initial,
            recorder.emit_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        start_errors = []
        stop_errors = []
        start_returned = threading.Event()
        stop_returned = threading.Event()

        def start_pipeline():
            try:
                pipeline.start(7)
            except Exception as error:
                start_errors.append(error)
            finally:
                start_returned.set()

        def stop_pipeline():
            try:
                pipeline.stop(7, discard_pending=True)
            except Exception as error:
                stop_errors.append(error)
            finally:
                stop_returned.set()

        with patch.object(source_pipeline_module, "Thread", ControlledStartThread):
            starter = threading.Thread(target=start_pipeline, daemon=True)
            starter.start()
            self.assertTrue(ControlledStartThread.first_start_entered.wait(timeout=1.0))
            stopper = threading.Thread(target=stop_pipeline, daemon=True)
            stopper.start()
            self.assertFalse(stop_returned.wait(timeout=0.1))
            ControlledStartThread.release_first_start.set()
            self.assertTrue(start_returned.wait(timeout=1.0))
            self.assertTrue(stop_returned.wait(timeout=1.0))
            starter.join(timeout=1.0)
            stopper.join(timeout=1.0)

        self.assertEqual(start_errors, [])
        self.assertEqual(stop_errors, [])
        self.assertEqual(pipeline._lifecycle_state.value, "stopped")
        self.assertFalse(pipeline._translation_thread.is_alive())
        self.assertFalse(pipeline._output_thread.is_alive())

    def test_concurrent_matching_stoppers_both_wait_for_provider_and_workers(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        translator.block_message = "two-stoppers"
        pipeline = self.make_pipeline(translator, recorder)
        pipeline.submit_trace(make_trace("two-stoppers", message="two-stoppers"))
        self.assertTrue(translator.entered.wait(timeout=1.0))
        first_returned = threading.Event()
        second_returned = threading.Event()

        first = threading.Thread(
            target=lambda: (pipeline.stop(7, discard_pending=True), first_returned.set()),
            daemon=True,
        )
        first.start()
        self.assertTrue(pipeline._stop_event.wait(timeout=1.0))
        second = threading.Thread(
            target=lambda: (pipeline.stop(7, discard_pending=True), second_returned.set()),
            daemon=True,
        )
        second.start()
        self.assertFalse(second_returned.wait(timeout=0.1))
        self.assertFalse(first_returned.is_set())

        translator.release.set()
        self.assertTrue(first_returned.wait(timeout=1.0))
        self.assertTrue(second_returned.wait(timeout=1.0))
        first.join(timeout=1.0)
        second.join(timeout=1.0)
        self.assertEqual(pipeline._lifecycle_state.value, "stopped")

    def test_discard_pending_false_is_explicitly_unsupported(self):
        recorder = Recorder()
        pipeline = self.make_pipeline(ScriptedTranslator(), recorder)
        with self.assertRaisesRegex(ValueError, "discard_pending=False"):
            pipeline.stop(7, discard_pending=False)
        self.assertTrue(pipeline._translation_thread.is_alive())

    def test_no_provider_terminal_metric_precedes_output_readiness(self):
        recorder = Recorder()
        metric_entered = threading.Event()
        release_metric = threading.Event()
        final_called = threading.Event()
        submit_returned = threading.Event()

        def blocking_metric(metric):
            if metric.stage == "translation" and metric.trace_id == "causal-empty":
                metric_entered.set()
                if not release_metric.wait(timeout=2.0):
                    raise AssertionError("test did not release terminal metric")
            recorder.emit_metric(metric)

        def observe_final(task):
            recorder.emit_final(task)
            final_called.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            ScriptedTranslator(),
            lambda *_: (),
            recorder.emit_initial,
            recorder.emit_update,
            blocking_metric,
            observe_final,
            lambda generation: generation == 7,
        )
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))

        submitter = threading.Thread(
            target=lambda: (
                pipeline.submit_trace(make_trace("causal-empty", providers=())),
                submit_returned.set(),
            ),
            daemon=True,
        )
        submitter.start()
        self.assertTrue(metric_entered.wait(timeout=1.0))
        self.assertFalse(final_called.wait(timeout=0.1))
        release_metric.set()
        self.assertTrue(submit_returned.wait(timeout=1.0))
        self.assertTrue(final_called.wait(timeout=1.0))
        submitter.join(timeout=1.0)

    def test_worker_side_stop_from_sending_waits_for_exit_and_skips_provider(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline_holder = {}
        worker_stop_returned = threading.Event()
        release_update_callback = threading.Event()
        observed_states = []

        def stopping_update(update):
            recorder.emit_update(update)
            if update.status is TranslationStatus.SENDING:
                pipeline = pipeline_holder["pipeline"]
                pipeline.stop(7, discard_pending=True)
                observed_states.append(pipeline._lifecycle_state.value)
                worker_stop_returned.set()
                if not release_update_callback.wait(timeout=2.0):
                    raise AssertionError("test did not release sending callback")

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            stopping_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        pipeline.submit_trace(make_trace("worker-stop"))
        self.assertTrue(worker_stop_returned.wait(timeout=1.0))
        self.assertEqual(observed_states, ["stopping"])

        external_stop_returned = threading.Event()
        external_stopper = threading.Thread(
            target=lambda: (
                pipeline.stop(7, discard_pending=True),
                external_stop_returned.set(),
            ),
            daemon=True,
        )
        external_stopper.start()
        self.assertFalse(external_stop_returned.wait(timeout=0.1))
        self.assertEqual(translator.calls, [])

        release_update_callback.set()
        self.assertTrue(external_stop_returned.wait(timeout=1.0))
        external_stopper.join(timeout=1.0)
        self.assertEqual(pipeline._lifecycle_state.value, "stopped")
        self.assertEqual(translator.calls, [])
        self.assertEqual(recorder.finals, [])

    def test_stop_from_attempt_metric_prevents_fallback_state_and_provider(self):
        recorder = Recorder()
        translator = ScriptedTranslator(
            [
                TranslationAttempt(
                    TranslationStatus.TIMEOUT,
                    "one",
                    None,
                    1,
                    "provider_timeout",
                )
            ]
        )
        pipeline_holder = {}
        metric_stop_returned = threading.Event()

        def stopping_metric(metric):
            recorder.emit_metric(metric)
            if metric.stage == "translation" and metric.outcome == "timeout":
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                metric_stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            recorder.emit_update,
            stopping_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        pipeline.submit_trace(make_trace("metric-stop", providers=("one", "two")))
        self.assertTrue(metric_stop_returned.wait(timeout=1.0))
        with pipeline._lifecycle_condition:
            deadline = time.monotonic() + 1.0
            while pipeline._lifecycle_state.value != "stopped":
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                pipeline._lifecycle_condition.wait(remaining)

        self.assertEqual(
            [call["translator_name"] for call in translator.calls],
            ["one"],
        )
        self.assertNotIn(
            TranslationStatus.FALLBACK,
            [item.status for item in recorder.updates],
        )
        self.assertEqual(recorder.finals, [])

    def test_stop_from_sending_metric_prevents_provider_call(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline_holder = {}
        metric_stop_returned = threading.Event()

        def stopping_metric(metric):
            recorder.emit_metric(metric)
            if metric.stage == "translation" and metric.outcome == "sending":
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                metric_stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            recorder.emit_update,
            stopping_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))
        pipeline.submit_trace(make_trace("sending-metric-stop"))

        self.assertTrue(metric_stop_returned.wait(timeout=1.0))
        with pipeline._lifecycle_condition:
            deadline = time.monotonic() + 1.0
            while pipeline._lifecycle_state.value != "stopped":
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                pipeline._lifecycle_condition.wait(remaining)

        self.assertEqual(translator.calls, [])
        self.assertEqual(recorder.finals, [])

    def test_stop_from_fallback_update_prevents_alternate_provider(self):
        recorder = Recorder()
        translator = ScriptedTranslator(
            [
                TranslationAttempt(
                    TranslationStatus.ERROR,
                    "one",
                    None,
                    1,
                    "provider_error",
                )
            ]
        )
        pipeline_holder = {}
        fallback_stop_returned = threading.Event()

        def stopping_update(update):
            recorder.emit_update(update)
            if update.status is TranslationStatus.FALLBACK:
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                fallback_stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            stopping_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        pipeline.submit_trace(make_trace("fallback-stop", providers=("one", "two")))
        self.assertTrue(fallback_stop_returned.wait(timeout=1.0))
        with pipeline._lifecycle_condition:
            deadline = time.monotonic() + 1.0
            while pipeline._lifecycle_state.value != "stopped":
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                pipeline._lifecycle_condition.wait(remaining)
        self.assertEqual(
            [call["translator_name"] for call in translator.calls],
            ["one"],
        )

    def test_stop_from_fallback_metric_prevents_alternate_provider(self):
        recorder = Recorder()
        translator = ScriptedTranslator(
            [
                TranslationAttempt(
                    TranslationStatus.ERROR,
                    "one",
                    None,
                    1,
                    "provider_error",
                )
            ]
        )
        pipeline_holder = {}
        metric_stop_returned = threading.Event()

        def stopping_metric(metric):
            recorder.emit_metric(metric)
            if metric.stage == "translation" and metric.outcome == "fallback":
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                metric_stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            recorder.emit_update,
            stopping_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        self.addCleanup(lambda: pipeline.stop(7, discard_pending=True))
        pipeline.submit_trace(
            make_trace("fallback-metric-stop", providers=("one", "two"))
        )

        self.assertTrue(metric_stop_returned.wait(timeout=1.0))
        with pipeline._lifecycle_condition:
            deadline = time.monotonic() + 1.0
            while pipeline._lifecycle_state.value != "stopped":
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                pipeline._lifecycle_condition.wait(remaining)

        self.assertEqual(
            [call["translator_name"] for call in translator.calls],
            ["one"],
        )
        self.assertEqual(recorder.finals, [])

    def test_stop_during_no_provider_slot_publication_cancels_remaining_slots(self):
        recorder = Recorder()
        pipeline_holder = {}
        stop_returned = threading.Event()

        def stopping_update(update):
            recorder.emit_update(update)
            if (
                update.status is TranslationStatus.ERROR
                and update.target_slot == "slot-1"
            ):
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            ScriptedTranslator(),
            lambda *_: (),
            recorder.emit_initial,
            stopping_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        pipeline.submit_trace(
            make_trace(
                "no-provider-stop",
                providers=(),
                targets=(
                    TranslationTarget("slot-1", "French", "France"),
                    TranslationTarget("slot-2", "German", "Germany"),
                ),
            )
        )
        self.assertTrue(stop_returned.wait(timeout=1.0))

        terminal_errors = [
            item for item in recorder.updates
            if item.status is TranslationStatus.ERROR
        ]
        self.assertEqual([item.target_slot for item in terminal_errors], ["slot-1"])
        self.assertEqual(recorder.finals, [])
        self.assertEqual(pipeline._lifecycle_state.value, "stopped")

    def test_translation_and_output_worker_stop_followers_both_unwind(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline_holder = {}
        translation_callback_entered = threading.Event()
        output_callback_entered = threading.Event()
        release_stop_calls = threading.Event()
        translation_stop_returned = threading.Event()
        output_stop_returned = threading.Event()

        def stopping_update(update):
            recorder.emit_update(update)
            if update.status is TranslationStatus.SENDING:
                translation_callback_entered.set()
                if not release_stop_calls.wait(timeout=2.0):
                    raise AssertionError("test did not release translation stop")
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                translation_stop_returned.set()

        def stopping_metric(metric):
            recorder.emit_metric(metric)
            if metric.stage == "output" and metric.outcome == "running":
                output_callback_entered.set()
                if not release_stop_calls.wait(timeout=2.0):
                    raise AssertionError("test did not release output stop")
                pipeline_holder["pipeline"].stop(7, discard_pending=True)
                output_stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            stopping_update,
            stopping_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        pipeline.submit_trace(make_trace("output-callback", targets=()))
        self.assertTrue(output_callback_entered.wait(timeout=1.0))
        pipeline.submit_trace(make_trace("translation-callback"))
        self.assertTrue(translation_callback_entered.wait(timeout=1.0))

        release_stop_calls.set()
        self.assertTrue(translation_stop_returned.wait(timeout=1.0))
        self.assertTrue(output_stop_returned.wait(timeout=1.0))
        with pipeline._lifecycle_condition:
            deadline = time.monotonic() + 1.0
            while pipeline._lifecycle_state.value != "stopped":
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                pipeline._lifecycle_condition.wait(remaining)

        self.assertFalse(pipeline._translation_thread.is_alive())
        self.assertFalse(pipeline._output_thread.is_alive())
        self.assertEqual(translator.calls, [])
        self.assertEqual(recorder.finals, [])

    def test_external_stopper_waits_while_worker_stop_follower_unwinds(self):
        recorder = Recorder()
        translator = ScriptedTranslator()
        pipeline_holder = {}
        callback_entered = threading.Event()
        release_callback = threading.Event()
        worker_stop_returned = threading.Event()
        external_stop_returned = threading.Event()
        observed_states = []

        def stopping_update(update):
            recorder.emit_update(update)
            if update.status is TranslationStatus.SENDING:
                callback_entered.set()
                if not release_callback.wait(timeout=2.0):
                    raise AssertionError("test did not release worker follower")
                pipeline = pipeline_holder["pipeline"]
                pipeline.stop(7, discard_pending=True)
                observed_states.append(pipeline._lifecycle_state.value)
                worker_stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            translator,
            lambda *_: (),
            recorder.emit_initial,
            stopping_update,
            recorder.emit_metric,
            recorder.emit_final,
            lambda generation: generation == 7,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(7)
        pipeline.submit_trace(make_trace("external-first"))
        self.assertTrue(callback_entered.wait(timeout=1.0))

        external_stopper = threading.Thread(
            target=lambda: (
                pipeline.stop(7, discard_pending=True),
                external_stop_returned.set(),
            ),
            daemon=True,
        )
        external_stopper.start()
        self.assertTrue(pipeline._stop_event.wait(timeout=1.0))
        self.assertFalse(external_stop_returned.is_set())
        release_callback.set()

        self.assertTrue(worker_stop_returned.wait(timeout=1.0))
        self.assertTrue(external_stop_returned.wait(timeout=1.0))
        external_stopper.join(timeout=1.0)
        self.assertEqual(observed_states, ["stopping"])
        self.assertEqual(pipeline._lifecycle_state.value, "stopped")
        self.assertEqual(translator.calls, [])
        self.assertEqual(recorder.finals, [])

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
        primary_failure_metrics = [
            item for item in recorder.metrics
            if item.stage == "translation"
            and item.trace_id == "trace-1"
            and item.outcome == "timeout"
        ]
        self.assertEqual(len(primary_failure_metrics), 1)
        primary_failure = primary_failure_metrics[0]
        self.assertEqual(primary_failure.engine, "primary")
        self.assertEqual(primary_failure.target_slot, "target-1")
        self.assertEqual(primary_failure.duration_ms, 9)
        self.assertEqual(primary_failure.error_code, "provider_timeout")
        translation_metrics = [
            item for item in recorder.metrics
            if item.stage == "translation" and item.trace_id == "trace-1"
        ]
        self.assertEqual(
            [item.outcome for item in translation_metrics],
            ["sending", "timeout", "fallback", "sending", "success"],
        )
        self.assertEqual(
            [item.engine for item in translation_metrics],
            ["primary", "primary", "alternate", "alternate", "alternate"],
        )
        active_metrics = [
            item for item in translation_metrics
            if item.outcome in ("sending", "fallback")
        ]
        self.assertEqual(len(active_metrics), 3)
        for metric in active_metrics:
            self.assertEqual(metric.source, PipelineSource.MIC)
            self.assertEqual(metric.trace_id, "trace-1")
            self.assertEqual(metric.target_slot, "target-1")
            self.assertIsInstance(metric.queue_age_ms, int)
            self.assertGreaterEqual(metric.queue_age_ms, 0)
            self.assertEqual(metric.queue_depth, 0)
            self.assertIsNone(metric.duration_ms)
            self.assertIsNone(metric.error_code)
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
                TranslationStatus.FALLBACK,
                TranslationStatus.SENDING,
                TranslationStatus.TIMEOUT,
            ],
        )
        terminal_updates = [
            item for item in recorder.updates
            if item.status in (TranslationStatus.TIMEOUT, TranslationStatus.ERROR)
        ]
        self.assertEqual(len(terminal_updates), 1)
        self.assertEqual(terminal_updates[0].engine, "two")
        primary_failure_metrics = [
            item for item in recorder.metrics
            if item.stage == "translation"
            and item.engine == "one"
            and item.outcome == "error"
        ]
        self.assertEqual(len(primary_failure_metrics), 1)
        self.assertEqual(primary_failure_metrics[0].duration_ms, 1)
        self.assertEqual(primary_failure_metrics[0].error_code, "first_error")
        self.assertEqual(len(recorder.finals[0].translations), 1)
        self.assertEqual(recorder.finals[0].translations[0].status, TranslationStatus.TIMEOUT)

    def test_last_provider_error_is_the_only_terminal_translation_update(self):
        attempts = [
            TranslationAttempt(
                TranslationStatus.TIMEOUT,
                "one",
                None,
                3,
                "provider_timeout",
            ),
            TranslationAttempt(
                TranslationStatus.ERROR,
                "two",
                None,
                5,
                "provider_error",
            ),
        ]
        recorder = Recorder()
        pipeline = self.make_pipeline(ScriptedTranslator(attempts), recorder)

        pipeline.submit_trace(make_trace("trace-final-error", providers=("one", "two")))
        self.assertTrue(recorder.wait_for(lambda: len(recorder.finals) == 1))

        self.assertEqual(
            [item.status for item in recorder.updates],
            [
                TranslationStatus.QUEUED,
                TranslationStatus.SENDING,
                TranslationStatus.FALLBACK,
                TranslationStatus.SENDING,
                TranslationStatus.ERROR,
            ],
        )
        terminal_updates = [
            item for item in recorder.updates
            if item.status in (TranslationStatus.TIMEOUT, TranslationStatus.ERROR)
        ]
        self.assertEqual(len(terminal_updates), 1)
        self.assertEqual(terminal_updates[0].engine, "two")
        self.assertEqual(terminal_updates[0].duration_ms, 5)
        self.assertEqual(terminal_updates[0].error_code, "provider_error")

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
        queued = [
            update for update in recorder.updates
            if update.status is TranslationStatus.QUEUED
        ]
        self.assertEqual([item.engine for item in queued], [None, None])
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
