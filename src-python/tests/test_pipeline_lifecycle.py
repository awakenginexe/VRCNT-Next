import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import controller as controller_module
import model as model_module
from controller import Controller
from model import Model
from models.pipeline.pipeline_types import PipelineSource
from models.transcription.whisper_runtime import WhisperRuntimeManager


WAIT_SECONDS = 2.0


class _RecoveryModel:
    def __init__(self):
        self.callback = None
        self.generations = {PipelineSource.MIC: 3, PipelineSource.SPEAKER: 8}
        self.active = {PipelineSource.MIC: True, PipelineSource.SPEAKER: True}
        self.recovered = []
        self.shutdown_calls = 0
        self.telemetry_calls = 0

    def setTranscriptionRecoveryCallback(self, callback):
        self.callback = callback

    def getSourcePipelineGeneration(self, source):
        return self.generations.get(source)

    def isSourcePipelineGenerationCurrent(self, source, generation):
        return self.generations.get(source) == generation

    def isTranscriptionSourceActive(self, source):
        return self.active.get(source, False)

    def recordTranscriptionRecovery(self, source, error_code):
        self.recovered.append((source, error_code))

    def shutdownTranscriptionPipelines(self):
        self.shutdown_calls += 1

    def telemetryShutdown(self):
        self.telemetry_calls += 1


class PipelineLifecycleTests(unittest.TestCase):
    def test_mainloop_stop_route_returns_after_translation_and_output_workers_exit(self):
        from mainloop import Main

        release = threading.Event()
        entered = [threading.Event(), threading.Event()]

        def worker(index):
            entered[index].set()
            release.wait(WAIT_SECONDS)

        workers = [
            threading.Thread(target=worker, args=(0,), name="fake-translation"),
            threading.Thread(target=worker, args=(1,), name="fake-output"),
        ]
        for worker_thread in workers:
            worker_thread.start()
        for entered_event in entered:
            self.assertTrue(entered_event.wait(WAIT_SECONDS))

        class FakeController:
            def shutdown(self):
                release.set()
                for worker_thread in workers:
                    worker_thread.join()
                return {"status": 200, "result": True}

        main = Main(FakeController(), {}, worker_count=0)
        main.stop()

        self.assertTrue(main._stop_event.is_set())
        self.assertTrue(all(not worker_thread.is_alive() for worker_thread in workers))

    def test_all_transcription_runtime_setters_route_exactly_one_restart(self):
        controller = Controller()
        controller.run_mapping = {
            "selected_transcription_compute_type": "/compute-type",
        }
        restart = Mock()
        controller._requestCoordinatedTranscriptionRestart = restart
        with (
            patch.object(
                controller,
                "_normalizeTranscriptionRuntimeSelection",
                return_value=False,
            ),
            patch.object(
                controller,
                "_normalizeSelectedYourLanguageForTranscription",
                return_value=False,
            ),
        ):
            calls = (
                lambda: controller.setSelectedTranscriptionComputeDevice(
                    controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE
                ),
                lambda: controller.setSelectedTranscriptionEngine(
                    controller_module.config.SELECTED_TRANSCRIPTION_ENGINE
                ),
                lambda: controller.setWhisperWeightType(
                    controller_module.config.WHISPER_WEIGHT_TYPE
                ),
                lambda: controller.setWhisperDecodingProfile(
                    controller_module.config.WHISPER_DECODING_PROFILE
                ),
                lambda: controller.setVoskWeightType(
                    controller_module.config.VOSK_WEIGHT_TYPE
                ),
                lambda: controller.setParakeetWeightType(
                    controller_module.config.PARAKEET_WEIGHT_TYPE
                ),
                lambda: controller.setSenseVoiceWeightType(
                    controller_module.config.SENSEVOICE_WEIGHT_TYPE
                ),
                lambda: controller.setSelectedTranscriptionComputeType(
                    controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE
                ),
            )
            for invoke in calls:
                with self.subTest(setter=invoke):
                    restart.reset_mock()
                    invoke()
                    restart.assert_called_once_with()
        controller.shutdown()

    def test_recorder_recovery_preserves_pipeline_worker_queue_and_lease(self):
        instance = object.__new__(Model)
        instance._inited = True
        instance._source_session_lock = threading.RLock()
        instance._source_pipeline_generations = {PipelineSource.MIC: 4}
        instance._source_pipeline_generation_counters = {PipelineSource.MIC: 4}
        pipeline = object()
        instance.mic_source_pipeline = pipeline
        instance.speaker_source_pipeline = None
        instance.transcription_pipeline_metrics = []
        instance._source_heartbeat_timestamps = {}
        queue = model_module._MetricAudioQueue(
            PipelineSource.MIC,
            instance._emitTranscriptionLifecycleMetric,
        )
        lease = object()
        worker = object()
        stop_event = threading.Event()
        stopped = []

        class Recorder:
            source = SimpleNamespace()

            def __init__(self, name):
                self.name = name

            def resume(self):
                pass

            def stop(self, *_args):
                stopped.append(self.name)

            def recordIntoQueue(self, offered_queue, _energy, **callbacks):
                self.offered_queue = offered_queue
                self.callbacks = callbacks

        old_recorder = Recorder("old")
        new_recorder = Recorder("new")
        session = {
            "generation": 4,
            "callback": lambda result: None,
            "audio_queue": queue,
            "recorder": old_recorder,
            "recorder_factory": lambda: new_recorder,
            "transcriber": object(),
            "worker": worker,
            "lease": lease,
            "stop_event": stop_event,
            "heartbeat_at": 1.0,
        }
        instance._source_transcription_sessions = {PipelineSource.MIC: session}
        instance.mic_audio_recorder = old_recorder

        self.assertTrue(instance.restartRecorder(PipelineSource.MIC, 4))

        self.assertEqual(stopped, ["old"])
        self.assertIs(instance.mic_source_pipeline, pipeline)
        self.assertIs(session["audio_queue"], queue)
        self.assertIs(session["worker"], worker)
        self.assertIs(session["lease"], lease)
        self.assertIs(session["recorder"], new_recorder)
        self.assertIs(new_recorder.offered_queue, queue)
        self.assertIn(
            (PipelineSource.MIC, "capture", "recovered"),
            {
                (event.source, event.stage, event.outcome)
                for event in instance.transcription_pipeline_metrics
            },
        )

    def test_shutdown_order_is_recorder_queue_worker_pipeline_lease_then_manager(self):
        instance = object.__new__(Model)
        instance._inited = True
        instance._source_session_lock = threading.RLock()
        instance._source_transcription_sessions = {}
        instance._source_heartbeat_timestamps = {}
        instance._source_pipeline_generations = {PipelineSource.MIC: 6}
        order = []

        class Recorder:
            def resume(self):
                pass

            def stop(self, *_args):
                order.append("recorder")

        class AudioQueue:
            def close(self):
                order.append("audio-queue")

        class Pipeline:
            def stop(self, generation, discard_pending=True):
                order.append(("source-pipeline", generation, discard_pending))

        class Lease:
            def close(self):
                order.append("lease")

        instance.mic_transcript_stop_event = threading.Event()
        instance.mic_audio_recorder = Recorder()
        instance.mic_audio_queue = AudioQueue()
        instance.mic_print_transcript = object()
        instance.mic_transcriber = object()
        instance.mic_whisper_runtime_lease = Lease()
        instance.mic_source_pipeline = Pipeline()
        instance.speaker_source_pipeline = None
        instance._requestTranscriptThreadStop = (
            lambda _thread: order.append("transcription-worker") or True
        )
        instance.stopSpeakerTranscript = lambda: order.append("speaker-stopped")
        instance.whisper_runtime_manager = SimpleNamespace(
            shutdown=lambda: order.append("runtime-manager")
        )

        instance.shutdownTranscriptionPipelines()

        self.assertEqual(
            order,
            [
                "recorder",
                "audio-queue",
                "transcription-worker",
                ("source-pipeline", 6, True),
                "lease",
                "speaker-stopped",
                "runtime-manager",
            ],
        )

    def test_blocked_old_generation_prevents_replacement_start_until_join_returns(self):
        fake_model = _RecoveryModel()
        release_old = threading.Event()
        old_join_entered = threading.Event()
        restart_done = threading.Event()
        calls = []
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            self.addCleanup(controller.shutdown)

            def stop_mic():
                calls.append("stop-mic")
                old_join_entered.set()
                self.assertTrue(release_old.wait(WAIT_SECONDS))

            controller.stopTranscriptionSendMessage = stop_mic
            controller.stopTranscriptionReceiveMessage = lambda: calls.append(
                "stop-speaker"
            )
            controller.startTranscriptionSendMessage = lambda: calls.append(
                "start-mic"
            )
            controller.startTranscriptionReceiveMessage = lambda: calls.append(
                "start-speaker"
            )
            restart_thread = threading.Thread(
                target=lambda: (
                    controller._requestCoordinatedTranscriptionRestart(
                        "device_changed"
                    ),
                    restart_done.set(),
                )
            )
            restart_thread.start()
            self.assertTrue(old_join_entered.wait(WAIT_SECONDS))
            self.assertEqual(calls, ["stop-mic"])
            release_old.set()
            self.assertTrue(restart_done.wait(WAIT_SECONDS))
            restart_thread.join()

        self.assertEqual(
            calls,
            ["stop-mic", "stop-speaker", "start-mic", "start-speaker"],
        )

    def test_model_reuses_one_runtime_for_matching_mic_and_speaker_leases(self):
        instance = object.__new__(Model)
        loads = []
        unloads = []
        instance.whisper_runtime_manager = WhisperRuntimeManager(
            factory=lambda root, key: loads.append((root, key)) or object(),
            unload=unloads.append,
        )

        fake_config = SimpleNamespace(
            SELECTED_TRANSCRIPTION_ENGINE="Whisper",
            PATH_DATA="data-root",
            WHISPER_WEIGHT_TYPE="tiny",
            SELECTED_TRANSCRIPTION_COMPUTE_DEVICE={"device": "cpu", "device_index": 0},
            SELECTED_TRANSCRIPTION_COMPUTE_TYPE="int8",
        )
        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
            patch.object(model_module, "resolveWhisperComputeType", return_value="int8"),
        ):
            mic = instance._acquireWhisperRuntimeLease()
            speaker = instance._acquireWhisperRuntimeLease()

        self.assertEqual(len(loads), 1)
        mic.close()
        self.assertEqual(unloads, [])
        speaker.close()
        self.assertEqual(len(unloads), 1)

    def test_recovery_coordinator_waits_for_safe_event_and_ignores_stale_generation(self):
        fake_model = _RecoveryModel()
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            self.addCleanup(controller.shutdown)
            restarted = threading.Event()
            reasons = []

            def restart(reason="configuration_changed"):
                reasons.append(reason)
                restarted.set()

            controller._requestCoordinatedTranscriptionRestart = restart
            safe = threading.Event()
            fake_model.callback(
                PipelineSource.MIC,
                3,
                "whisper_inference_failed",
                safe,
            )
            self.assertFalse(restarted.is_set())
            safe.set()
            self.assertTrue(restarted.wait(WAIT_SECONDS))
            self.assertEqual(reasons, ["whisper_inference_failed"])
            self.assertEqual(
                fake_model.recovered,
                [(PipelineSource.MIC, "whisper_inference_failed")],
            )

            restarted.clear()
            stale_safe = threading.Event()
            stale_safe.set()
            fake_model.callback(
                PipelineSource.SPEAKER,
                7,
                "whisper_inference_failed",
                stale_safe,
            )
            self.assertFalse(restarted.wait(0.1))

    def test_coordinated_restart_snapshots_active_sources_and_restarts_each_once(self):
        fake_model = _RecoveryModel()
        calls = []
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            self.addCleanup(controller.shutdown)
            controller.stopTranscriptionSendMessage = lambda: calls.append("stop-mic")
            controller.stopTranscriptionReceiveMessage = lambda: calls.append("stop-speaker")
            controller.startTranscriptionSendMessage = lambda: calls.append("start-mic")
            controller.startTranscriptionReceiveMessage = lambda: calls.append("start-speaker")

            controller._requestCoordinatedTranscriptionRestart("weight_changed")

        self.assertEqual(
            calls,
            ["stop-mic", "stop-speaker", "start-mic", "start-speaker"],
        )

    def test_controller_shutdown_stops_pipelines_before_telemetry(self):
        fake_model = _RecoveryModel()
        order = []
        fake_model.shutdownTranscriptionPipelines = lambda: order.append("pipelines")
        fake_model.telemetryShutdown = lambda: order.append("telemetry")
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            response = controller.shutdown()

        self.assertEqual(response, {"status": 200, "result": True})
        self.assertEqual(order, ["pipelines", "telemetry"])
        self.assertFalse(controller._transcription_recovery_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
