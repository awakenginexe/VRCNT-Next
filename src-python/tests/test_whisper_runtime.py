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


def pause_condition_waiter_after_wake(manager, thread_name):
    """Pause one waiter after wake while allowing other condition users through."""
    paused = threading.Event()
    resume = threading.Event()
    original_wait = manager._condition.wait
    pause_armed = True

    def observed_wait(timeout=None):
        nonlocal pause_armed
        result = original_wait(timeout)
        if pause_armed and threading.current_thread().name == thread_name:
            pause_armed = False
            manager._condition.release()
            try:
                paused.set()
                resume.wait()
            finally:
                manager._condition.acquire()
        return result

    manager._condition.wait = observed_wait
    return paused, resume


class WhisperRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.key_a = WhisperRuntimeKey("tiny", "cpu", 0, "int8")
        self.key_b = WhisperRuntimeKey("base", "cpu", 0, "int8")

    def assert_thread_finished(self, thread, completed, outcome):
        self.assertTrue(completed.wait(WAIT_SECONDS), f"{thread.name} did not finish")
        thread.join()
        if "error" in outcome:
            raise outcome["error"]

    def assert_thread_failed(self, thread, completed, outcome, error_type):
        self.assertTrue(completed.wait(WAIT_SECONDS), f"{thread.name} did not finish")
        thread.join()
        self.assertIsInstance(outcome.get("error"), error_type)

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

    def test_closing_waiting_lease_wakes_before_other_inference_finishes(self):
        factory = RecordingFactory(block_audio="active")
        unloaded = []
        manager = WhisperRuntimeManager(factory=factory, unload=unloaded.append)
        waiting_lease = manager.acquire("app-root", self.key_a)
        active_lease = manager.acquire("app-root", self.key_a)
        model = factory.models[0]
        self.addCleanup(model.release_iterator.set)

        active_thread, active_done, active_outcome = run_in_thread(
            "active-inference", lambda: active_lease.transcribe("active")
        )
        self.assertTrue(model.iterator_entered.wait(WAIT_SECONDS))

        lease_waiting = observe_condition_wait(manager, "closing-lease-inference")
        waiting_thread, waiting_done, waiting_outcome = run_in_thread(
            "closing-lease-inference", lambda: waiting_lease.transcribe("waiting")
        )
        self.assertTrue(lease_waiting.wait(WAIT_SECONDS))

        waiting_lease.close()

        self.assert_thread_failed(
            waiting_thread,
            waiting_done,
            waiting_outcome,
            WhisperRuntimeClosed,
        )
        self.assertFalse(active_done.is_set())
        self.assertEqual(unloaded, [])

        model.release_iterator.set()
        self.assert_thread_finished(active_thread, active_done, active_outcome)
        active_lease.close()
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

    def test_final_close_unload_failure_retains_ownership_until_shutdown_retry(self):
        factory = RecordingFactory()
        unload_attempts = []

        def fail_once_unload(model):
            unload_attempts.append(model)
            if len(unload_attempts) == 1:
                raise RuntimeError("first unload failed")

        manager = WhisperRuntimeManager(factory=factory, unload=fail_once_unload)
        lease = manager.acquire("app-root", self.key_a)
        model = factory.models[0]

        with self.assertRaisesRegex(RuntimeError, "first unload failed"):
            lease.close()

        self.assertEqual(unload_attempts, [model])
        with self.assertRaises(WhisperRuntimeBusy):
            manager.acquire("app-root", self.key_b)
        with self.assertRaises(WhisperRuntimeBusy):
            manager.acquire("app-root", self.key_a)
        self.assertEqual(len(factory.calls), 1)

        manager.shutdown()

        self.assertEqual(unload_attempts, [model, model])
        self.assertEqual(len(factory.calls), 1)
        with self.assertRaises(WhisperRuntimeClosed):
            manager.acquire("app-root", self.key_a)
        manager.shutdown()
        self.assertEqual(unload_attempts, [model, model])

    def test_shutdown_unload_failure_is_retryable_without_losing_model(self):
        factory = RecordingFactory()
        unload_attempts = []

        def fail_once_unload(model):
            unload_attempts.append(model)
            if len(unload_attempts) == 1:
                raise RuntimeError("shutdown unload failed")

        manager = WhisperRuntimeManager(factory=factory, unload=fail_once_unload)
        lease = manager.acquire("app-root", self.key_a)
        model = factory.models[0]

        with self.assertRaisesRegex(RuntimeError, "shutdown unload failed"):
            manager.shutdown()

        self.assertEqual(unload_attempts, [model])
        self.assertTrue(lease.closed)
        with self.assertRaises(WhisperRuntimeClosed):
            manager.acquire("app-root", self.key_a)
        with self.assertRaises(WhisperRuntimeClosed):
            lease.transcribe("future")

        manager.shutdown()
        manager.shutdown()

        self.assertEqual(unload_attempts, [model, model])
        self.assertEqual(len(factory.calls), 1)

    def test_unload_callback_runs_outside_manager_condition(self):
        callback_api_completed = threading.Event()
        callback_api_outcome = {}
        callback_api_threads = []
        manager = None
        lease = None

        def reentrant_unload(_model):
            def read_lease_state():
                try:
                    callback_api_outcome["closed"] = lease.closed
                except BaseException as error:
                    callback_api_outcome["error"] = error
                finally:
                    callback_api_completed.set()

            api_thread = threading.Thread(
                name="unload-manager-api",
                target=read_lease_state,
                daemon=True,
            )
            callback_api_threads.append(api_thread)
            api_thread.start()
            if not callback_api_completed.wait(WAIT_SECONDS):
                raise AssertionError("unload callback ran while holding manager condition")

        factory = RecordingFactory()
        manager = WhisperRuntimeManager(factory=factory, unload=reentrant_unload)
        lease = manager.acquire("app-root", self.key_a)

        lease.close()

        for api_thread in callback_api_threads:
            api_thread.join()
        if "error" in callback_api_outcome:
            raise callback_api_outcome["error"]
        self.assertTrue(callback_api_outcome["closed"])

    def test_close_and_shutdown_join_one_unload_operation(self):
        factory = RecordingFactory(block_audio="blocked")
        unload_entered = threading.Event()
        release_unload = threading.Event()
        unload_attempts = []

        def blocking_unload(model):
            unload_attempts.append(model)
            unload_entered.set()
            release_unload.wait()

        manager = WhisperRuntimeManager(factory=factory, unload=blocking_unload)
        lease = manager.acquire("app-root", self.key_a)
        model = factory.models[0]
        self.addCleanup(model.release_iterator.set)
        self.addCleanup(release_unload.set)

        inference_thread, inference_done, inference_outcome = run_in_thread(
            "converging-inference", lambda: lease.transcribe("blocked")
        )
        self.assertTrue(model.iterator_entered.wait(WAIT_SECONDS))

        close_waiting = observe_condition_wait(manager, "converging-close")
        close_thread, close_done, close_outcome = run_in_thread(
            "converging-close", lease.close
        )
        self.assertTrue(close_waiting.wait(WAIT_SECONDS))

        shutdown_waiting = observe_condition_wait(manager, "converging-shutdown")
        shutdown_thread, shutdown_done, shutdown_outcome = run_in_thread(
            "converging-shutdown", manager.shutdown
        )
        self.assertTrue(shutdown_waiting.wait(WAIT_SECONDS))

        model.release_iterator.set()
        self.assertTrue(unload_entered.wait(WAIT_SECONDS))
        self.assertEqual(unload_attempts, [model])
        release_unload.set()

        self.assert_thread_finished(inference_thread, inference_done, inference_outcome)
        self.assert_thread_finished(close_thread, close_done, close_outcome)
        self.assert_thread_finished(shutdown_thread, shutdown_done, shutdown_outcome)
        self.assertEqual(unload_attempts, [model])

    def test_draining_joiner_observes_fast_unload_failure(self):
        factory = RecordingFactory(block_audio="blocked")
        unload_attempts = []

        def fail_once_unload(model):
            unload_attempts.append(model)
            if len(unload_attempts) == 1:
                raise RuntimeError("fast unload failed")

        manager = WhisperRuntimeManager(factory=factory, unload=fail_once_unload)
        lease = manager.acquire("app-root", self.key_a)
        model = factory.models[0]
        self.addCleanup(model.release_iterator.set)

        inference_thread, inference_done, inference_outcome = run_in_thread(
            "fast-failure-inference", lambda: lease.transcribe("blocked")
        )
        self.assertTrue(model.iterator_entered.wait(WAIT_SECONDS))

        close_waiting = observe_condition_wait(manager, "fast-failure-close")
        close_thread, close_done, close_outcome = run_in_thread(
            "fast-failure-close", lease.close
        )
        self.assertTrue(close_waiting.wait(WAIT_SECONDS))

        shutdown_paused, resume_shutdown = pause_condition_waiter_after_wake(
            manager,
            "fast-failure-shutdown",
        )
        self.addCleanup(resume_shutdown.set)
        shutdown_thread, shutdown_done, shutdown_outcome = run_in_thread(
            "fast-failure-shutdown", manager.shutdown
        )

        model.release_iterator.set()
        self.assertTrue(shutdown_paused.wait(WAIT_SECONDS))
        self.assert_thread_failed(
            close_thread,
            close_done,
            close_outcome,
            RuntimeError,
        )
        self.assertFalse(shutdown_done.is_set())

        resume_shutdown.set()
        self.assert_thread_finished(inference_thread, inference_done, inference_outcome)
        self.assert_thread_failed(
            shutdown_thread,
            shutdown_done,
            shutdown_outcome,
            RuntimeError,
        )
        self.assertEqual(unload_attempts, [model])

        manager.shutdown()
        self.assertEqual(unload_attempts, [model, model])

    def test_stale_closed_lease_does_not_join_later_model_unload(self):
        factory = RecordingFactory()
        second_unload_entered = threading.Event()
        release_second_unload = threading.Event()
        unload_attempts = []

        def block_second_unload(model):
            unload_attempts.append(model)
            if len(unload_attempts) == 2:
                second_unload_entered.set()
                release_second_unload.wait()

        manager = WhisperRuntimeManager(factory=factory, unload=block_second_unload)
        stale_lease = manager.acquire("app-root", self.key_a)
        stale_lease.close()
        current_lease = manager.acquire("app-root", self.key_b)
        current_model = factory.models[-1]
        self.addCleanup(release_second_unload.set)

        current_close, current_done, current_outcome = run_in_thread(
            "current-close", current_lease.close
        )
        self.assertTrue(second_unload_entered.wait(WAIT_SECONDS))

        stale_close, stale_done, stale_outcome = run_in_thread(
            "stale-close", stale_lease.close
        )
        self.assert_thread_finished(stale_close, stale_done, stale_outcome)
        self.assertFalse(current_done.is_set())
        self.assertEqual(unload_attempts[-1], current_model)

        release_second_unload.set()
        self.assert_thread_finished(current_close, current_done, current_outcome)

    def test_factory_and_transcribe_exceptions_leave_manager_recoverable(self):
        factory_calls = []
        models = []

        class RecoverableModel(FakeWhisperModel):
            def transcribe(self, audio, **options):
                if audio == "raise":
                    raise RuntimeError("transcribe failed")
                return super().transcribe(audio, **options)

        def fail_once_factory(root, key):
            factory_calls.append((root, key))
            if len(factory_calls) == 1:
                raise RuntimeError("factory failed")
            model = RecoverableModel()
            models.append(model)
            return model

        unloaded = []
        manager = WhisperRuntimeManager(factory=fail_once_factory, unload=unloaded.append)

        with self.assertRaisesRegex(RuntimeError, "factory failed"):
            manager.acquire("app-root", self.key_a)

        lease = manager.acquire("app-root", self.key_a)
        with self.assertRaisesRegex(RuntimeError, "transcribe failed"):
            lease.transcribe("raise")
        result = lease.transcribe("recovered")

        self.assertEqual(
            result,
            WhisperInferenceResult(("segment:recovered",), "info:recovered"),
        )
        lease.close()
        self.assertEqual(unloaded, models)


if __name__ == "__main__":
    unittest.main()
