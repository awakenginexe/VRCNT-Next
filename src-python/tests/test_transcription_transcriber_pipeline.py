import inspect
import os
import sys
import time
import unittest
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.latest_queue import LatestQueue, QueueClosed
from models.pipeline.pipeline_types import AudioChunk, PipelineSource
from models.transcription import transcription_transcriber as transcriber_module
from models.transcription.transcription_transcriber import AudioTranscriber


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


def queue_with(*chunks):
    queue = LatestQueue(maxsize=max(1, len(chunks)))
    for chunk in chunks:
        queue.offer(chunk)
    return queue


def pcm(value, samples=160):
    return int(value).to_bytes(2, "little", signed=True) * samples


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
            self.assertTrue(
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

        self.assertTrue(
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


if __name__ == "__main__":
    unittest.main()
