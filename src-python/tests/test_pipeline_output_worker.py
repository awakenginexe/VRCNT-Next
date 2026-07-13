import os
import sys
import threading
import time
import unittest
from queue import Queue
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import (
    MessageFormatSnapshot,
    OutputConfigSnapshot,
    PipelineSource,
    TranscriptionTrace,
)
from models.pipeline.source_pipeline import SourcePipeline
from models.pipeline import source_pipeline as source_pipeline_module


def make_trace(trace_id, started_at):
    fmt = MessageFormatSnapshot("", "", "", "", " / ", " | ", False)
    config = OutputConfigSnapshot(
        "1", True, True, False, False, False, False, False, False, False,
        False, False, False, (), (), (), fmt, fmt,
    )
    return TranscriptionTrace(
        trace_id, 5, PipelineSource.MIC, trace_id, "English", (), (), (),
        "Small", (), started_at, config,
    )


class GatedGetQueue(Queue):
    def __init__(self):
        super().__init__(maxsize=4)
        self.item_put = threading.Event()
        self.allow_get = threading.Event()

    def put(self, item, block=True, timeout=None):
        result = super().put(item, block=block, timeout=timeout)
        self.item_put.set()
        return result

    def get(self, block=True, timeout=None):
        if not self.allow_get.wait(timeout=2.0):
            raise AssertionError("test did not release output get")
        return super().get(block=block, timeout=timeout)


class ObservedOutputQueue(Queue):
    def __init__(self):
        super().__init__(maxsize=4)
        self.put_waiting_for_capacity = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.full():
            self.put_waiting_for_capacity.set()
        return super().put(item, block=block, timeout=timeout)


class OutputWorkerTests(unittest.TestCase):
    def test_finalizer_error_is_measured_and_worker_continues(self):
        condition = threading.Condition()
        metrics = []
        calls = []

        def emit_metric(metric):
            with condition:
                metrics.append(metric)
                condition.notify_all()

        def emit_final(task):
            calls.append(task.trace_id)
            if task.trace_id == "first":
                raise RuntimeError("broken destination")

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            object(),
            lambda *_: (),
            lambda _trace: None,
            lambda _update: None,
            emit_metric,
            emit_final,
            lambda generation: generation == 5,
        )
        removed = []
        original_remove = pipeline._remove_record

        def observe_remove(trace_id, expected=None):
            original_remove(trace_id, expected)
            with condition:
                removed.append(trace_id)
                condition.notify_all()

        pipeline._remove_record = observe_remove
        pipeline.start(5)
        self.addCleanup(lambda: pipeline.stop(5, discard_pending=True))
        started = time.monotonic() - 0.05
        pipeline.submit_trace(make_trace("first", started))
        pipeline.submit_trace(make_trace("second", started))

        deadline = time.monotonic() + 2.0
        with condition:
            while len(
                [
                    item for item in metrics
                    if item.stage == "output"
                    and item.outcome in ("success", "error")
                ]
            ) < 2:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                condition.wait(remaining)
            while not {"first", "second"}.issubset(removed):
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                condition.wait(remaining)

        output_metrics = [item for item in metrics if item.stage == "output"]
        self.assertEqual(
            [item.outcome for item in output_metrics],
            ["running", "error", "running", "success"],
        )
        self.assertEqual(calls, ["first", "second"])
        terminal = [item for item in output_metrics if item.outcome in ("error", "success")]
        self.assertTrue(all(item.duration_ms >= 50 for item in terminal))
        self.assertTrue(pipeline._output_thread.is_alive())
        with pipeline._records_lock:
            self.assertNotIn("first", pipeline._records)
            self.assertNotIn("second", pipeline._records)

    def test_stale_generation_drops_output_task_and_cleans_aggregation(self):
        current = threading.Event()
        current.set()
        final_calls = []
        metrics = []
        removed = threading.Event()
        pipeline = SourcePipeline(
            PipelineSource.MIC,
            object(),
            lambda *_: (),
            lambda _trace: None,
            lambda _update: None,
            metrics.append,
            lambda task: final_calls.append(task.trace_id),
            lambda generation: generation == 5 and current.is_set(),
        )
        gated_queue = GatedGetQueue()
        pipeline._output_queue = gated_queue
        original_remove = pipeline._remove_record

        def observe_remove(trace_id, expected=None):
            original_remove(trace_id, expected)
            if trace_id == "stale":
                removed.set()

        pipeline._remove_record = observe_remove
        pipeline.start(5)
        self.addCleanup(lambda: pipeline.stop(5, discard_pending=True))
        pipeline.submit_trace(make_trace("stale", time.monotonic()))
        self.assertTrue(gated_queue.item_put.wait(timeout=1.0))
        with pipeline._records_lock:
            self.assertIn("stale", pipeline._records)

        current.clear()
        gated_queue.allow_get.set()
        self.assertTrue(removed.wait(timeout=1.0))

        self.assertEqual(final_calls, [])
        self.assertEqual(
            [item for item in metrics if item.trace_id == "stale"],
            [],
        )
        with pipeline._records_lock:
            self.assertNotIn("stale", pipeline._records)

    def test_full_output_put_cancels_on_stop_and_waits_for_inflight_finalizer(self):
        finalizer_entered = threading.Event()
        release_finalizer = threading.Event()
        translation_exited = threading.Event()
        stop_returned = threading.Event()
        final_calls = []
        pipeline = SourcePipeline(
            PipelineSource.MIC,
            object(),
            lambda *_: (),
            lambda _trace: None,
            lambda _update: None,
            lambda _metric: None,
            lambda task: self._blocking_finalizer(
                task,
                final_calls,
                finalizer_entered,
                release_finalizer,
            ),
            lambda generation: generation == 5,
        )
        observed_queue = ObservedOutputQueue()
        pipeline._output_queue = observed_queue
        original_translation_worker = pipeline._translation_worker

        def observe_translation_exit():
            try:
                original_translation_worker()
            finally:
                translation_exited.set()

        pipeline._translation_worker = observe_translation_exit
        pipeline.start(5)
        for index in range(6):
            pipeline.submit_trace(make_trace(f"full-{index}", time.monotonic()))
        self.assertTrue(finalizer_entered.wait(timeout=1.0))
        self.assertTrue(observed_queue.put_waiting_for_capacity.wait(timeout=1.0))

        def stop_pipeline():
            pipeline.stop(5, discard_pending=True)
            stop_returned.set()

        stopper = threading.Thread(target=stop_pipeline, daemon=True)
        stopper.start()
        self.assertTrue(pipeline._stop_event.wait(timeout=1.0))
        self.assertTrue(translation_exited.wait(timeout=1.0))
        self.assertFalse(stop_returned.is_set())
        self.assertEqual(final_calls, ["full-0"])

        release_finalizer.set()
        self.assertTrue(stop_returned.wait(timeout=1.0))
        stopper.join(timeout=1.0)
        self.assertEqual(final_calls, ["full-0"])
        self.assertFalse(pipeline._translation_thread.is_alive())
        self.assertFalse(pipeline._output_thread.is_alive())
        self.assertTrue(pipeline._output_queue.empty())
        with pipeline._records_lock:
            self.assertEqual(pipeline._records, {})

    def test_output_metric_failures_never_skip_finalizer_cleanup_or_next_task(self):
        condition = threading.Condition()
        final_calls = []
        removed = []
        raised_metrics = {
            ("running-metric", "running"),
            ("error-metric", "error"),
            ("success-metric", "success"),
        }

        def flaky_metric(metric):
            if (metric.trace_id, metric.outcome) in raised_metrics:
                raise RuntimeError(f"metric failed: {metric.outcome}")

        def finalizer(task):
            final_calls.append(task.trace_id)
            if task.trace_id == "error-metric":
                raise RuntimeError("expected finalizer failure")

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            object(),
            lambda *_: (),
            lambda _trace: None,
            lambda _update: None,
            flaky_metric,
            finalizer,
            lambda generation: generation == 5,
        )
        original_remove = pipeline._remove_record

        def observe_remove(trace_id, expected=None):
            original_remove(trace_id, expected)
            with condition:
                removed.append(trace_id)
                condition.notify_all()

        pipeline._remove_record = observe_remove
        pipeline.start(5)
        self.addCleanup(lambda: pipeline.stop(5, discard_pending=True))
        trace_ids = (
            "running-metric",
            "error-metric",
            "success-metric",
            "after-metric-errors",
        )
        with patch.object(source_pipeline_module.logger, "exception") as log_exception:
            for trace_id in trace_ids:
                pipeline.submit_trace(make_trace(trace_id, time.monotonic()))

            deadline = time.monotonic() + 2.0
            with condition:
                while not set(trace_ids).issubset(removed):
                    remaining = deadline - time.monotonic()
                    self.assertGreater(remaining, 0)
                    condition.wait(remaining)
            self.assertEqual(log_exception.call_count, 3)

        self.assertEqual(final_calls, list(trace_ids))
        self.assertTrue(pipeline._output_thread.is_alive())
        with pipeline._records_lock:
            self.assertEqual(pipeline._records, {})

    def test_output_running_callback_stop_skips_finalizer_until_worker_exits(self):
        pipeline_holder = {}
        stop_returned = threading.Event()
        observed_states = []
        final_calls = []

        def stopping_metric(metric):
            if metric.stage == "output" and metric.outcome == "running":
                pipeline = pipeline_holder["pipeline"]
                pipeline.stop(5, discard_pending=True)
                observed_states.append(pipeline._lifecycle_state.value)
                stop_returned.set()

        pipeline = SourcePipeline(
            PipelineSource.MIC,
            object(),
            lambda *_: (),
            lambda _trace: None,
            lambda _update: None,
            stopping_metric,
            lambda task: final_calls.append(task.trace_id),
            lambda generation: generation == 5,
        )
        pipeline_holder["pipeline"] = pipeline
        pipeline.start(5)
        pipeline.submit_trace(make_trace("output-worker-stop", time.monotonic()))
        self.assertTrue(stop_returned.wait(timeout=1.0))
        with pipeline._lifecycle_condition:
            deadline = time.monotonic() + 1.0
            while pipeline._lifecycle_state.value != "stopped":
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0)
                pipeline._lifecycle_condition.wait(remaining)

        self.assertEqual(observed_states, ["stopping"])
        self.assertEqual(final_calls, [])
        self.assertFalse(pipeline._output_thread.is_alive())

    @staticmethod
    def _blocking_finalizer(
        task,
        calls,
        entered,
        release,
    ):
        calls.append(task.trace_id)
        entered.set()
        if not release.wait(timeout=3.0):
            raise AssertionError("test did not release finalizer")


if __name__ == "__main__":
    unittest.main()
