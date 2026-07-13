import os
import sys
import threading
import time
import unittest
from queue import Queue


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


def output_config():
    fmt = MessageFormatSnapshot("", "", "", "", " / ", " | ", False)
    return OutputConfigSnapshot(
        "1", True, True, False, False, False, False, False, False, False,
        False, False, False,
        (LanguageSlotSnapshot("your-1", "English", "US", True),),
        (), (), fmt, fmt,
    )


def trace(trace_id, targets=None, providers=("provider",)):
    return TranscriptionTrace(
        trace_id, 11, PipelineSource.SPEAKER, trace_id, "English", (),
        targets if targets is not None else (TranslationTarget("slot", "French", "France"),),
        providers, "Small", (), time.monotonic(), output_config(),
    )


class Harness:
    def __init__(self):
        self.condition = threading.Condition()
        self.initial = []
        self.updates = []
        self.metrics = []
        self.finals = []

    def append(self, collection, value):
        with self.condition:
            collection.append(value)
            self.condition.notify_all()

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True


class BlockingTranslator:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def translateAttempt(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            self.entered.set()
            if not self.release.wait(timeout=3.0):
                raise AssertionError("test did not release provider")
        return TranslationAttempt(
            TranslationStatus.SUCCESS, kwargs["translator_name"],
            "translated:" + kwargs["message"], 1, None,
        )


class ObservedOutputQueue(Queue):
    def __init__(self):
        super().__init__(maxsize=4)
        self.put_waiting_for_capacity = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.full():
            self.put_waiting_for_capacity.set()
        return super().put(item, block=block, timeout=timeout)


class ProgressivePipelineTests(unittest.TestCase):
    def make_pipeline(self, translator, harness, emit_final=None):
        pipeline = SourcePipeline(
            PipelineSource.SPEAKER,
            translator,
            lambda *_: (),
            lambda item: harness.append(harness.initial, item),
            lambda item: harness.append(harness.updates, item),
            lambda item: harness.append(harness.metrics, item),
            emit_final or (lambda item: harness.append(harness.finals, item)),
            lambda generation: generation == 11,
        )
        pipeline.start(11)
        self.addCleanup(lambda: pipeline.stop(11, discard_pending=True))
        return pipeline

    def test_ninth_waiting_job_displaces_exact_oldest_unstarted_slot(self):
        harness = Harness()
        translator = BlockingTranslator()
        pipeline = self.make_pipeline(translator, harness)
        pipeline.submit_trace(trace("in-flight"))
        self.assertTrue(translator.entered.wait(timeout=1.0))

        for index in range(9):
            pipeline.submit_trace(trace(f"waiting-{index}"))

        skipped = [
            item for item in harness.updates
            if item.status is TranslationStatus.SKIPPED_OVERLOAD
        ]
        self.assertEqual(
            [(item.trace_id, item.target_slot) for item in skipped],
            [("waiting-0", "slot")],
        )
        overload = [metric for metric in harness.metrics if metric.outcome == "skipped_overload"]
        self.assertEqual(overload[-1].trace_id, "waiting-0")
        self.assertEqual(overload[-1].dropped_count, 1)
        self.assertEqual(
            [item.trace_id for item in harness.initial],
            ["in-flight"] + [f"waiting-{i}" for i in range(9)],
        )
        translator.release.set()
        self.assertTrue(harness.wait_for(lambda: len(harness.finals) == 10))

    def test_target_slots_aggregate_independently_and_preserve_target_order(self):
        harness = Harness()
        translator = BlockingTranslator()
        translator.release.set()
        pipeline = self.make_pipeline(translator, harness)
        targets = (
            TranslationTarget("second-name", "French", "France"),
            TranslationTarget("first-name", "German", "Germany"),
        )

        pipeline.submit_trace(trace("two-slots", targets=targets))
        self.assertTrue(harness.wait_for(lambda: len(harness.finals) == 1))

        final = harness.finals[0]
        self.assertEqual(final.targets, targets)
        self.assertEqual(
            [item.target_slot for item in final.translations],
            ["second-name", "first-name"],
        )
        self.assertEqual(len(harness.finals), 1)

    def test_bounded_admission_rejects_seventeenth_while_output_is_blocked(self):
        harness = Harness()
        translator = BlockingTranslator()
        translator.release.set()
        finalizer_entered = threading.Event()
        release_finalizer = threading.Event()

        def blocked_finalizer(item):
            finalizer_entered.set()
            if not release_finalizer.wait(timeout=3.0):
                raise AssertionError("test did not release finalizer")
            harness.append(harness.finals, item)

        pipeline = self.make_pipeline(translator, harness, blocked_finalizer)
        for index in range(16):
            pipeline.submit_trace(trace(f"active-{index}", targets=()))
        self.assertTrue(finalizer_entered.wait(timeout=1.0))

        admission_returned = threading.Event()

        def submit_seventeenth():
            pipeline.submit_trace(trace("rejected-17"))
            admission_returned.set()

        submitter = threading.Thread(target=submit_seventeenth, daemon=True)
        submitter.start()
        self.assertTrue(admission_returned.wait(timeout=1.0))
        self.assertFalse(release_finalizer.is_set())
        self.assertEqual(harness.initial[-1].trace_id, "rejected-17")
        rejected_updates = [item for item in harness.updates if item.trace_id == "rejected-17"]
        self.assertEqual(
            [item.status for item in rejected_updates],
            [TranslationStatus.SKIPPED_OVERLOAD],
        )
        rejected_metrics = [item for item in harness.metrics if item.trace_id == "rejected-17"]
        self.assertEqual([item.outcome for item in rejected_metrics], ["skipped_overload"])
        self.assertEqual(len(translator.calls), 0)
        release_finalizer.set()
        submitter.join(timeout=1.0)

    def test_full_output_queue_never_backpressures_zero_target_or_displacing_submitters(self):
        harness = Harness()
        translator = BlockingTranslator()
        translator.release.set()
        finalizer_entered = threading.Event()
        release_finalizer = threading.Event()

        def blocked_finalizer(item):
            finalizer_entered.set()
            if not release_finalizer.wait(timeout=3.0):
                raise AssertionError("test did not release finalizer")
            harness.append(harness.finals, item)

        pipeline = SourcePipeline(
            PipelineSource.SPEAKER,
            translator,
            lambda *_: (),
            lambda item: harness.append(harness.initial, item),
            lambda item: harness.append(harness.updates, item),
            lambda item: harness.append(harness.metrics, item),
            blocked_finalizer,
            lambda generation: generation == 11,
        )
        observed_queue = ObservedOutputQueue()
        pipeline._output_queue = observed_queue
        pipeline.start(11)
        self.addCleanup(lambda: pipeline.stop(11, discard_pending=True))

        pipeline.submit_trace(trace("output-0", targets=()))
        self.assertTrue(finalizer_entered.wait(timeout=1.0))
        for index in range(1, 6):
            pipeline.submit_trace(trace(f"output-{index}", targets=()))
        self.assertTrue(observed_queue.put_waiting_for_capacity.wait(timeout=1.0))

        submissions_returned = threading.Event()

        def submit_without_output_capacity():
            pipeline.submit_trace(trace("ready-zero", targets=()))
            for index in range(9):
                pipeline.submit_trace(trace(f"queued-{index}"))
            submissions_returned.set()

        submitter = threading.Thread(target=submit_without_output_capacity, daemon=True)
        submitter.start()
        self.assertTrue(submissions_returned.wait(timeout=1.0))
        self.assertFalse(release_finalizer.is_set())
        skipped = [
            item for item in harness.updates
            if item.status is TranslationStatus.SKIPPED_OVERLOAD
            and item.error_code == "translation_queue_overload"
        ]
        self.assertEqual(
            [(item.trace_id, item.target_slot) for item in skipped],
            [("queued-0", "slot")],
        )
        self.assertIn("ready-zero", [item.trace_id for item in harness.initial])
        self.assertIn("queued-8", [item.trace_id for item in harness.initial])
        release_finalizer.set()
        submitter.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
