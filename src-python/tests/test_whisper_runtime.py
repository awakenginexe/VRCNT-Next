import os
import sys
import threading
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.transcription.whisper_runtime import (
    WhisperInferenceResult,
    WhisperRuntimeBusy,
    WhisperRuntimeClosed,
    WhisperRuntimeKey,
    WhisperRuntimeManager,
)
from models.transcription.transcription_whisper import unloadWhisperModel


WAIT_SECONDS = 2


class FakeWhisperModel:
    def __init__(self, block_audio=None):
        self.block_audio = block_audio
        self.iterator_entered = threading.Event()
        self.release_iterator = threading.Event()
        self.transcribe_calls = []
        self.second_model_entry = threading.Event()

    def transcribe(self, audio, **options):
        self.transcribe_calls.append((audio, options))
        if audio == "second":
            self.second_model_entry.set()

        def segments():
            if audio == self.block_audio:
                self.iterator_entered.set()
                self.release_iterator.wait()
            yield f"segment:{audio}"

        return segments(), f"info:{audio}"


class RecordingFactory:
    def __init__(self, block_audio=None):
        self.block_audio = block_audio
        self.calls = []
        self.models = []

    def __call__(self, root, key):
        self.calls.append((root, key))
        model = FakeWhisperModel(block_audio=self.block_audio)
        self.models.append(model)
        return model


def run_in_thread(name, operation):
    completed = threading.Event()
    outcome = {}

    def target():
        try:
            outcome["result"] = operation()
        except BaseException as error:
            outcome["error"] = error
        finally:
            completed.set()

    thread = threading.Thread(name=name, target=target, daemon=True)
    thread.start()
    return thread, completed, outcome


def observe_condition_wait(manager, thread_name):
    """Return an Event set when a named thread actually waits on the manager."""
    waiting = threading.Event()
    original_wait = manager._condition.wait

    def observed_wait(timeout=None):
        if threading.current_thread().name == thread_name:
            waiting.set()
        return original_wait(timeout)

    manager._condition.wait = observed_wait
    return waiting


class WhisperRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.key_a = WhisperRuntimeKey("tiny", "cpu", 0, "int8")
        self.key_b = WhisperRuntimeKey("base", "cpu", 0, "int8")

    def assert_thread_finished(self, thread, completed, outcome):
        self.assertTrue(completed.wait(WAIT_SECONDS), f"{thread.name} did not finish")
        thread.join()
        if "error" in outcome:
            raise outcome["error"]

    def test_matching_leases_share_model_and_final_close_unloads_once(self):
        factory = RecordingFactory()
        unloaded = []
        manager = WhisperRuntimeManager(factory=factory, unload=unloaded.append)

        first = manager.acquire("app-root", self.key_a)
        second = manager.acquire("app-root", self.key_a)

        self.assertEqual(factory.calls, [("app-root", self.key_a)])
        self.assertEqual(first.key, self.key_a)
        self.assertEqual(second.key, self.key_a)

        first.close()
        first.close()
        self.assertEqual(unloaded, [])

        second.close()
        second.close()
        self.assertEqual(unloaded, factory.models)

    def test_default_unload_calls_native_model_unload(self):
        unload_calls = []

        class NativeModel:
            def unload_model(self):
                unload_calls.append("unloaded")

        class Wrapper:
            model = NativeModel()

        unloadWhisperModel(Wrapper())

        self.assertEqual(unload_calls, ["unloaded"])

    def test_inference_is_serialized_through_lazy_segment_materialization(self):
        factory = RecordingFactory(block_audio="first")
        unloaded = []
        manager = WhisperRuntimeManager(factory=factory, unload=unloaded.append)
        first = manager.acquire("app-root", self.key_a)
        second = manager.acquire("app-root", self.key_a)
        model = factory.models[0]
        self.addCleanup(model.release_iterator.set)

        first_thread, first_done, first_outcome = run_in_thread(
            "first-inference", lambda: first.transcribe("first", beam_size=1)
        )
        self.assertTrue(model.iterator_entered.wait(WAIT_SECONDS))

        second_waiting = observe_condition_wait(manager, "second-inference")
        second_thread, second_done, second_outcome = run_in_thread(
            "second-inference", lambda: second.transcribe("second", beam_size=2)
        )
        self.assertTrue(second_waiting.wait(WAIT_SECONDS))
        self.assertFalse(model.second_model_entry.is_set())

        model.release_iterator.set()
        self.assert_thread_finished(first_thread, first_done, first_outcome)
        self.assert_thread_finished(second_thread, second_done, second_outcome)

        self.assertEqual(
            first_outcome["result"],
            WhisperInferenceResult(("segment:first",), "info:first"),
        )
        self.assertEqual(
            second_outcome["result"],
            WhisperInferenceResult(("segment:second",), "info:second"),
        )
        self.assertEqual(
            model.transcribe_calls,
            [
                ("first", {"beam_size": 1}),
                ("second", {"beam_size": 2}),
            ],
        )

        first.close()
        second.close()
        self.assertEqual(unloaded, [model])

    def test_different_key_waits_for_final_close_and_inference_before_replacement(self):
        factory = RecordingFactory(block_audio="blocked")
        unloaded = []
        manager = WhisperRuntimeManager(factory=factory, unload=unloaded.append)
        lease = manager.acquire("app-root", self.key_a)
        model_a = factory.models[0]
        self.addCleanup(model_a.release_iterator.set)

        inference_thread, inference_done, inference_outcome = run_in_thread(
            "blocked-inference", lambda: lease.transcribe("blocked")
        )
        self.assertTrue(model_a.iterator_entered.wait(WAIT_SECONDS))

        close_waiting = observe_condition_wait(manager, "final-close")
        close_thread, close_done, close_outcome = run_in_thread(
            "final-close", lease.close
        )
        self.assertTrue(close_waiting.wait(WAIT_SECONDS))
        self.assertEqual(unloaded, [])
        with self.assertRaises(WhisperRuntimeBusy):
            manager.acquire("app-root", self.key_b)
        self.assertEqual(len(factory.calls), 1)

        model_a.release_iterator.set()
        self.assert_thread_finished(inference_thread, inference_done, inference_outcome)
        self.assert_thread_finished(close_thread, close_done, close_outcome)
        self.assertEqual(unloaded, [model_a])

        lease.close()
        self.assertEqual(unloaded, [model_a])
        replacement = manager.acquire("app-root", self.key_b)
        self.assertEqual(factory.calls[-1], ("app-root", self.key_b))
        with self.assertRaises(WhisperRuntimeClosed):
            lease.transcribe("stale")
        replacement.close()
        self.assertEqual(unloaded, factory.models)

    def test_shutdown_invalidates_leases_waits_for_inference_and_unloads_once(self):
        factory = RecordingFactory(block_audio="blocked")
        unloaded = []
        manager = WhisperRuntimeManager(factory=factory, unload=unloaded.append)
        active_lease = manager.acquire("app-root", self.key_a)
        idle_lease = manager.acquire("app-root", self.key_a)
        model = factory.models[0]
        self.addCleanup(model.release_iterator.set)

        inference_thread, inference_done, inference_outcome = run_in_thread(
            "shutdown-inference", lambda: active_lease.transcribe("blocked")
        )
        self.assertTrue(model.iterator_entered.wait(WAIT_SECONDS))

        shutdown_waiting = observe_condition_wait(manager, "shutdown")
        shutdown_thread, shutdown_done, shutdown_outcome = run_in_thread(
            "shutdown", manager.shutdown
        )
        self.assertTrue(shutdown_waiting.wait(WAIT_SECONDS))
        self.assertEqual(unloaded, [])
        with self.assertRaises(WhisperRuntimeClosed):
            manager.acquire("app-root", self.key_a)
        with self.assertRaises(WhisperRuntimeClosed):
            idle_lease.transcribe("future")

        model.release_iterator.set()
        self.assert_thread_finished(inference_thread, inference_done, inference_outcome)
        self.assert_thread_finished(shutdown_thread, shutdown_done, shutdown_outcome)
        self.assertEqual(unloaded, [model])

        with self.assertRaises(WhisperRuntimeClosed):
            active_lease.transcribe("future")
        active_lease.close()
        idle_lease.close()
        manager.shutdown()
        self.assertEqual(unloaded, [model])


if __name__ == "__main__":
    unittest.main()
