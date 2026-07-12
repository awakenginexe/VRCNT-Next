import inspect
import importlib
import os
import sys
import time
import unittest
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.latest_queue import LatestQueue, QueueClosed
from models.pipeline.pipeline_types import AudioChunk, PipelineSource


def _import_pipeline_modules_with_real_dependencies():
    dependency_roots = ("numpy", "requests")
    needs_isolation = any(
        root in sys.modules
        and not callable(
            getattr(
                sys.modules[root],
                "frombuffer" if root == "numpy" else "get",
                None,
            )
        )
        for root in dependency_roots
    )
    saved_modules = {}
    if needs_isolation:
        saved_modules = {
            name: module
            for name, module in sys.modules.items()
            if name in dependency_roots
            or name.startswith(tuple(f"{root}." for root in dependency_roots))
        }
        for name in saved_modules:
            sys.modules.pop(name, None)
        importlib.import_module("numpy")
        importlib.import_module("requests")
    try:
        transcriber = importlib.import_module(
            "models.transcription.transcription_transcriber"
        )
        runtime = importlib.import_module(
            "models.transcription.whisper_runtime"
        )
        application_model = importlib.import_module("model")
        return transcriber, runtime, application_model
    finally:
        if needs_isolation:
            for name in list(sys.modules):
                if name in dependency_roots or name.startswith(
                    tuple(f"{root}." for root in dependency_roots)
                ):
                    sys.modules.pop(name, None)
            sys.modules.update(saved_modules)


transcriber_module, runtime_module, model_module = (
    _import_pipeline_modules_with_real_dependencies()
)
AudioTranscriber = transcriber_module.AudioTranscriber
WhisperRuntimeManager = runtime_module.WhisperRuntimeManager


class FakeSource:
    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2
    channels = 1


class NamedAudioChunk:
    def __init__(self, data, spoken_at, captured_at_monotonic):
        self.data = data
        self.spoken_at = spoken_at
        self.captured_at_monotonic = captured_at_monotonic

    def __iter__(self):
        raise AssertionError("transcriber must read AudioChunk named fields")


class ClosedOnGetQueue:
    def empty(self):
        return False

    def get(self):
        raise QueueClosed()

    def qsize(self):
        return 0


class FakeInferenceResult:
    def __init__(self, segments, info):
        self.segments = tuple(segments)
        self.info = info

    def __iter__(self):
        yield self.segments
        yield self.info


class FakeLease:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []
        self.closed = False

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))
        if self.error is not None:
            raise self.error
        return FakeInferenceResult(
            (
                SimpleNamespace(
                    text=" hello",
                    avg_logprob=-0.2,
                    no_speech_prob=0.1,
                ),
                SimpleNamespace(
                    text=" ignored",
                    avg_logprob=-2.0,
                    no_speech_prob=0.1,
                ),
                SimpleNamespace(
                    text=" world",
                    avg_logprob=-0.1,
                    no_speech_prob=0.2,
                ),
            ),
            SimpleNamespace(language="en", language_probability=0.9),
        )

    def close(self):
        self.closed = True


class FakeMicRecorder:
    events = None

    def __init__(self, *args, **kwargs):
        self.source = FakeSource()

    def recordIntoQueue(self, audio_queue, energy_queue):
        self.events.append(("record", "mic"))

    def resume(self):
        self.events.append(("resume_recorder", "mic"))

    def stop(self, *args):
        self.events.append(("stop_recorder", "mic"))


class FakeSpeakerRecorder(FakeMicRecorder):
    def recordIntoQueue(self, audio_queue, energy_queue):
        self.events.append(("record", "speaker"))

    def resume(self):
        self.events.append(("resume_recorder", "speaker"))

    def stop(self, *args):
        self.events.append(("stop_recorder", "speaker"))


class FakeThreadFnc:
    events = None

    def __init__(self, fnc, end_fnc=None, *args, **kwargs):
        self.label = "mic" if "Mic" in fnc.__name__ else "speaker"
        self.daemon = True

    def start(self):
        self.events.append(("thread_start", self.label))

    def stop(self):
        self.events.append(("thread_stop", self.label))

    def join(self, timeout=None):
        self.events.append(("thread_join", self.label, timeout))

    def is_alive(self):
        return False


class FailingStartThreadFnc(FakeThreadFnc):
    def start(self):
        super().start()
        raise RuntimeError(f"{self.label} thread start failed")


class FailingConstructThreadFnc(FakeThreadFnc):
    def __init__(self, fnc, end_fnc=None, *args, **kwargs):
        label = "mic" if "Mic" in fnc.__name__ else "speaker"
        raise RuntimeError(f"{label} thread construction failed")


class FakeConstructedTranscriber:
    events = None
    contexts = None

    def __init__(self, *args, **kwargs):
        context = kwargs["pipeline_context"]
        self.contexts.append(context)
        self.events.append(("construct", context.source))


class RecordingRuntimeManager(WhisperRuntimeManager):
    def __init__(self, events, factory, unload):
        self.events = events
        super().__init__(factory=factory, unload=unload)

    def acquire(self, root, key):
        self.events.append(("acquire", key))
        return super().acquire(root, key)


def make_pipeline_context(
    lease,
    *,
    events=None,
    generation=7,
    is_current=None,
    request_recovery=None,
):
    events = [] if events is None else events
    is_current = is_current or (lambda candidate: candidate == generation)
    request_recovery = request_recovery or (lambda *args: None)
    context_type = getattr(
        transcriber_module,
        "TranscriberPipelineContext",
        SimpleNamespace,
    )
    return context_type(
        source=PipelineSource.MIC,
        whisper_runtime_lease=lease,
        whisper_decoding_profile="balanced",
        generation=generation,
        is_generation_current=is_current,
        emit_metric=events.append,
        request_recovery=request_recovery,
    )


def make_transcriber(lease, context):
    kwargs = dict(
        speaker=False,
        source=FakeSource(),
        phrase_timeout=3,
        max_phrases=10,
        transcription_engine="Whisper",
        root="unused-root",
        whisper_weight_type="tiny",
        device="cpu",
        device_index=0,
        compute_type="int8",
    )
    if "pipeline_context" in inspect.signature(AudioTranscriber).parameters:
        kwargs["pipeline_context"] = context
        return AudioTranscriber(**kwargs)

    # RED compatibility: let pre-pipeline code reach its fixed inference path so
    # the focused suite reports direct ownership and decoding-profile failures.
    with patch.object(
        transcriber_module,
        "_getWhisperHelpers",
        return_value=(lambda *args, **options: lease, lambda *args: True),
    ):
        instance = AudioTranscriber(**kwargs)
    instance.pipeline_context = context
    return instance


def make_non_whisper_transcriber(engine, events, recovery_requests):
    context = make_pipeline_context(
        None,
        events=events,
        request_recovery=lambda *args: recovery_requests.append(args),
    )
    instance = AudioTranscriber(
        speaker=False,
        source=FakeSource(),
        phrase_timeout=3,
        max_phrases=10,
        transcription_engine="Google",
        pipeline_context=context,
    )
    instance.transcription_engine = engine
    return instance


def queue_with(*chunks):
    queue = LatestQueue(maxsize=max(1, len(chunks)))
    for chunk in chunks:
        queue.offer(chunk)
    return queue


def pcm(value, samples=160):
    return int(value).to_bytes(2, "little", signed=True) * samples


def make_model_config(engine="Whisper", compute_type="auto"):
    return SimpleNamespace(
        ENABLE_TRANSCRIPTION_SEND=True,
        ENABLE_TRANSCRIPTION_RECEIVE=True,
        SELECTED_MIC_HOST="host",
        SELECTED_MIC_DEVICE="mic-device",
        SELECTED_SPEAKER_DEVICE="speaker-device",
        MIC_RECORD_TIMEOUT=1,
        MIC_PHRASE_TIMEOUT=3,
        MIC_THRESHOLD=100,
        MIC_AUTOMATIC_THRESHOLD=False,
        MIC_MAX_PHRASES=10,
        SPEAKER_RECORD_TIMEOUT=1,
        SPEAKER_PHRASE_TIMEOUT=3,
        SPEAKER_THRESHOLD=100,
        SPEAKER_AUTOMATIC_THRESHOLD=False,
        SPEAKER_MAX_PHRASES=10,
        SELECTED_TRANSCRIPTION_ENGINE=engine,
        PATH_DATA="unused-root",
        WHISPER_WEIGHT_TYPE="tiny",
        VOSK_WEIGHT_TYPE=None,
        PARAKEET_WEIGHT_TYPE=None,
        SENSEVOICE_WEIGHT_TYPE=None,
        SELECTED_TRANSCRIPTION_COMPUTE_DEVICE={
            "device": "cuda",
            "device_index": 2,
        },
        SELECTED_TRANSCRIPTION_COMPUTE_TYPE=compute_type,
        WHISPER_DECODING_PROFILE="balanced",
    )


def make_bare_model(manager=None):
    instance = object.__new__(model_module.Model)
    instance.ensure_initialized = lambda: None
    instance.whisper_runtime_manager = manager
    instance.mic_print_transcript = None
    instance.mic_audio_recorder = None
    instance.mic_audio_queue = None
    instance.mic_transcriber = None
    instance.mic_transcript_stop_event = None
    instance.mic_whisper_runtime_lease = None
    instance.speaker_print_transcript = None
    instance.speaker_audio_recorder = None
    instance.speaker_audio_queue = None
    instance.speaker_transcriber = None
    instance.speaker_transcript_stop_event = None
    instance.speaker_whisper_runtime_lease = None
    instance.transcription_pipeline_metrics = []
    instance.transcription_recovery_requests = []
    instance._startTranscriptStallWatchdog = lambda *args: None
    instance.changeMicTranscriptStatus = lambda: None
    return instance


@contextmanager
def patched_model_startup(
    events,
    contexts,
    fake_config,
    *,
    thread_type=FakeThreadFnc,
):
    fake_devices = SimpleNamespace(
        getMicDevices=lambda: {"host": [{"name": "mic-device"}]},
        getSpeakerDevices=lambda: [{"name": "speaker-device"}],
    )
    FakeMicRecorder.events = events
    FakeSpeakerRecorder.events = events
    thread_type.events = events
    FakeConstructedTranscriber.events = events
    FakeConstructedTranscriber.contexts = contexts
    with (
        patch.object(model_module, "config", fake_config),
        patch.object(model_module, "device_manager", fake_devices),
        patch.object(model_module, "checkWhisperWeight", return_value=True),
        patch.object(
            model_module,
            "SelectedMicEnergyAndAudioRecorder",
            FakeMicRecorder,
        ),
        patch.object(
            model_module,
            "SelectedSpeakerEnergyAndAudioRecorder",
            FakeSpeakerRecorder,
        ),
        patch.object(model_module, "AudioTranscriber", FakeConstructedTranscriber),
        patch.object(model_module, "threadFnc", thread_type),
    ):
        yield


class TranscriberPipelineTests(unittest.TestCase):
    def test_pipeline_context_has_exact_frozen_contract(self):
        context_type = getattr(
            transcriber_module,
            "TranscriberPipelineContext",
            None,
        )

        self.assertIsNotNone(context_type)
        self.assertEqual(
            [field.name for field in fields(context_type)],
            [
                "source",
                "whisper_runtime_lease",
                "whisper_decoding_profile",
                "generation",
                "is_generation_current",
                "emit_metric",
                "request_recovery",
            ],
        )
        context = make_pipeline_context(FakeLease())
        with self.assertRaises(FrozenInstanceError):
            context.generation = 8

    def test_whisper_constructor_never_calls_direct_model_helpers(self):
        lease = FakeLease()
        context = make_pipeline_context(lease)

        with patch(
            "models.transcription.transcription_whisper.getWhisperModel",
            side_effect=AssertionError("direct Whisper ownership"),
        ) as helpers:
            AudioTranscriber(
                False,
                FakeSource(),
                3,
                10,
                "Whisper",
                "unused-root",
                "tiny",
                None,
                None,
                None,
                "cpu",
                0,
                "int8",
                context,
            )

        helpers.assert_not_called()

    def test_balanced_profile_uses_beam_two_and_filters_segments(self):
        lease = FakeLease()
        events = []
        context = make_pipeline_context(lease, events=events)
        transcriber = make_transcriber(lease, context)
        captured_at = time.perf_counter() - 0.25
        audio_queue = queue_with(
            AudioChunk(pcm(100), datetime.now(timezone.utc), captured_at)
        )

        self.assertTrue(
            transcriber.transcribeAudioQueue(
                audio_queue,
                ["English"],
                ["United States"],
            )
        )

        self.assertEqual(len(lease.calls), 1)
        self.assertEqual(lease.calls[0][1]["beam_size"], 2)
        self.assertEqual(transcriber.getTranscript()["text"], " hello world")

    def test_queue_age_metrics_and_phrase_start_use_audio_chunk_capture_time(self):
        lease = FakeLease()
        events = []
        context = make_pipeline_context(lease, events=events)
        transcriber = make_transcriber(lease, context)
        first_capture = time.perf_counter() - 0.30
        second_capture = first_capture + 0.10
        spoken_at = datetime.now(timezone.utc)
        audio_queue = queue_with(
            AudioChunk(pcm(100), spoken_at, first_capture),
            AudioChunk(pcm(200), spoken_at + timedelta(milliseconds=100), second_capture),
        )

        self.assertTrue(
            transcriber.transcribeAudioQueue(
                audio_queue,
                ["English"],
                ["United States"],
            )
        )

        self.assertEqual(
            [(event.stage, event.outcome) for event in events[:2]],
            [("queue", "success"), ("transcription", "running")],
        )
        queue_event = events[0]
        self.assertGreaterEqual(queue_event.queue_age_ms, 150)
        self.assertLess(queue_event.queue_age_ms, 1000)
        self.assertEqual(queue_event.queue_depth, 0)
        self.assertNotIn("text", queue_event.to_payload())
        self.assertEqual(
            transcriber.getTranscript()["started_at_monotonic"],
            first_capture,
        )

    def test_drain_reads_named_fields_and_retains_every_queued_chunk(self):
        lease = FakeLease()
        context = make_pipeline_context(lease)
        transcriber = make_transcriber(lease, context)
        now = datetime.now(timezone.utc)
        audio_queue = queue_with(
            NamedAudioChunk(pcm(100), now, time.perf_counter()),
            NamedAudioChunk(pcm(200), now, time.perf_counter()),
            NamedAudioChunk(pcm(300), now, time.perf_counter()),
        )

        self.assertTrue(
            transcriber.transcribeAudioQueue(
                audio_queue,
                ["English"],
                ["United States"],
            )
        )

        self.assertEqual(audio_queue.qsize(), 0)
        self.assertEqual(lease.calls[0][0].size, 480)

    def test_inactive_generation_drops_result_and_success_metric(self):
        lease = FakeLease()
        events = []
        context = make_pipeline_context(
            lease,
            events=events,
            is_current=lambda generation: False,
        )
        transcriber = make_transcriber(lease, context)
        update_calls = []
        transcriber.updateTranscript = update_calls.append
        audio_queue = queue_with(
            AudioChunk(
                pcm(100),
                datetime.now(timezone.utc),
                time.perf_counter(),
            )
        )

        self.assertTrue(
            transcriber.transcribeAudioQueue(
                audio_queue,
                ["English"],
                ["United States"],
            )
        )

        self.assertEqual(update_calls, [])
        self.assertNotIn(
            ("transcription", "success"),
            [(event.stage, event.outcome) for event in events],
        )
        self.assertIn(
            ("transcription", "stale"),
            [(event.stage, event.outcome) for event in events],
        )

    def test_outer_whisper_processing_failure_is_terminal_and_returns_false(self):
        lease = FakeLease()
        events = []
        recovery_requests = []
        cleanup_states = []

        def request_recovery(*args):
            self.assertFalse(args[-1].is_set())
            recovery_requests.append(args)

        context = make_pipeline_context(
            lease,
            events=events,
            request_recovery=request_recovery,
        )
        transcriber = make_transcriber(lease, context)
        original_clear = transcriber.clearLiveAudioSample

        def clear_audio():
            original_clear()
            cleanup_states.append(transcriber.audio_sources["last_sample"])

        transcriber.clearLiveAudioSample = clear_audio
        transcriber.audio_sources["process_data_func"] = lambda: (_ for _ in ()).throw(
            RuntimeError("fake processing failure")
        )

        result = transcriber.transcribeAudioQueue(
            queue_with(
                AudioChunk(
                    pcm(100),
                    datetime.now(timezone.utc),
                    time.perf_counter(),
                )
            ),
            ["English"],
            ["United States"],
        )

        self.assertFalse(result)
        self.assertEqual(cleanup_states, [b""])
        self.assertEqual(len(recovery_requests), 1)
        self.assertTrue(recovery_requests[0][-1].is_set())
        terminal = [
            (event.outcome, event.error_code)
            for event in events
            if event.stage == "transcription" and event.outcome != "running"
        ]
        self.assertEqual(terminal, [("error", "whisper_inference_failed")])

    def test_silent_whisper_audio_completes_running_metric_without_inference(self):
        lease = FakeLease()
        events = []
        context = make_pipeline_context(lease, events=events)
        transcriber = make_transcriber(lease, context)

        result = transcriber.transcribeAudioQueue(
            queue_with(
                AudioChunk(
                    pcm(0),
                    datetime.now(timezone.utc),
                    time.perf_counter(),
                )
            ),
            ["English"],
            ["United States"],
        )

        self.assertTrue(result)
        self.assertEqual(lease.calls, [])
        self.assertEqual(
            [
                event.outcome
                for event in events
                if event.stage == "transcription"
            ],
            ["running", "success"],
        )

    def test_missing_languages_ends_running_metric_with_input_error(self):
        lease = FakeLease()
        events = []
        context = make_pipeline_context(lease, events=events)
        transcriber = make_transcriber(lease, context)

        result = transcriber.transcribeAudioQueue(
            queue_with(
                AudioChunk(
                    pcm(100),
                    datetime.now(timezone.utc),
                    time.perf_counter(),
                )
            ),
            [],
            [],
        )

        self.assertFalse(result)
        terminal = [
            (event.outcome, event.error_code)
            for event in events
            if event.stage == "transcription" and event.outcome != "running"
        ]
        self.assertEqual(
            terminal,
            [("error", "transcription_languages_unavailable")],
        )

    def test_each_non_whisper_engine_exception_is_terminal_error(self):
        cases = (
            (
                "Google",
                "google_recognition_failed",
                lambda transcriber: setattr(
                    transcriber,
                    "audio_recognizer",
                    SimpleNamespace(
                        recognize_google=lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("google failed")
                        )
                    ),
                ),
            ),
            (
                "Vosk",
                "vosk_inference_failed",
                lambda transcriber: setattr(
                    transcriber,
                    "vosk_recognizer",
                    SimpleNamespace(
                        transcribe=lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("vosk failed")
                        )
                    ),
                ),
            ),
            (
                "Parakeet",
                "parakeet_inference_failed",
                lambda transcriber: setattr(
                    transcriber,
                    "parakeet_model",
                    SimpleNamespace(
                        transcribe=lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("parakeet failed")
                        )
                    ),
                ),
            ),
            (
                "SenseVoice",
                "sensevoice_inference_failed",
                lambda transcriber: setattr(
                    transcriber,
                    "sensevoice_model",
                    SimpleNamespace(
                        recognize=lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("sensevoice failed")
                        )
                    ),
                ),
            ),
        )

        for engine, error_code, configure in cases:
            with self.subTest(engine=engine):
                events = []
                recovery_requests = []
                transcriber = make_non_whisper_transcriber(
                    engine,
                    events,
                    recovery_requests,
                )
                configure(transcriber)
                with patch.object(transcriber_module, "errorLogging"):
                    result = transcriber.transcribeAudioQueue(
                        queue_with(
                            AudioChunk(
                                pcm(100),
                                datetime.now(timezone.utc),
                                time.perf_counter(),
                            )
                        ),
                        ["English"],
                        ["United States"],
                    )

                self.assertFalse(result)
                self.assertEqual(recovery_requests, [])
                self.assertEqual(transcriber.audio_sources["last_sample"], b"")
                terminal = [
                    (event.outcome, event.error_code)
                    for event in events
                    if event.stage == "transcription"
                    and event.outcome != "running"
                ]
                self.assertEqual(terminal, [("error", error_code)])
                self.assertNotIn(
                    ("transcription", "success"),
                    [(event.stage, event.outcome) for event in events],
                )

    def test_non_whisper_outer_processing_exceptions_never_request_recovery(self):
        for engine in ("Google", "Vosk", "Parakeet", "SenseVoice"):
            with self.subTest(engine=engine):
                events = []
                recovery_requests = []
                transcriber = make_non_whisper_transcriber(
                    engine,
                    events,
                    recovery_requests,
                )
                transcriber.audio_sources["process_data_func"] = lambda: (
                    _ for _ in ()
                ).throw(RuntimeError("audio processing failed"))
                with patch.object(transcriber_module, "errorLogging"):
                    result = transcriber.transcribeAudioQueue(
                        queue_with(
                            AudioChunk(
                                pcm(100),
                                datetime.now(timezone.utc),
                                time.perf_counter(),
                            )
                        ),
                        ["English"],
                        ["United States"],
                    )

                self.assertFalse(result)
                self.assertEqual(recovery_requests, [])
                self.assertEqual(transcriber.audio_sources["last_sample"], b"")
                terminal = [
                    (event.outcome, event.error_code)
                    for event in events
                    if event.stage == "transcription"
                    and event.outcome != "running"
                ]
                self.assertEqual(
                    terminal,
                    [("error", "audio_processing_failed")],
                )

    def test_google_unknown_value_remains_successful_empty_result(self):
        events = []
        recovery_requests = []
        transcriber = make_non_whisper_transcriber(
            "Google",
            events,
            recovery_requests,
        )
        transcriber.audio_recognizer = SimpleNamespace(
            recognize_google=lambda *args, **kwargs: (_ for _ in ()).throw(
                transcriber_module.UnknownValueError()
            )
        )

        result = transcriber.transcribeAudioQueue(
            queue_with(
                AudioChunk(
                    pcm(100),
                    datetime.now(timezone.utc),
                    time.perf_counter(),
                )
            ),
            ["English"],
            ["United States"],
        )

        self.assertTrue(result)
        self.assertEqual(recovery_requests, [])
        self.assertEqual(
            [
                event.outcome
                for event in events
                if event.stage == "transcription"
            ],
            ["running", "success"],
        )

    def test_whisper_failure_clears_audio_and_only_requests_recovery(self):
        lease = FakeLease(error=RuntimeError("fake inference failed"))
        recovery_requests = []
        context = make_pipeline_context(
            lease,
            request_recovery=lambda *args: recovery_requests.append(args),
        )
        transcriber = make_transcriber(lease, context)
        audio_queue = queue_with(
            AudioChunk(
                pcm(100),
                datetime.now(timezone.utc),
                time.perf_counter(),
            )
        )

        with patch(
            "models.transcription.transcription_whisper.getWhisperModel",
            side_effect=AssertionError("failure path reloaded Whisper"),
        ) as helpers:
            self.assertFalse(
                transcriber.transcribeAudioQueue(
                    audio_queue,
                    ["English"],
                    ["United States"],
                )
            )

        helpers.assert_not_called()
        self.assertEqual(transcriber.audio_sources["last_sample"], b"")
        self.assertEqual(len(recovery_requests), 1)
        source, generation, reason, safe_to_restart = recovery_requests[0]
        self.assertEqual(source, PipelineSource.MIC)
        self.assertEqual(generation, 7)
        self.assertEqual(reason, "whisper_inference_failed")
        self.assertIsInstance(safe_to_restart, Event)
        self.assertTrue(safe_to_restart.is_set())

    def test_recovery_callback_returns_before_cleanup_releases_restart(self):
        lease = FakeLease(error=RuntimeError("fake inference failed"))
        cleanup_complete = Event()
        restart_started = Event()
        observed_cleanup_state = []
        waiter_threads = []

        def request_recovery(source, generation, reason, safe_to_restart):
            self.assertFalse(safe_to_restart.is_set())

            def wait_for_cleanup():
                safe_to_restart.wait(1)
                observed_cleanup_state.append(cleanup_complete.is_set())
                restart_started.set()

            waiter = Thread(target=wait_for_cleanup, daemon=True)
            waiter_threads.append(waiter)
            waiter.start()
            self.assertFalse(restart_started.is_set())

        context = make_pipeline_context(
            lease,
            request_recovery=request_recovery,
        )
        transcriber = make_transcriber(lease, context)
        original_clear = transcriber.clearLiveAudioSample

        def record_cleanup():
            original_clear()
            cleanup_complete.set()

        transcriber.clearLiveAudioSample = record_cleanup
        audio_queue = queue_with(
            AudioChunk(
                pcm(100),
                datetime.now(timezone.utc),
                time.perf_counter(),
            )
        )

        self.assertFalse(
            transcriber.transcribeAudioQueue(
                audio_queue,
                ["English"],
                ["United States"],
            )
        )
        self.assertTrue(restart_started.wait(1))
        for waiter in waiter_threads:
            waiter.join(1)
        self.assertEqual(observed_cleanup_state, [True])

    def test_closed_queue_is_normal_stop_without_failure_logging(self):
        lease = FakeLease()
        context = make_pipeline_context(lease)
        transcriber = make_transcriber(lease, context)
        audio_queue = ClosedOnGetQueue()

        with patch.object(transcriber_module, "errorLogging") as log_error:
            self.assertFalse(
                transcriber.transcribeAudioQueue(
                    audio_queue,
                    ["English"],
                    ["United States"],
                )
            )

        log_error.assert_not_called()


class ModelWhisperLeaseIntegrationTests(unittest.TestCase):
    def test_runtime_failed_unload_can_retry_without_shutdown(self):
        attempts = []

        def fail_once(model):
            attempts.append(model)
            if len(attempts) == 1:
                raise RuntimeError("first unload failed")

        manager = WhisperRuntimeManager(
            factory=lambda root, key: object(),
            unload=fail_once,
        )
        key = runtime_module.WhisperRuntimeKey(
            "tiny",
            "cpu",
            0,
            "int8",
        )
        lease = manager.acquire("unused-root", key)

        with self.assertRaisesRegex(RuntimeError, "first unload failed"):
            lease.close()
        manager.retry_failed_unload()

        self.assertEqual(len(attempts), 2)
        replacement = manager.acquire("unused-root", key)
        replacement.close()
        self.assertEqual(len(attempts), 3)

    def test_runtime_repeated_failed_retry_can_succeed_later(self):
        attempts = []

        def fail_twice(model):
            attempts.append(model)
            if len(attempts) <= 2:
                raise RuntimeError(f"unload failed {len(attempts)}")

        manager = WhisperRuntimeManager(
            factory=lambda root, key: object(),
            unload=fail_twice,
        )
        key = runtime_module.WhisperRuntimeKey(
            "tiny",
            "cpu",
            0,
            "int8",
        )
        lease = manager.acquire("unused-root", key)

        with self.assertRaisesRegex(RuntimeError, "unload failed 1"):
            lease.close()
        with self.assertRaisesRegex(RuntimeError, "unload failed 2"):
            manager.retry_failed_unload()
        with self.assertRaises(runtime_module.WhisperRuntimeBusy):
            manager.acquire("unused-root", key)
        manager.retry_failed_unload()

        self.assertEqual(len(attempts), 3)
        replacement = manager.acquire("unused-root", key)
        replacement.close()
        self.assertEqual(len(attempts), 4)

    def test_cuda_auto_and_int8_share_one_resolved_runtime_key(self):
        factory_keys = []
        unloaded = []
        manager = WhisperRuntimeManager(
            factory=lambda root, key: factory_keys.append(key) or object(),
            unload=unloaded.append,
        )
        instance = make_bare_model(manager)
        fake_config = make_model_config(compute_type="auto")

        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
        ):
            auto_lease = instance._acquireWhisperRuntimeLease()
            fake_config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE = "int8"
            int8_lease = instance._acquireWhisperRuntimeLease()

        self.assertEqual(len(factory_keys), 1)
        self.assertEqual(factory_keys[0].compute_type, "int8_float16")
        self.assertEqual(auto_lease.key, factory_keys[0])
        self.assertEqual(int8_lease.key, factory_keys[0])
        auto_lease.close()
        self.assertEqual(unloaded, [])
        int8_lease.close()
        self.assertEqual(len(unloaded), 1)

    def test_mic_and_speaker_share_manager_and_stop_join_before_source_close(self):
        events = []
        contexts = []
        factory_keys = []
        unloaded = []
        manager = RecordingRuntimeManager(
            events,
            factory=lambda root, key: factory_keys.append(key) or object(),
            unload=lambda model: unloaded.append(model),
        )
        instance = make_bare_model(manager)
        fake_config = make_model_config()
        fake_devices = SimpleNamespace(
            getMicDevices=lambda: {
                "host": [{"name": "mic-device"}],
            },
            getSpeakerDevices=lambda: [{"name": "speaker-device"}],
        )
        FakeMicRecorder.events = events
        FakeSpeakerRecorder.events = events
        FakeThreadFnc.events = events
        FakeConstructedTranscriber.events = events
        FakeConstructedTranscriber.contexts = contexts

        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "device_manager", fake_devices),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
            patch.object(
                model_module,
                "SelectedMicEnergyAndAudioRecorder",
                FakeMicRecorder,
            ),
            patch.object(
                model_module,
                "SelectedSpeakerEnergyAndAudioRecorder",
                FakeSpeakerRecorder,
            ),
            patch.object(model_module, "AudioTranscriber", FakeConstructedTranscriber),
            patch.object(model_module, "threadFnc", FakeThreadFnc),
        ):
            instance.startMicTranscript(lambda result: None)
            instance.startSpeakerTranscript(lambda result: None)

            mic_lease = instance.mic_whisper_runtime_lease
            speaker_lease = instance.speaker_whisper_runtime_lease
            original_mic_close = mic_lease.close
            original_speaker_close = speaker_lease.close
            mic_lease.close = lambda: events.append(("lease_close", "mic")) or original_mic_close()
            speaker_lease.close = lambda: events.append(("lease_close", "speaker")) or original_speaker_close()

            instance.stopMicTranscript()
            self.assertTrue(mic_lease.closed)
            self.assertFalse(speaker_lease.closed)
            self.assertEqual(unloaded, [])
            instance.stopSpeakerTranscript()

        self.assertIs(instance.whisper_runtime_manager, manager)
        self.assertEqual(len(factory_keys), 1)
        self.assertEqual(factory_keys[0].compute_type, "int8_float16")
        self.assertEqual(
            [event[0] for event in events if event[0] in ("acquire", "construct")],
            ["acquire", "construct", "acquire", "construct"],
        )
        self.assertEqual([context.source for context in contexts], [PipelineSource.MIC, PipelineSource.SPEAKER])
        self.assertIs(contexts[0].whisper_runtime_lease, mic_lease)
        self.assertIs(contexts[1].whisper_runtime_lease, speaker_lease)
        self.assertLess(
            events.index(("thread_join", "mic", model_module.TRANSCRIPT_THREAD_JOIN_TIMEOUT)),
            events.index(("lease_close", "mic")),
        )
        self.assertLess(
            events.index(("thread_join", "speaker", model_module.TRANSCRIPT_THREAD_JOIN_TIMEOUT)),
            events.index(("lease_close", "speaker")),
        )
        self.assertTrue(speaker_lease.closed)
        self.assertEqual(len(unloaded), 1)

    def test_final_stop_waits_for_active_inference_after_join_timeout(self):
        iterator_entered = Event()
        release_iterator = Event()
        unloaded = []

        class BlockingModel:
            def transcribe(self, audio, **options):
                def segments():
                    iterator_entered.set()
                    release_iterator.wait(1)
                    yield SimpleNamespace(text="done")

                return segments(), SimpleNamespace(language="en")

        manager = WhisperRuntimeManager(
            factory=lambda root, key: BlockingModel(),
            unload=unloaded.append,
        )
        instance = make_bare_model(manager)
        fake_config = make_model_config()
        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
        ):
            lease = instance._acquireWhisperRuntimeLease()
        instance.mic_whisper_runtime_lease = lease
        instance.mic_print_transcript = object()
        instance.mic_transcriber = object()
        instance.mic_audio_queue = object()
        instance.mic_transcript_stop_event = Event()
        join_timed_out = Event()
        instance._requestTranscriptThreadStop = lambda thread: join_timed_out.set() or False

        inference_done = Event()
        stop_done = Event()
        errors = []

        def infer():
            try:
                lease.transcribe("audio")
            except BaseException as error:
                errors.append(error)
            finally:
                inference_done.set()

        def stop():
            try:
                instance.stopMicTranscript()
            except BaseException as error:
                errors.append(error)
            finally:
                stop_done.set()

        inference_thread = Thread(target=infer, daemon=True)
        inference_thread.start()
        self.assertTrue(iterator_entered.wait(1))
        stop_thread = Thread(target=stop, daemon=True)
        stop_thread.start()
        self.assertTrue(join_timed_out.wait(1))
        self.assertFalse(stop_done.wait(0.05))
        self.assertEqual(unloaded, [])

        release_iterator.set()
        self.assertTrue(inference_done.wait(1))
        self.assertTrue(stop_done.wait(1))
        inference_thread.join(1)
        stop_thread.join(1)
        self.assertEqual(errors, [])
        self.assertTrue(lease.closed)
        self.assertEqual(len(unloaded), 1)

    def test_non_whisper_context_keeps_runtime_lease_none(self):
        manager = WhisperRuntimeManager(
            factory=lambda root, key: self.fail("non-Whisper loaded a model"),
            unload=lambda model: None,
        )
        instance = make_bare_model(manager)
        fake_config = make_model_config(engine="Google")

        with patch.object(model_module, "config", fake_config):
            lease = instance._acquireWhisperRuntimeLease()
            context = instance._makeTranscriberPipelineContext(
                PipelineSource.MIC,
                lease,
            )

        self.assertIsNone(lease)
        self.assertIsNone(context.whisper_runtime_lease)

    def test_mic_startup_failures_roll_back_every_owned_resource(self):
        cases = (
            ("thread_construct", FailingConstructThreadFnc),
            ("thread_start", FailingStartThreadFnc),
            ("watchdog", FakeThreadFnc),
            ("status", FakeThreadFnc),
        )
        for stage, thread_type in cases:
            with self.subTest(stage=stage):
                events = []
                contexts = []
                unloaded = []
                manager = WhisperRuntimeManager(
                    factory=lambda root, key: object(),
                    unload=unloaded.append,
                )
                instance = make_bare_model(manager)
                if stage == "watchdog":
                    instance._startTranscriptStallWatchdog = lambda *args: (_ for _ in ()).throw(
                        RuntimeError("watchdog failed")
                    )
                if stage == "status":
                    instance.changeMicTranscriptStatus = lambda: (_ for _ in ()).throw(
                        RuntimeError("status failed")
                    )

                with patched_model_startup(
                    events,
                    contexts,
                    make_model_config(),
                    thread_type=thread_type,
                ):
                    with self.assertRaises(RuntimeError):
                        instance.startMicTranscript(lambda result: None)

                self.assertIsNone(instance.mic_audio_recorder)
                self.assertIsNone(instance.mic_audio_queue)
                self.assertIsNone(instance.mic_transcriber)
                self.assertIsNone(instance.mic_print_transcript)
                self.assertIsNone(instance.mic_transcript_stop_event)
                self.assertIsNone(instance.mic_whisper_runtime_lease)
                self.assertIn(("stop_recorder", "mic"), events)
                if stage != "thread_construct":
                    self.assertIn(("thread_stop", "mic"), events)
                    self.assertIn(
                        (
                            "thread_join",
                            "mic",
                            model_module.TRANSCRIPT_THREAD_JOIN_TIMEOUT,
                        ),
                        events,
                    )
                self.assertEqual(len(unloaded), 1)

    def test_speaker_watchdog_failure_rolls_back_started_worker_and_lease(self):
        events = []
        contexts = []
        unloaded = []
        manager = WhisperRuntimeManager(
            factory=lambda root, key: object(),
            unload=unloaded.append,
        )
        instance = make_bare_model(manager)
        instance._startTranscriptStallWatchdog = lambda *args: (_ for _ in ()).throw(
            RuntimeError("speaker watchdog failed")
        )

        with patched_model_startup(
            events,
            contexts,
            make_model_config(),
        ):
            with self.assertRaisesRegex(RuntimeError, "speaker watchdog failed"):
                instance.startSpeakerTranscript(lambda result: None)

        self.assertIsNone(instance.speaker_audio_recorder)
        self.assertIsNone(instance.speaker_audio_queue)
        self.assertIsNone(instance.speaker_transcriber)
        self.assertIsNone(instance.speaker_print_transcript)
        self.assertIsNone(instance.speaker_transcript_stop_event)
        self.assertIsNone(instance.speaker_whisper_runtime_lease)
        self.assertIn(("stop_recorder", "speaker"), events)
        self.assertIn(("thread_stop", "speaker"), events)
        self.assertIn(
            (
                "thread_join",
                "speaker",
                model_module.TRANSCRIPT_THREAD_JOIN_TIMEOUT,
            ),
            events,
        )
        self.assertEqual(len(unloaded), 1)

    def test_model_stop_retries_fail_once_unload_and_clears_lease(self):
        attempts = []

        def fail_once(model):
            attempts.append(model)
            if len(attempts) == 1:
                raise RuntimeError("transient unload failure")

        manager = WhisperRuntimeManager(
            factory=lambda root, key: object(),
            unload=fail_once,
        )
        instance = make_bare_model(manager)
        fake_config = make_model_config()
        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
        ):
            lease = instance._acquireWhisperRuntimeLease()
        instance.mic_whisper_runtime_lease = lease

        with patch.object(model_module, "errorLogging"):
            instance.stopMicTranscript()

        self.assertEqual(len(attempts), 2)
        self.assertIsNone(instance.mic_whisper_runtime_lease)
        self.assertTrue(lease.closed)
        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
        ):
            replacement = instance._acquireWhisperRuntimeLease()
        replacement.close()
        self.assertEqual(len(attempts), 3)

    def test_model_stop_retains_lease_after_repeated_failure_then_retries(self):
        attempts = []

        def fail_twice(model):
            attempts.append(model)
            if len(attempts) <= 2:
                raise RuntimeError(f"persistent unload failure {len(attempts)}")

        manager = WhisperRuntimeManager(
            factory=lambda root, key: object(),
            unload=fail_twice,
        )
        instance = make_bare_model(manager)
        fake_config = make_model_config()
        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
        ):
            lease = instance._acquireWhisperRuntimeLease()
        instance.mic_whisper_runtime_lease = lease
        instance.mic_audio_recorder = object()
        instance.mic_audio_queue = object()
        instance.mic_transcriber = object()

        with (
            patch.object(model_module, "errorLogging"),
            self.assertRaisesRegex(RuntimeError, "persistent unload failure 2"),
        ):
            instance.stopMicTranscript()

        self.assertIs(instance.mic_whisper_runtime_lease, lease)
        self.assertIsNone(instance.mic_audio_recorder)
        self.assertIsNone(instance.mic_audio_queue)
        self.assertIsNone(instance.mic_transcriber)
        self.assertEqual(len(attempts), 2)
        with self.assertRaises(runtime_module.WhisperRuntimeBusy):
            manager.acquire("unused-root", lease.key)

        with patch.object(model_module, "errorLogging"):
            instance.stopMicTranscript()

        self.assertEqual(len(attempts), 3)
        self.assertIsNone(instance.mic_whisper_runtime_lease)
        with (
            patch.object(model_module, "config", fake_config),
            patch.object(model_module, "checkWhisperWeight", return_value=True),
        ):
            replacement = instance._acquireWhisperRuntimeLease()
        replacement.close()
        self.assertEqual(len(attempts), 4)


if __name__ == "__main__":
    unittest.main()
