import os
import sys
import threading
import time
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import (
    MessageFormatSnapshot,
    OutputConfigSnapshot,
    PipelineSource,
    TranscriptionTrace,
)
from models.pipeline.source_pipeline import SourcePipeline


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

        output_metrics = [item for item in metrics if item.stage == "output"]
        self.assertEqual(
            [item.outcome for item in output_metrics],
            ["running", "error", "running", "success"],
        )
        self.assertEqual(calls, ["first", "second"])
        terminal = [item for item in output_metrics if item.outcome in ("error", "success")]
        self.assertTrue(all(item.duration_ms >= 50 for item in terminal))
        self.assertTrue(pipeline._output_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
