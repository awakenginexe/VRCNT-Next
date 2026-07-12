import os
import sys
import unittest
from datetime import datetime, timezone
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.latest_queue import LatestQueue
from models.pipeline.pipeline_types import AudioChunk
from models.transcription.transcription_recorder import (
    BaseEnergyAndAudioRecorder,
    BaseRecorder,
    SelectedSpeakerEnergyAndAudioRecorder,
    SelectedSpeakerRecorder,
)


class FakeAudio:
    def __init__(self, raw_data):
        self.raw_data = raw_data

    def get_raw_data(self):
        return self.raw_data


class FakeRecognizer:
    def __init__(self):
        self.audio_callback = None
        self.energy_callback = None

    def listen_in_background(
        self, source, callback, phrase_time_limit=None
    ):
        self.audio_callback = callback
        return "stop", "pause", "resume"

    def listen_energy_and_audio_in_background(
        self,
        source,
        callback,
        phrase_time_limit,
        callback_energy,
        phrase_timeout,
        record_timeout,
    ):
        self.audio_callback = callback
        self.energy_callback = callback_energy
        return "stop", "pause", "resume"


def make_full_audio_queue():
    audio_queue = LatestQueue[AudioChunk](maxsize=4)
    for index in range(4):
        audio_queue.offer(
            AudioChunk(
                data=f"old-{index}".encode(),
                spoken_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
                captured_at_monotonic=float(index),
            )
        )
    return audio_queue


class TranscriptionRecorderConstructorTests(unittest.TestCase):
    def test_speaker_recorders_use_library_default_chunk_size(self):
        constructor_calls = []

        def microphone_stub(*args, **kwargs):
            constructor_calls.append((args, kwargs))
            return object()

        device = {
            "index": 7,
            "defaultSampleRate": 48000,
            "maxInputChannels": 2,
        }

        with patch(
            "models.transcription.transcription_recorder.Microphone",
            side_effect=microphone_stub,
        ):
            SelectedSpeakerRecorder(device, 300, True, 5)
            SelectedSpeakerEnergyAndAudioRecorder(device, 300, True, 10)

        self.assertEqual(len(constructor_calls), 2)
        for recorder_name, (args, kwargs) in zip(
            ("SelectedSpeakerRecorder", "SelectedSpeakerEnergyAndAudioRecorder"),
            constructor_calls,
        ):
            with self.subTest(recorder=recorder_name):
                self.assertEqual(args, ())
                self.assertIs(kwargs["speaker"], True)
                self.assertEqual(kwargs["device_index"], 7)
                self.assertEqual(kwargs["sample_rate"], 48000)
                self.assertEqual(kwargs["channels"], 2)
                self.assertNotIn("chunk_size", kwargs)


class TranscriptionRecorderCallbackTests(unittest.TestCase):
    def invoke_promptly(self, callback, *args):
        errors = []
        completed = Event()

        def invoke():
            try:
                callback(*args)
            except BaseException as exc:
                errors.append(exc)
            finally:
                completed.set()

        worker = Thread(target=invoke, daemon=True)
        worker.start()
        self.assertTrue(
            completed.wait(timeout=0.5),
            "recorder callback blocked while the audio queue was full",
        )
        if errors:
            raise errors[0]

    def test_base_recorder_replaces_oldest_chunk_without_blocking(self):
        audio_queue = make_full_audio_queue()
        dropped = []
        heartbeats = []
        recognizer = FakeRecognizer()
        recorder = BaseRecorder(object(), 300, True, 5)
        recorder.recorder = recognizer
        spoken_at = datetime(2026, 7, 13, 12, 30, tzinfo=timezone.utc)
        clock = SimpleNamespace(perf_counter=lambda: 321.5)

        with (
            patch(
                "models.transcription.transcription_recorder.datetime"
            ) as datetime_mock,
            patch(
                "models.transcription.transcription_recorder.time",
                new=clock,
                create=True,
            ),
        ):
            datetime_mock.now.return_value = spoken_at
            recorder.recordIntoQueue(
                audio_queue,
                on_drop=dropped.append,
                on_heartbeat=heartbeats.append,
            )
            self.invoke_promptly(
                recognizer.audio_callback, None, FakeAudio(b"new-audio")
            )

        self.assertEqual(audio_queue.qsize(), 4)
        self.assertEqual([chunk.data for chunk in dropped], [b"old-0"])
        retained = audio_queue.drain()
        self.assertEqual(
            [chunk.data for chunk in retained],
            [b"old-1", b"old-2", b"old-3", b"new-audio"],
        )
        self.assertEqual(heartbeats, [321.5])
        data, time_spoken = retained[-1]
        self.assertEqual(data, b"new-audio")
        self.assertEqual(time_spoken, spoken_at)
        self.assertEqual(retained[-1].captured_at_monotonic, 321.5)

    def test_energy_and_audio_recorder_heartbeats_for_audio_and_silence(self):
        audio_queue = make_full_audio_queue()
        dropped = []
        heartbeats = []
        recognizer = FakeRecognizer()
        recorder = BaseEnergyAndAudioRecorder(
            object(), 300, True, 10, 1, 5
        )
        recorder.recorder = recognizer
        spoken_at = datetime(2026, 7, 13, 12, 30, tzinfo=timezone.utc)
        perf_counter_values = iter((400.25, 401.5))
        clock = SimpleNamespace(
            perf_counter=lambda: next(perf_counter_values)
        )

        with (
            patch(
                "models.transcription.transcription_recorder.datetime"
            ) as datetime_mock,
            patch(
                "models.transcription.transcription_recorder.time",
                new=clock,
                create=True,
            ),
        ):
            datetime_mock.now.return_value = spoken_at
            recorder.recordIntoQueue(
                audio_queue,
                None,
                on_drop=dropped.append,
                on_heartbeat=heartbeats.append,
            )
            self.assertIsNotNone(recognizer.energy_callback)
            self.invoke_promptly(
                recognizer.audio_callback, None, FakeAudio(b"combined-audio")
            )
            self.invoke_promptly(recognizer.energy_callback, 0)

        self.assertEqual(audio_queue.qsize(), 4)
        self.assertEqual([chunk.data for chunk in dropped], [b"old-0"])
        self.assertEqual(
            [chunk.data for chunk in audio_queue.drain()],
            [b"old-1", b"old-2", b"old-3", b"combined-audio"],
        )
        self.assertEqual(heartbeats, [400.25, 401.5])

    def test_conventional_full_queue_replaces_oldest_without_waiting(self):
        audio_queue = Queue(maxsize=1)
        oldest = AudioChunk(
            b"oldest",
            datetime(2026, 7, 13, tzinfo=timezone.utc),
            1.0,
        )
        audio_queue.put_nowait(oldest)
        dropped = []
        recognizer = FakeRecognizer()
        recorder = BaseRecorder(object(), 300, True, 5)
        recorder.recorder = recognizer
        clock = SimpleNamespace(perf_counter=lambda: 500.0)

        with patch(
            "models.transcription.transcription_recorder.time",
            new=clock,
            create=True,
        ):
            recorder.recordIntoQueue(audio_queue, on_drop=dropped.append)
            self.invoke_promptly(
                recognizer.audio_callback, None, FakeAudio(b"replacement")
            )

        self.assertEqual(dropped, [oldest])
        self.assertEqual(audio_queue.qsize(), 1)
        self.assertEqual(audio_queue.get_nowait().data, b"replacement")


if __name__ == "__main__":
    unittest.main()
