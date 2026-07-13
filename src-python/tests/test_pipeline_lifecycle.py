import os
import sys
import threading
import unittest
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import controller as controller_module
import model as model_module
from controller import Controller
from model import Model, threadFnc
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
from models.transcription.whisper_runtime import WhisperRuntimeManager


WAIT_SECONDS = 2.0


class _RecoveryModel:
    def __init__(self):
        self.callback = None
        self.generations = {PipelineSource.MIC: 3, PipelineSource.SPEAKER: 8}
        self.active = {PipelineSource.MIC: True, PipelineSource.SPEAKER: True}
        self.recovered = []
        self.recovery_failed = []
        self.recovery_metric_event = threading.Event()
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

    def stopMicTranscript(self):
        self.active[PipelineSource.MIC] = False
        self.generations.pop(PipelineSource.MIC, None)

    def stopSpeakerTranscript(self):
        self.active[PipelineSource.SPEAKER] = False
        self.generations.pop(PipelineSource.SPEAKER, None)

    def recordTranscriptionRecovery(self, source, error_code):
        self.recovered.append((source, error_code))
        self.recovery_metric_event.set()

    def recordTranscriptionRecoveryFailure(self, source, error_code):
        self.recovery_failed.append((source, error_code))
        self.recovery_metric_event.set()

    def shutdownTranscriptionPipelines(self):
        self.shutdown_calls += 1

    def telemetryShutdown(self):
        self.telemetry_calls += 1


class PipelineLifecycleTests(unittest.TestCase):
    def test_mainloop_stop_route_returns_after_translation_and_output_workers_exit(self):
        from mainloop import Main
        transcription_release = threading.Event()
        provider_release = threading.Event()
        finalizer_release = threading.Event()
        transcription_entered = threading.Event()
        provider_entered = threading.Event()
        finalizer_entered = threading.Event()
        recorder_stopped = threading.Event()
        main_stopped = threading.Event()
        order = []
        self.addCleanup(transcription_release.set)
        self.addCleanup(provider_release.set)
        self.addCleanup(finalizer_release.set)

        class ControlledTranslator:
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
                provider_entered.set()
                provider_release.wait()
                return TranslationAttempt(
                    TranslationStatus.SUCCESS,
                    translator_name,
                    f"translated-{message}",
                    1,
                    None,
                )

        instance = object.__new__(Model)
        instance._inited = True
        instance._ensureTranscriptionLifecycleState()
        instance.transcription_pipeline_metrics = []
        generation = 21

        def finalizer(_task):
            finalizer_entered.set()
            finalizer_release.wait()

        pipeline = SourcePipeline(
            source=PipelineSource.MIC,
            translator=ControlledTranslator(),
            transliterate=lambda *args: (),
            emit_initial=lambda trace: None,
            emit_update=lambda update: None,
            emit_metric=lambda event: None,
            emit_final=finalizer,
            is_generation_current=lambda candidate: (
                instance.isSourcePipelineGenerationCurrent(
                    PipelineSource.MIC,
                    candidate,
                )
            ),
        )
        instance.mic_source_pipeline = pipeline
        instance.speaker_source_pipeline = None
        instance._source_pipeline_generations = {PipelineSource.MIC: generation}
        instance._source_pipeline_generation_counters = {
            PipelineSource.MIC: generation,
            PipelineSource.SPEAKER: 0,
        }
        pipeline.start(generation)
        translation_worker = pipeline._translation_thread
        output_worker = pipeline._output_thread

        fmt = MessageFormatSnapshot("", "", "", "", "", "", False)
        output_config = OutputConfigSnapshot(
            "1",
            True,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            False,
            (),
            (),
            (LanguageSlotSnapshot("1", "Japanese", "Japan", True),),
            fmt,
            fmt,
        )

        def trace(trace_id, targets):
            return TranscriptionTrace(
                trace_id,
                generation,
                PipelineSource.MIC,
                trace_id,
                "English",
                (),
                targets,
                ("Google",),
                "Small",
                (),
                monotonic(),
                output_config,
            )

        self.assertTrue(pipeline.submit_trace(trace("finalizer", ())))
        self.assertTrue(finalizer_entered.wait(WAIT_SECONDS))
        target = TranslationTarget("1", "Japanese", "Japan")
        self.assertTrue(pipeline.submit_trace(trace("provider", (target,))))
        self.assertTrue(provider_entered.wait(WAIT_SECONDS))

        def transcription_call():
            transcription_entered.set()
            transcription_release.wait()

        transcription_worker = threadFnc(transcription_call)
        transcription_worker.start()
        self.assertTrue(transcription_entered.wait(WAIT_SECONDS))

        class Recorder:
            def resume(self):
                pass

            def stop(self, *_args):
                order.append("recorder")
                recorder_stopped.set()

        class Lease:
            def close(self):
                order.append("lease")

        class RuntimeManager:
            def shutdown(self):
                self_outer.assertFalse(translation_worker.is_alive())
                self_outer.assertFalse(output_worker.is_alive())
                self_outer.assertIsNone(instance.mic_source_pipeline)
                self_outer.assertNotIn(
                    PipelineSource.MIC,
                    instance._source_pipeline_generations,
                )
                order.append("runtime")

        self_outer = self
        audio_queue = model_module._MetricAudioQueue(
            PipelineSource.MIC,
            instance._emitTranscriptionLifecycleMetric,
        )
        stop_event = threading.Event()
        lease = Lease()
        instance.mic_audio_recorder = Recorder()
        instance.mic_audio_queue = audio_queue
        instance.mic_print_transcript = transcription_worker
        instance.mic_transcriber = object()
        instance.mic_transcript_stop_event = stop_event
        instance.mic_whisper_runtime_lease = lease
        instance.speaker_audio_recorder = None
        instance.speaker_audio_queue = None
        instance.speaker_print_transcript = None
        instance.speaker_transcriber = None
        instance.speaker_transcript_stop_event = None
        instance.speaker_whisper_runtime_lease = None
        instance.whisper_runtime_manager = RuntimeManager()
        instance.telemetry = SimpleNamespace(
            shutdown=lambda: order.append("telemetry")
        )
        instance._source_transcription_sessions = {
            PipelineSource.MIC: {
                "generation": generation,
                "worker": transcription_worker,
                "stop_event": stop_event,
            }
        }

        with patch.object(controller_module, "model", instance):
            controller = Controller()
            main = Main(controller, {}, worker_count=0)
            stop_thread = threading.Thread(
                target=lambda: (main.stop(), main_stopped.set()),
                name="controlled-main-stop",
            )
            stop_thread.start()
            self.addCleanup(stop_thread.join, WAIT_SECONDS)
            self.assertTrue(recorder_stopped.wait(WAIT_SECONDS))
            transcription_release.set()
            self.assertTrue(pipeline._stop_event.wait(WAIT_SECONDS))
            provider_release.set()
            finalizer_release.set()
            self.assertTrue(main_stopped.wait(WAIT_SECONDS))
            stop_thread.join()

        self.assertTrue(main._stop_event.is_set())
        self.assertFalse(transcription_worker.is_alive())
        self.assertFalse(translation_worker.is_alive())
        self.assertFalse(output_worker.is_alive())
        self.assertIsNone(instance.mic_source_pipeline)
        self.assertNotIn(PipelineSource.MIC, instance._source_pipeline_generations)
        self.assertEqual(order[-3:], ["lease", "runtime", "telemetry"])

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

            def restart(
                reason="configuration_changed",
                *,
                expected_source=None,
                expected_generation=None,
            ):
                reasons.append(reason)
                restarted.set()
                return True

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

    def test_user_stop_winning_before_restart_lock_ignores_recovery_request(self):
        fake_model = _RecoveryModel()
        fake_model.active[PipelineSource.SPEAKER] = False
        before_lock = threading.Event()
        resume_restart = threading.Event()
        request_done = threading.Event()
        starts = []
        self.addCleanup(resume_restart.set)

        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            original_restart = controller._requestCoordinatedTranscriptionRestart

            def paused_restart(
                reason="configuration_changed",
                *,
                expected_source=None,
                expected_generation=None,
            ):
                before_lock.set()
                resume_restart.wait()
                try:
                    return original_restart(
                        reason,
                        expected_source=expected_source,
                        expected_generation=expected_generation,
                    )
                finally:
                    request_done.set()

            controller._requestCoordinatedTranscriptionRestart = paused_restart
            controller.startTranscriptionSendMessage = lambda: (
                starts.append(PipelineSource.MIC) or True
            )
            safe = threading.Event()
            safe.set()
            fake_model.callback(
                PipelineSource.MIC,
                3,
                "whisper_inference_failed",
                safe,
            )
            self.assertTrue(before_lock.wait(WAIT_SECONDS))

            # This uses the real Controller stop path and therefore the same
            # restart lock that the resumed coordinated recovery must acquire.
            controller.stopTranscriptionSendMessage()
            resume_restart.set()
            self.assertTrue(request_done.wait(WAIT_SECONDS))

            self.assertFalse(fake_model.recovery_metric_event.wait(0.1))
            self.assertEqual(starts, [])
            self.assertEqual(fake_model.recovered, [])
            self.assertEqual(fake_model.recovery_failed, [])
            self.assertTrue(controller._transcription_recovery_thread.is_alive())
            controller.shutdown()

    def test_recovery_restart_failure_or_exception_never_emits_recovered(self):
        for failing_source in (PipelineSource.MIC, PipelineSource.SPEAKER):
            for failure in (False, RuntimeError("controlled start failure")):
                with self.subTest(
                    source=failing_source.value,
                    failure=type(failure).__name__,
                ):
                    fake_model = _RecoveryModel()
                    calls = []
                    with patch.object(controller_module, "model", fake_model):
                        controller = Controller()
                        controller.stopTranscriptionSendMessage = (
                            lambda: calls.append("stop-mic")
                        )
                        controller.stopTranscriptionReceiveMessage = (
                            lambda: calls.append("stop-speaker")
                        )

                        def start(source):
                            calls.append(f"start-{source.value}")
                            if source is failing_source:
                                if isinstance(failure, Exception):
                                    raise failure
                                return False
                            return True

                        controller.startTranscriptionSendMessage = lambda: start(
                            PipelineSource.MIC
                        )
                        controller.startTranscriptionReceiveMessage = lambda: start(
                            PipelineSource.SPEAKER
                        )
                        safe = threading.Event()
                        safe.set()
                        fake_model.callback(
                            PipelineSource.MIC,
                            3,
                            "whisper_inference_failed",
                            safe,
                        )
                        self.assertTrue(
                            fake_model.recovery_metric_event.wait(WAIT_SECONDS)
                        )
                        controller.shutdown()

                    self.assertEqual(fake_model.recovered, [])
                    self.assertEqual(
                        fake_model.recovery_failed,
                        [(PipelineSource.MIC, "whisper_inference_failed")],
                    )
                    self.assertEqual(
                        calls,
                        [
                            "stop-mic",
                            "stop-speaker",
                            "start-mic",
                            "start-speaker",
                        ],
                    )

    def test_coordinated_restart_snapshots_active_sources_and_restarts_each_once(self):
        fake_model = _RecoveryModel()
        calls = []
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            self.addCleanup(controller.shutdown)
            controller.stopTranscriptionSendMessage = lambda: calls.append("stop-mic")
            controller.stopTranscriptionReceiveMessage = lambda: calls.append("stop-speaker")
            controller.startTranscriptionSendMessage = lambda: (
                calls.append("start-mic") or True
            )
            controller.startTranscriptionReceiveMessage = lambda: (
                calls.append("start-speaker") or True
            )

            result = controller._requestCoordinatedTranscriptionRestart(
                "weight_changed"
            )

        self.assertEqual(
            calls,
            ["stop-mic", "stop-speaker", "start-mic", "start-speaker"],
        )
        self.assertTrue(result)

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
