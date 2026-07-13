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
    def _exercise_healthy_silence_source(self, source):
        generation = 11 if source is PipelineSource.MIC else 12
        instance = object.__new__(Model)
        instance._inited = True
        instance._ensureTranscriptionLifecycleState()
        instance._source_pipeline_generations = {source: generation}
        instance._source_pipeline_generation_counters[source] = generation
        instance.mic_source_pipeline = (
            object() if source is PipelineSource.MIC else None
        )
        instance.speaker_source_pipeline = (
            object() if source is PipelineSource.SPEAKER else None
        )
        instance.mic_print_transcript = None
        instance.mic_audio_recorder = None
        instance.mic_whisper_runtime_lease = None
        instance.mic_transcriber = None
        instance.mic_transcript_stop_event = None
        instance.mic_mute_status = None
        instance.speaker_print_transcript = None
        instance.speaker_audio_recorder = None
        instance.speaker_whisper_runtime_lease = None
        instance.speaker_transcriber = None
        instance.speaker_transcript_stop_event = None
        instance.restartRecorder = Mock(return_value=True)
        instance._acquireWhisperRuntimeLease = Mock(return_value=None)
        instance._makeTranscriberPipelineContext = Mock(return_value=None)
        instance._startTranscriptStallWatchdog = Mock()
        recorders = []
        heartbeat_values = iter((10.0, 60.0))

        class Recorder:
            source = SimpleNamespace()

            def __init__(self, **_kwargs):
                self.callbacks = {}
                recorders.append(self)

            def recordIntoQueue(self, _queue, _energy, **callbacks):
                self.callbacks = callbacks

            def resume(self):
                return None

            def stop(self, *_args):
                return None

        class Transcriber:
            def __init__(self, **_kwargs):
                return None

            def transcribeAudioQueue(self, *_args, **_kwargs):
                recorders[-1].callbacks["on_heartbeat"](
                    next(heartbeat_values)
                )
                return False

            def resetAudioSource(self, _source):
                return None

        class TwoIterationThread:
            def __init__(self, fnc, end_fnc=None, **_kwargs):
                self.fnc = fnc
                self.end_fnc = end_fnc

            def start(self):
                self.fnc()
                self.fnc()

            def stop(self):
                return None

            def join(self, timeout=None):
                del timeout
                return None

            def is_alive(self):
                return False

        config_values = {
            "_SELECTED_TAB_NO": "1",
            "_SELECTED_YOUR_LANGUAGES": {
                "1": {
                    "1": {
                        "enable": True,
                        "language": "English",
                        "country": "United States",
                    }
                }
            },
            "_SELECTED_TARGET_LANGUAGES": {
                "1": {
                    "1": {
                        "enable": True,
                        "language": "Japanese",
                        "country": "Japan",
                    }
                }
            },
            "_ENABLE_TRANSCRIPTION_SEND": True,
            "_ENABLE_TRANSCRIPTION_RECEIVE": True,
            "_SELECTED_MIC_HOST": "Host",
            "_SELECTED_MIC_DEVICE": "Mic",
            "_SELECTED_SPEAKER_DEVICE": "Speaker",
            "_MIC_RECORD_TIMEOUT": 3,
            "_MIC_PHRASE_TIMEOUT": 3,
            "_SPEAKER_RECORD_TIMEOUT": 3,
            "_SPEAKER_PHRASE_TIMEOUT": 3,
            "_VRC_MIC_MUTE_SYNC": False,
        }
        with (
            patch.multiple(model_module.config, **config_values),
            patch.object(model_module, "AudioTranscriber", Transcriber),
            patch.object(model_module, "threadFnc", TwoIterationThread),
            patch.object(
                model_module,
                "SelectedMicEnergyAndAudioRecorder",
                Recorder,
            ),
            patch.object(
                model_module,
                "SelectedSpeakerEnergyAndAudioRecorder",
                Recorder,
            ),
            patch.object(
                model_module.device_manager,
                "getMicDevices",
                return_value={"Host": [{"name": "Mic"}]},
            ),
            patch.object(
                model_module.device_manager,
                "getSpeakerDevices",
                return_value=[{"name": "Speaker"}],
            ),
            patch.object(
                model_module,
                "monotonic",
                side_effect=(0.0, 0.0, 10.0, 60.0),
            ),
        ):
            if source is PipelineSource.MIC:
                started = instance._startMicTranscript(
                    lambda _result: None,
                    generation,
                )
            else:
                started = instance._startSpeakerTranscript(
                    lambda _result: None,
                    generation,
                )

            self.assertTrue(started)
            self.assertEqual(
                instance._source_heartbeat_timestamps[source],
                60.0,
            )
            instance.restartRecorder.assert_not_called()

            if source is PipelineSource.MIC:
                instance.stopMicTranscript(stop_pipeline=False)
            else:
                instance.stopSpeakerTranscript(stop_pipeline=False)

        self.assertNotIn(source, instance._source_heartbeat_timestamps)
        self.assertNotIn(source, instance._source_transcription_sessions)

    def test_healthy_mic_heartbeat_during_empty_queue_never_refreshes_recorder(self):
        self._exercise_healthy_silence_source(PipelineSource.MIC)

    def test_healthy_speaker_heartbeat_during_empty_queue_never_refreshes_recorder(self):
        self._exercise_healthy_silence_source(PipelineSource.SPEAKER)

    def test_capture_watchdog_recovers_each_stale_heartbeat_once_and_ignores_old_sessions(self):
        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        class ScriptedStopEvent:
            def __init__(self, outcomes):
                self.outcomes = iter(outcomes)
                self.stopped = False

            def wait(self, _timeout):
                outcome = next(self.outcomes, True)
                self.stopped = self.stopped or outcome
                return outcome

            def is_set(self):
                return self.stopped

        for source in (PipelineSource.MIC, PipelineSource.SPEAKER):
            with self.subTest(source=source.value, state="active"):
                generation = 20
                stop_event = ScriptedStopEvent((False, False, True))
                instance = object.__new__(Model)
                instance._inited = True
                instance._ensureTranscriptionLifecycleState()
                instance._source_pipeline_generations = {source: generation}
                instance.mic_source_pipeline = (
                    object() if source is PipelineSource.MIC else None
                )
                instance.speaker_source_pipeline = (
                    object() if source is PipelineSource.SPEAKER else None
                )
                instance._source_heartbeat_timestamps[source] = 0.0
                instance._source_transcription_sessions[source] = {
                    "generation": generation,
                    "stop_event": stop_event,
                }
                instance.restartRecorder = Mock(return_value=True)

                with (
                    patch.object(model_module, "Thread", ImmediateThread),
                    patch.object(
                        model_module,
                        "monotonic",
                        side_effect=(100.0, 101.0),
                    ),
                ):
                    instance._startCaptureHeartbeatWatchdog(
                        source,
                        generation,
                        stop_event,
                        stall_seconds=90.0,
                    )

                instance.restartRecorder.assert_called_once_with(
                    source,
                    generation,
                )

            with self.subTest(source=source.value, state="stale_generation"):
                generation = 30
                stop_event = ScriptedStopEvent((False, True))
                instance = object.__new__(Model)
                instance._inited = True
                instance._ensureTranscriptionLifecycleState()
                instance._source_pipeline_generations = {source: generation + 1}
                instance.mic_source_pipeline = (
                    object() if source is PipelineSource.MIC else None
                )
                instance.speaker_source_pipeline = (
                    object() if source is PipelineSource.SPEAKER else None
                )
                instance._source_heartbeat_timestamps[source] = 0.0
                instance._source_transcription_sessions[source] = {
                    "generation": generation + 1,
                    "stop_event": stop_event,
                }
                instance.restartRecorder = Mock(return_value=True)

                with (
                    patch.object(model_module, "Thread", ImmediateThread),
                    patch.object(model_module, "monotonic", return_value=100.0),
                ):
                    instance._startCaptureHeartbeatWatchdog(
                        source,
                        generation,
                        stop_event,
                        stall_seconds=90.0,
                    )

                instance.restartRecorder.assert_not_called()

    def test_capture_watchdog_retries_an_unchanged_heartbeat_after_reopen_failure(self):
        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        class ScriptedStopEvent:
            def __init__(self):
                self.outcomes = iter((False, False, True))

            def wait(self, _timeout):
                return next(self.outcomes, True)

        source = PipelineSource.MIC
        generation = 41
        stop_event = ScriptedStopEvent()
        instance = object.__new__(Model)
        instance._inited = True
        instance._ensureTranscriptionLifecycleState()
        instance._source_pipeline_generations = {source: generation}
        instance.mic_source_pipeline = object()
        instance.speaker_source_pipeline = None
        instance._source_heartbeat_timestamps[source] = 0.0
        instance._source_transcription_sessions[source] = {
            "generation": generation,
            "stop_event": stop_event,
        }
        instance.restartRecorder = Mock(side_effect=(False, True))

        with (
            patch.object(model_module, "Thread", ImmediateThread),
            patch.object(model_module, "monotonic", side_effect=(100.0, 101.0)),
        ):
            instance._startCaptureHeartbeatWatchdog(
                source,
                generation,
                stop_event,
                stall_seconds=90.0,
            )

        self.assertEqual(instance.restartRecorder.call_count, 2)
        instance.restartRecorder.assert_called_with(source, generation)

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

    def test_recovery_burst_keeps_older_current_request_when_newer_requests_are_stale(self):
        fake_model = _RecoveryModel()
        fake_model.active[PipelineSource.SPEAKER] = False
        safe_wait_entered = threading.Event()
        release_safe_wait = threading.Event()
        safe_to_restart = threading.Event()
        stale_checked = threading.Event()

        class ControlledSafeEvent:
            def wait(self, _timeout):
                safe_wait_entered.set()
                release_safe_wait.wait()
                return safe_to_restart.is_set()

        original_is_current = fake_model.isSourcePipelineGenerationCurrent

        def is_current(source, generation):
            if source is PipelineSource.SPEAKER:
                stale_checked.set()
            return original_is_current(source, generation)

        fake_model.isSourcePipelineGenerationCurrent = is_current

        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            restarted = []
            controller._requestCoordinatedTranscriptionRestart = (
                lambda reason="configuration_changed", **_kwargs: (
                    restarted.append(reason) or True
                )
            )
            fake_model.callback(
                PipelineSource.MIC,
                3,
                "mic_inference_failed",
                ControlledSafeEvent(),
            )
            self.assertTrue(safe_wait_entered.wait(WAIT_SECONDS))

            # Five offers force one queue displacement. Every newer request is
            # stale, so the current MIC request already being coordinated must
            # remain the recovery candidate.
            for generation in (1, 2, 4, 5, 7):
                stale_safe = threading.Event()
                stale_safe.set()
                fake_model.callback(
                    PipelineSource.SPEAKER,
                    generation,
                    "speaker_inference_failed",
                    stale_safe,
                )
            self.assertEqual(controller._transcription_recovery_queue.qsize(), 4)

            release_safe_wait.set()
            self.assertTrue(stale_checked.wait(WAIT_SECONDS))
            safe_to_restart.set()
            self.assertTrue(fake_model.recovery_metric_event.wait(WAIT_SECONDS))

            self.assertEqual(restarted, ["mic_inference_failed"])
            self.assertEqual(
                fake_model.recovered,
                [(PipelineSource.MIC, "mic_inference_failed")],
            )
            self.assertEqual(fake_model.recovery_failed, [])
            self.assertTrue(controller._transcription_recovery_thread.is_alive())
            controller.shutdown()
            self.assertFalse(controller._transcription_recovery_thread.is_alive())

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

    def test_source_pipeline_replacement_is_atomic_with_concurrent_stop(self):
        instance = object.__new__(Model)
        instance._inited = True
        instance.translator = object()
        instance._source_session_lock = threading.RLock()
        instance._source_pipeline_generations = {PipelineSource.MIC: 1}
        instance._source_pipeline_generation_counters = {
            PipelineSource.MIC: 1,
            PipelineSource.SPEAKER: 0,
        }
        instance._source_transcription_sessions = {}
        instance._source_heartbeat_timestamps = {}
        instance.transcription_pipeline_metrics = []
        instance.speaker_source_pipeline = None

        old_stop_entered = threading.Event()
        release_old_stop = threading.Event()
        replacement_done = threading.Event()
        stop_done = threading.Event()
        live_lock = threading.Lock()
        live_count = 1
        max_live_count = 1

        class ControlledPipeline:
            def __init__(self, **_kwargs):
                self.is_old = False
                self.running = False

            def start(self, _generation):
                nonlocal live_count, max_live_count
                with live_lock:
                    self.running = True
                    live_count += 1
                    max_live_count = max(max_live_count, live_count)

            def stop(self, _generation, discard_pending=True):
                del discard_pending
                nonlocal live_count
                if self.is_old:
                    old_stop_entered.set()
                    release_old_stop.wait()
                with live_lock:
                    if self.running:
                        self.running = False
                        live_count -= 1

        old_pipeline = ControlledPipeline()
        old_pipeline.is_old = True
        old_pipeline.running = True
        instance.mic_source_pipeline = old_pipeline
        callbacks = {
            "emit_initial": lambda _trace: None,
            "emit_update": lambda _update: None,
            "emit_metric": lambda _event: None,
            "emit_final": lambda _task: None,
        }

        with patch.object(model_module, "SourcePipeline", ControlledPipeline):
            replacement_thread = threading.Thread(
                target=lambda: (
                    instance.ensureSourcePipeline(PipelineSource.MIC, callbacks, 2),
                    replacement_done.set(),
                )
            )
            replacement_thread.start()
            self.addCleanup(release_old_stop.set)
            self.addCleanup(replacement_thread.join, WAIT_SECONDS)
            self.assertTrue(old_stop_entered.wait(WAIT_SECONDS))

            # Identity detaches atomically before an old worker is joined.
            self.assertFalse(
                instance.isSourcePipelineGenerationCurrent(PipelineSource.MIC, 1)
            )
            self.assertIsNone(instance.getSourcePipeline(PipelineSource.MIC))

            stop_thread = threading.Thread(
                target=lambda: (
                    instance.stopSourcePipeline(PipelineSource.MIC),
                    stop_done.set(),
                )
            )
            stop_thread.start()
            self.addCleanup(stop_thread.join, WAIT_SECONDS)

            # A stop racing the replacement owns the next source transition;
            # it cannot observe the temporary detach and return too early.
            self.assertFalse(stop_done.wait(0.1))
            release_old_stop.set()
            self.assertTrue(replacement_done.wait(WAIT_SECONDS))
            self.assertTrue(stop_done.wait(WAIT_SECONDS))
            replacement_thread.join()
            stop_thread.join()

        self.assertIsNone(instance.getSourcePipeline(PipelineSource.MIC))
        self.assertFalse(
            instance.isSourcePipelineGenerationCurrent(PipelineSource.MIC, 2)
        )
        self.assertEqual(live_count, 0)
        self.assertEqual(max_live_count, 1)

    def test_runtime_setting_transactions_are_serialized_and_report_restart_failure(self):
        controller = Controller()
        controller.run_mapping = {
            "selected_transcription_compute_type": "/compute-type",
        }
        first_restart_entered = threading.Event()
        release_first_restart = threading.Event()
        second_setter_done = threading.Event()
        snapshots = []
        responses = {}
        restart_call_lock = threading.Lock()
        restart_call_count = 0
        original_device = dict(
            controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE
        )
        original_compute_type = (
            controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE
        )
        original_profile = controller_module.config.WHISPER_DECODING_PROFILE
        self.addCleanup(controller.shutdown)

        def restart():
            nonlocal restart_call_count
            with restart_call_lock:
                call_index = restart_call_count
                restart_call_count += 1
            before = (
                controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE[
                    "device"
                ],
                controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE[
                    "device_index"
                ],
                controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE,
            )
            if call_index == 0:
                first_restart_entered.set()
                release_first_restart.wait()
            after = (
                controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE[
                    "device"
                ],
                controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE[
                    "device_index"
                ],
                controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE,
            )
            with restart_call_lock:
                snapshots.append((call_index, before, after))
            return True

        try:
            with patch.object(
                controller,
                "_normalizeTranscriptionRuntimeSelection",
                return_value=False,
            ):
                controller._requestCoordinatedTranscriptionRestart = restart

                first = threading.Thread(
                    target=lambda: responses.setdefault(
                        "device",
                        controller.setSelectedTranscriptionComputeDevice(
                            {"device": "cpu", "device_index": 0}
                        ),
                    )
                )
                second = threading.Thread(
                    target=lambda: (
                        responses.setdefault(
                            "compute_type",
                            controller.setSelectedTranscriptionComputeType("int8"),
                        ),
                        second_setter_done.set(),
                    )
                )
                first.start()
                self.addCleanup(release_first_restart.set)
                self.addCleanup(first.join, WAIT_SECONDS)
                self.assertTrue(first_restart_entered.wait(WAIT_SECONDS))
                second.start()
                self.addCleanup(second.join, WAIT_SECONDS)
                self.assertFalse(second_setter_done.wait(0.1))
                release_first_restart.set()
                first.join()
                second.join()

                self.assertEqual(
                    sorted(snapshots),
                    [
                        (0, ("cpu", 0, "auto"), ("cpu", 0, "auto")),
                        (1, ("cpu", 0, "int8"), ("cpu", 0, "int8")),
                    ],
                )
                self.assertEqual(responses["device"]["status"], 200)
                self.assertEqual(
                    responses["device"]["result"]["device"],
                    "cpu",
                )
                self.assertEqual(
                    responses["device"]["result"]["device_index"],
                    0,
                )
                self.assertEqual(
                    responses["compute_type"],
                    {"status": 200, "result": "int8"},
                )

                controller._requestCoordinatedTranscriptionRestart = lambda: False
                failure = controller.setWhisperDecodingProfile("accurate")
                self.assertEqual(
                    controller_module.config.WHISPER_DECODING_PROFILE,
                    "accurate",
                )
                self.assertEqual(
                    failure,
                    {
                        "status": 500,
                        "result": "accurate",
                        "error_code": "transcription_restart_failed",
                    },
                )
        finally:
            release_first_restart.set()
            controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE = (
                original_device
            )
            controller_module.config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE = (
                original_compute_type
            )
            controller_module.config.WHISPER_DECODING_PROFILE = original_profile

    def test_shutdown_waits_for_inflight_start_then_rejects_resurrection(self):
        fake_model = _RecoveryModel()
        fake_model.active = {
            PipelineSource.MIC: False,
            PipelineSource.SPEAKER: False,
        }
        setup_entered = threading.Event()
        release_setup = threading.Event()
        start_done = threading.Event()
        shutdown_done = threading.Event()
        setup_calls = []
        shutdown_response = {}

        def shutdown_pipelines():
            fake_model.shutdown_calls += 1
            fake_model.active[PipelineSource.MIC] = False
            fake_model.active[PipelineSource.SPEAKER] = False

        fake_model.shutdownTranscriptionPipelines = shutdown_pipelines
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()

            def blocked_setup():
                setup_calls.append("start")
                setup_entered.set()
                release_setup.wait()
                fake_model.active[PipelineSource.MIC] = True
                return True

            controller._startTranscriptionSendMessageUnlocked = blocked_setup
            start_thread = threading.Thread(
                target=lambda: (
                    controller.startTranscriptionSendMessage(),
                    start_done.set(),
                )
            )
            start_thread.start()
            self.addCleanup(release_setup.set)
            self.addCleanup(start_thread.join, WAIT_SECONDS)
            self.assertTrue(setup_entered.wait(WAIT_SECONDS))

            shutdown_thread = threading.Thread(
                target=lambda: (
                    shutdown_response.setdefault("value", controller.shutdown()),
                    shutdown_done.set(),
                )
            )
            shutdown_thread.start()
            self.addCleanup(shutdown_thread.join, WAIT_SECONDS)

            # Shutdown first establishes terminal intent under the same lock;
            # it cannot finish while setup still owns that lock.
            self.assertFalse(shutdown_done.wait(0.1))
            release_setup.set()
            self.assertTrue(start_done.wait(WAIT_SECONDS))
            self.assertTrue(shutdown_done.wait(WAIT_SECONDS))
            start_thread.join()
            shutdown_thread.join()

            self.assertEqual(
                shutdown_response["value"],
                {"status": 200, "result": True},
            )
            self.assertFalse(fake_model.active[PipelineSource.MIC])
            self.assertEqual(fake_model.shutdown_calls, 1)
            self.assertFalse(controller.startTranscriptionSendMessage())
            self.assertFalse(controller.startTranscriptionReceiveMessage())
            self.assertIsNone(controller._requestCoordinatedTranscriptionRestart())
            self.assertEqual(setup_calls, ["start"])

            profile = controller_module.config.WHISPER_DECODING_PROFILE
            rejected = controller.setWhisperDecodingProfile("post-shutdown-change")
            self.assertEqual(rejected["status"], 503)
            self.assertEqual(rejected["error_code"], "transcription_shutdown")
            self.assertEqual(
                controller_module.config.WHISPER_DECODING_PROFILE,
                profile,
            )

    def test_energy_checks_restore_device_access_when_model_start_raises(self):
        cases = (
            ("startCheckMicEnergy", "startCheckMicEnergy"),
            ("startCheckSpeakerEnergy", "startCheckSpeakerEnergy"),
        )
        for controller_method, model_method in cases:
            with self.subTest(controller_method=controller_method):
                fake_model = _RecoveryModel()

                def raise_energy_error(_callback):
                    raise RuntimeError("controlled energy start failure")

                setattr(fake_model, model_method, raise_energy_error)
                with patch.object(controller_module, "model", fake_model):
                    controller = Controller()
                    try:
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "controlled energy start failure",
                        ):
                            getattr(controller, controller_method)()
                        self.assertTrue(controller.device_access_status)
                    finally:
                        controller.device_access_status = True
                        controller.shutdown()

    def test_energy_start_rechecks_terminal_state_after_device_wait(self):
        cases = (
            ("startCheckMicEnergy", "startCheckMicEnergy", "mic-energy"),
            (
                "startCheckSpeakerEnergy",
                "startCheckSpeakerEnergy",
                "speaker-energy",
            ),
        )
        for controller_method, model_method, label in cases:
            with self.subTest(controller_method=controller_method):
                fake_model = _RecoveryModel()
                wait_succeeded = threading.Event()
                release_wait_result = threading.Event()
                energy_done = threading.Event()
                model_starts_after_shutdown = []

                with patch.object(controller_module, "model", fake_model):
                    controller = Controller()
                    original_wait = controller._waitForDeviceAccessOrShutdown

                    def paused_successful_wait():
                        result = original_wait()
                        self.assertTrue(result)
                        wait_succeeded.set()
                        release_wait_result.wait()
                        return result

                    controller._waitForDeviceAccessOrShutdown = (
                        paused_successful_wait
                    )
                    setattr(
                        fake_model,
                        model_method,
                        lambda _callback: model_starts_after_shutdown.append(
                            (
                                label,
                                controller._transcription_shutdown_state,
                            )
                        ),
                    )
                    energy_thread = threading.Thread(
                        target=lambda: (
                            getattr(controller, controller_method)(),
                            energy_done.set(),
                        )
                    )
                    energy_thread.start()
                    try:
                        self.assertTrue(wait_succeeded.wait(WAIT_SECONDS))
                        self.assertEqual(
                            controller.shutdown(),
                            {"status": 200, "result": True},
                        )
                        self.assertEqual(
                            controller._transcription_shutdown_state,
                            "shutdown",
                        )
                        release_wait_result.set()
                        self.assertTrue(energy_done.wait(WAIT_SECONDS))
                        self.assertEqual(model_starts_after_shutdown, [])
                        self.assertTrue(controller.device_access_status)
                    finally:
                        release_wait_result.set()
                        energy_thread.join(WAIT_SECONDS)

    def test_shutdown_stops_energy_start_that_won_lifecycle_lock(self):
        cases = (
            ("startCheckMicEnergy", "mic", "mic-energy-stop"),
            (
                "startCheckSpeakerEnergy",
                "speaker",
                "speaker-energy-stop",
            ),
        )
        for controller_method, source_name, stop_label in cases:
            with self.subTest(controller_method=controller_method):
                instance = object.__new__(Model)
                instance._inited = True
                instance._transcription_recovery_callback = None
                instance.stopMicTranscript = lambda: None
                instance.stopSpeakerTranscript = lambda: None
                instance.whisper_runtime_manager = SimpleNamespace(
                    shutdown=lambda: None
                )
                instance.telemetry = SimpleNamespace(shutdown=lambda: None)
                energy_active = {"mic": False, "speaker": False}
                energy_start_entered = threading.Event()
                release_energy_start = threading.Event()
                energy_done = threading.Event()
                shutdown_done = threading.Event()
                shutdown_response = []
                stops = []

                def start_energy(_callback, source=source_name):
                    energy_active[source] = True
                    energy_start_entered.set()
                    release_energy_start.wait()

                def stop_mic_energy():
                    stops.append("mic-energy-stop")
                    energy_active["mic"] = False

                def stop_speaker_energy():
                    stops.append("speaker-energy-stop")
                    energy_active["speaker"] = False

                instance.startCheckMicEnergy = (
                    start_energy
                    if source_name == "mic"
                    else lambda _callback: None
                )
                instance.startCheckSpeakerEnergy = (
                    start_energy
                    if source_name == "speaker"
                    else lambda _callback: None
                )
                instance.stopCheckMicEnergy = stop_mic_energy
                instance.stopCheckSpeakerEnergy = stop_speaker_energy

                with patch.object(controller_module, "model", instance):
                    controller = Controller()
                    energy_thread = threading.Thread(
                        target=lambda: (
                            getattr(controller, controller_method)(),
                            energy_done.set(),
                        )
                    )
                    energy_thread.start()
                    self.assertTrue(
                        energy_start_entered.wait(WAIT_SECONDS)
                    )
                    shutdown_thread = threading.Thread(
                        target=lambda: (
                            shutdown_response.append(controller.shutdown()),
                            shutdown_done.set(),
                        )
                    )
                    shutdown_thread.start()
                    try:
                        self.assertTrue(
                            controller._transcription_shutdown_requested.wait(
                                WAIT_SECONDS
                            )
                        )
                        self.assertFalse(shutdown_done.wait(0.1))
                        release_energy_start.set()
                        self.assertTrue(energy_done.wait(WAIT_SECONDS))
                        self.assertTrue(shutdown_done.wait(WAIT_SECONDS))
                        self.assertEqual(
                            shutdown_response,
                            [{"status": 200, "result": True}],
                        )
                        self.assertFalse(energy_active[source_name])
                        self.assertIn(stop_label, stops)
                    finally:
                        release_energy_start.set()
                        energy_thread.join(WAIT_SECONDS)
                        shutdown_thread.join(WAIT_SECONDS)
                        energy_active[source_name] = False

    def test_model_energy_start_rolls_back_partial_owned_resources(self):
        cases = (
            (
                "mic",
                "startCheckMicEnergy",
                "mic_energy_recorder",
                "mic_energy_plot_progressbar",
                "SelectedMicEnergyRecorder",
            ),
            (
                "speaker",
                "startCheckSpeakerEnergy",
                "speaker_energy_recorder",
                "speaker_energy_plot_progressbar",
                "SelectedSpeakerEnergyRecorder",
            ),
        )
        for (
            source_name,
            start_method,
            recorder_attribute,
            thread_attribute,
            recorder_class_name,
        ) in cases:
            for failure_stage in ("record", "thread"):
                with self.subTest(
                    source=source_name,
                    failure_stage=failure_stage,
                ):
                    recorders = []
                    threads = []

                    class ControlledRecorder:
                        def __init__(self, _device):
                            self.active = False
                            self.calls = []
                            recorders.append(self)

                        def recordIntoQueue(self, _queue):
                            self.active = True
                            self.calls.append("record")
                            if failure_stage == "record":
                                raise RuntimeError(
                                    "controlled recorder start failure"
                                )

                        def resume(self):
                            self.calls.append("resume")

                        def stop(self):
                            self.calls.append("stop")
                            self.active = False

                    class ControlledThread:
                        def __init__(self, *_args, **_kwargs):
                            self.daemon = False
                            self.stopped = False
                            self.joined = False
                            threads.append(self)

                        def start(self):
                            raise RuntimeError(
                                "controlled progress thread start failure"
                            )

                        def stop(self):
                            self.stopped = True

                        def join(self):
                            self.joined = True

                    instance = object.__new__(Model)
                    instance._inited = True
                    instance.check_mic_energy_fnc = lambda _value: None
                    instance.check_speaker_energy_fnc = lambda _value: None
                    instance.mic_energy_recorder = None
                    instance.mic_energy_plot_progressbar = None
                    instance.speaker_energy_recorder = None
                    instance.speaker_energy_plot_progressbar = None
                    fake_config = SimpleNamespace(
                        SELECTED_MIC_HOST="host",
                        SELECTED_MIC_DEVICE="mic-device",
                        SELECTED_SPEAKER_DEVICE="speaker-device",
                    )
                    fake_device_manager = SimpleNamespace(
                        getMicDevices=lambda: {
                            "host": [{"name": "mic-device"}]
                        },
                        getSpeakerDevices=lambda: [
                            {"name": "speaker-device"}
                        ],
                    )

                    try:
                        with (
                            patch.object(model_module, "config", fake_config),
                            patch.object(
                                model_module,
                                "device_manager",
                                fake_device_manager,
                            ),
                            patch.object(
                                model_module,
                                recorder_class_name,
                                ControlledRecorder,
                            ),
                            patch.object(
                                model_module,
                                "threadFnc",
                                ControlledThread,
                            ),
                        ):
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "controlled .* start failure",
                            ):
                                getattr(instance, start_method)(
                                    lambda _value: None
                                )

                            self.assertIsNone(
                                getattr(instance, recorder_attribute)
                            )
                            self.assertIsNone(
                                getattr(instance, thread_attribute)
                            )
                            self.assertEqual(len(recorders), 1)
                            self.assertFalse(recorders[0].active)
                            self.assertIn("resume", recorders[0].calls)
                            self.assertIn("stop", recorders[0].calls)
                            if failure_stage == "thread":
                                self.assertEqual(len(threads), 1)
                                self.assertTrue(threads[0].stopped)
                                self.assertTrue(threads[0].joined)
                    finally:
                        for recorder in recorders:
                            recorder.active = False
                        setattr(instance, recorder_attribute, None)
                        setattr(instance, thread_attribute, None)

    def test_device_waiting_starts_exit_when_shutdown_is_requested(self):
        class ObservedController(Controller):
            def __init__(self):
                self.device_wait_observed = threading.Event()
                self._observed_device_access_status = True
                super().__init__()

            @property
            def device_access_status(self):
                value = self._observed_device_access_status
                if value is False:
                    self.device_wait_observed.set()
                return value

            @device_access_status.setter
            def device_access_status(self, value):
                self._observed_device_access_status = value

        cases = (
            (
                "startTranscriptionSendMessage",
                "startMicTranscript",
                PipelineSource.MIC,
            ),
            (
                "startTranscriptionReceiveMessage",
                "startSpeakerTranscript",
                PipelineSource.SPEAKER,
            ),
        )
        for controller_method, model_method, source in cases:
            with self.subTest(controller_method=controller_method):
                fake_model = _RecoveryModel()
                model_starts = []
                fake_model.nextSourcePipelineGeneration = lambda _source: 42
                fake_model.ensureSourcePipeline = (
                    lambda started_source, _callbacks, _generation: model_starts.append(
                        ("pipeline", started_source)
                    )
                )
                setattr(
                    fake_model,
                    model_method,
                    lambda _callback: model_starts.append(
                        ("session", source)
                    )
                    or True,
                )
                fake_model.detectVRAMError = lambda _error: (False, None)
                fake_model.stopSourcePipeline = lambda _source: None

                with patch.object(controller_module, "model", fake_model):
                    controller = ObservedController()
                    controller.device_access_status = False
                    start_done = threading.Event()
                    shutdown_done = threading.Event()
                    start_results = []
                    start_thread = threading.Thread(
                        target=lambda: (
                            start_results.append(
                                getattr(controller, controller_method)()
                            ),
                            start_done.set(),
                        )
                    )
                    start_thread.start()
                    self.assertTrue(
                        controller.device_wait_observed.wait(WAIT_SECONDS)
                    )
                    shutdown_thread = threading.Thread(
                        target=lambda: (
                            controller.shutdown(),
                            shutdown_done.set(),
                        )
                    )
                    shutdown_thread.start()
                    try:
                        self.assertTrue(
                            controller._transcription_shutdown_requested.wait(
                                WAIT_SECONDS
                            )
                        )
                        self.assertTrue(start_done.wait(0.25))
                        self.assertEqual(start_results, [False])
                        self.assertTrue(shutdown_done.wait(WAIT_SECONDS))
                        self.assertEqual(model_starts, [])
                    finally:
                        controller.device_access_status = True
                        controller._transcription_shutdown_requested.set()
                        start_thread.join(WAIT_SECONDS)
                        shutdown_thread.join(WAIT_SECONDS)

    def test_concurrent_and_repeated_shutdown_callers_share_one_terminal_result(self):
        fake_model = _RecoveryModel()
        model_shutdown_entered = threading.Event()
        release_model_shutdown = threading.Event()
        first_done = threading.Event()
        second_done = threading.Event()
        responses = []

        def shutdown_pipelines():
            fake_model.shutdown_calls += 1
            model_shutdown_entered.set()
            release_model_shutdown.wait()

        fake_model.shutdownTranscriptionPipelines = shutdown_pipelines
        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            first = threading.Thread(
                target=lambda: (
                    responses.append(controller.shutdown()),
                    first_done.set(),
                )
            )
            first.start()
            self.addCleanup(release_model_shutdown.set)
            self.addCleanup(first.join, WAIT_SECONDS)
            self.assertTrue(model_shutdown_entered.wait(WAIT_SECONDS))

            second = threading.Thread(
                target=lambda: (
                    responses.append(controller.shutdown()),
                    second_done.set(),
                )
            )
            second.start()
            self.addCleanup(second.join, WAIT_SECONDS)
            self.assertFalse(first_done.is_set())
            self.assertFalse(second_done.wait(0.1))

            release_model_shutdown.set()
            self.assertTrue(first_done.wait(WAIT_SECONDS))
            self.assertTrue(second_done.wait(WAIT_SECONDS))
            first.join()
            second.join()

            self.assertEqual(
                responses,
                [
                    {"status": 200, "result": True},
                    {"status": 200, "result": True},
                ],
            )
            self.assertEqual(fake_model.shutdown_calls, 1)
            self.assertEqual(fake_model.telemetry_calls, 1)
            self.assertEqual(
                controller.shutdown(),
                {"status": 200, "result": True},
            )
            self.assertEqual(fake_model.shutdown_calls, 1)
            self.assertEqual(fake_model.telemetry_calls, 1)

    def test_shutdown_releases_restart_lock_while_joining_blocked_coordinator(self):
        fake_model = _RecoveryModel()
        fake_model.active[PipelineSource.SPEAKER] = False
        coordinator_before_lock = threading.Event()
        release_coordinator = threading.Event()
        queue_closed = threading.Event()
        shutdown_done = threading.Event()

        with patch.object(controller_module, "model", fake_model):
            controller = Controller()
            original_restart = controller._requestCoordinatedTranscriptionRestart

            def paused_restart(reason="configuration_changed", **kwargs):
                coordinator_before_lock.set()
                release_coordinator.wait()
                return original_restart(reason, **kwargs)

            controller._requestCoordinatedTranscriptionRestart = paused_restart
            original_close = controller._transcription_recovery_queue.close

            def tracked_close():
                original_close()
                queue_closed.set()

            controller._transcription_recovery_queue.close = tracked_close
            safe = threading.Event()
            safe.set()
            fake_model.callback(
                PipelineSource.MIC,
                3,
                "mic_inference_failed",
                safe,
            )
            self.assertTrue(coordinator_before_lock.wait(WAIT_SECONDS))

            shutdown_thread = threading.Thread(
                target=lambda: (controller.shutdown(), shutdown_done.set())
            )
            shutdown_thread.start()
            self.addCleanup(release_coordinator.set)
            self.addCleanup(shutdown_thread.join, WAIT_SECONDS)
            self.assertTrue(queue_closed.wait(WAIT_SECONDS))
            release_coordinator.set()
            self.assertTrue(shutdown_done.wait(WAIT_SECONDS))
            shutdown_thread.join()

            self.assertFalse(controller._transcription_recovery_thread.is_alive())
            self.assertEqual(fake_model.recovered, [])
            self.assertEqual(fake_model.recovery_failed, [])

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
