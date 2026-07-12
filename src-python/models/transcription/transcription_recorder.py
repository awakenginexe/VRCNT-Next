"""Recorders that wrap speech_recognition microphone interfaces.

These classes provide small adapters that push raw audio bytes into queues.
They intentionally keep a thin API so the rest of the system can mock them
in tests.
"""

from datetime import datetime
from queue import Empty, Full
import time
from typing import Any

from speech_recognition import Recognizer, Microphone

from models.pipeline.pipeline_types import AudioChunk


def _offer_audio(audio_queue: Any, chunk: AudioChunk, on_drop=None) -> None:
    if hasattr(audio_queue, "offer"):
        result = audio_queue.offer(chunk)
        if result.dropped is not None and on_drop is not None:
            on_drop(result.dropped)
        return
    try:
        audio_queue.put_nowait(chunk)
    except Full:
        pass
    else:
        return

    # Conventional queues have no atomic replace operation. Bound recovery so
    # a continuously contended queue cannot make the capture callback spin.
    displaced_chunks = []
    for _ in range(2):
        try:
            displaced = audio_queue.get_nowait()
        except Empty:
            pass
        else:
            displaced_chunks.append(displaced)

        try:
            audio_queue.put_nowait(chunk)
        except Full:
            continue
        else:
            break

    if on_drop is not None:
        for displaced in displaced_chunks:
            on_drop(displaced)


class BaseRecorder:
    def __init__(self, source: Any, energy_threshold: int, dynamic_energy_threshold: bool, record_timeout: int) -> None:
        self.recorder = Recognizer()
        self.recorder.energy_threshold = energy_threshold
        self.recorder.dynamic_energy_threshold = dynamic_energy_threshold
        self.record_timeout = record_timeout
        self.stop = None

        if source is None:
            raise ValueError("audio source can't be None")

        self.source = source

    def adjustForNoise(self) -> None:
        with self.source:
            self.recorder.adjust_for_ambient_noise(self.source)

    def recordIntoQueue(
        self,
        audio_queue: Any,
        energy_queue: Any = None,
        *,
        on_drop=None,
        on_heartbeat=None,
    ) -> None:
        def record_callback(_, audio):
            captured_at = time.perf_counter()
            chunk = AudioChunk(
                data=audio.get_raw_data(),
                spoken_at=datetime.now(),
                captured_at_monotonic=captured_at,
            )
            _offer_audio(audio_queue, chunk, on_drop)
            if on_heartbeat is not None:
                on_heartbeat(captured_at)

        self.stop, self.pause, self.resume = self.recorder.listen_in_background(self.source, record_callback, phrase_time_limit=self.record_timeout)


class SelectedMicRecorder(BaseRecorder):
    def __init__(self, device: dict, energy_threshold: int, dynamic_energy_threshold: bool, record_timeout: int) -> None:
        # Safely construct Microphone source. If device dict is missing expected keys
        # or index is out-of-range for the platform, fallback to default device (None)
        try:
            device_index = int(device.get('index', -1))
            sample_rate = int(device.get("defaultSampleRate", 16000))
            if device_index < 0:
                # invalid index -> fallback
                raise ValueError("invalid device index")
            source = Microphone(
                device_index=device_index,
                sample_rate=sample_rate,
            )
        except Exception:
            # Best-effort fallback: use system default microphone
            try:
                source = Microphone()
            except Exception:
                raise
        super().__init__(source=source, energy_threshold=energy_threshold, dynamic_energy_threshold=dynamic_energy_threshold, record_timeout=record_timeout)
        # self.adjustForNoise()


class SelectedSpeakerRecorder(BaseRecorder):
    def __init__(self, device: dict, energy_threshold: int, dynamic_energy_threshold: bool, record_timeout: int) -> None:
        try:
            device_index = int(device.get('index', -1))
            sample_rate = int(device.get("defaultSampleRate", 16000))
            channels = int(device.get("maxInputChannels", 1))
            if device_index < 0:
                raise ValueError("invalid device index")
            source = Microphone(speaker=True,
                device_index=device_index,
                sample_rate=sample_rate,
                channels=channels
            )
        except Exception:
            try:
                source = Microphone(speaker=True)
            except Exception:
                raise
        super().__init__(source=source, energy_threshold=energy_threshold, dynamic_energy_threshold=dynamic_energy_threshold, record_timeout=record_timeout)
        # self.adjustForNoise()

class BaseEnergyRecorder:
    def __init__(self, source: Any) -> None:
        self.recorder = Recognizer()
        self.recorder.energy_threshold = 0
        self.recorder.dynamic_energy_threshold = False
        self.record_timeout = 0
        self.stop = None

        if source is None:
            raise ValueError("audio source can't be None")

        self.source = source

    def adjustForNoise(self) -> None:
        with self.source:
            self.recorder.adjust_for_ambient_noise(self.source)

    def recordIntoQueue(self, energy_queue: Any) -> None:
        def recordCallback(_, energy):
            energy_queue.put(energy)

        self.stop, self.pause, self.resume = self.recorder.listen_energy_in_background(self.source, recordCallback)


class SelectedMicEnergyRecorder(BaseEnergyRecorder):
    def __init__(self, device: dict) -> None:
        try:
            device_index = int(device.get('index', -1))
            sample_rate = int(device.get("defaultSampleRate", 16000))
            if device_index < 0:
                raise ValueError("invalid device index")
            source = Microphone(
                device_index=device_index,
                sample_rate=sample_rate,
            )
        except Exception:
            try:
                source = Microphone()
            except Exception:
                raise
        super().__init__(source=source)
        # self.adjustForNoise()


class SelectedSpeakerEnergyRecorder(BaseEnergyRecorder):
    def __init__(self, device: dict) -> None:
        try:
            device_index = int(device.get('index', -1))
            sample_rate = int(device.get("defaultSampleRate", 16000))
            channels = int(device.get("maxInputChannels", 1))
            if device_index < 0:
                raise ValueError("invalid device index")
            source = Microphone(speaker=True,
                device_index=device_index,
                sample_rate=sample_rate,
                channels=channels
            )
        except Exception:
            try:
                source = Microphone(speaker=True)
            except Exception:
                raise
        super().__init__(source=source)
        # self.adjustForNoise()

class BaseEnergyAndAudioRecorder:
    def __init__(
        self,
        source: Any,
        energy_threshold: int,
        dynamic_energy_threshold: bool,
        phrase_time_limit: int,
        phrase_timeout: int,
        record_timeout: int,
    ) -> None:
        self.recorder = Recognizer()
        self.recorder.energy_threshold = energy_threshold
        self.recorder.dynamic_energy_threshold = dynamic_energy_threshold
        self.phrase_time_limit = phrase_time_limit
        self.phrase_timeout = phrase_timeout
        self.record_timeout = record_timeout
        self.stop = None

        if source is None:
            raise ValueError("audio source can't be None")

        self.source = source

    def adjustForNoise(self) -> None:
        with self.source:
            self.recorder.adjust_for_ambient_noise(self.source)

    def recordIntoQueue(
        self,
        audio_queue: Any,
        energy_queue: Any = None,
        *,
        on_drop=None,
        on_heartbeat=None,
    ) -> None:
        def audioRecordCallback(_, audio):
            captured_at = time.perf_counter()
            chunk = AudioChunk(
                data=audio.get_raw_data(),
                spoken_at=datetime.now(),
                captured_at_monotonic=captured_at,
            )
            _offer_audio(audio_queue, chunk, on_drop)
            if on_heartbeat is not None:
                on_heartbeat(captured_at)

        def energyRecordCallback(energy):
            captured_at = time.perf_counter()
            if energy_queue is not None:
                energy_queue.put(energy)
            if on_heartbeat is not None:
                on_heartbeat(captured_at)

        self.stop, self.pause, self.resume = self.recorder.listen_energy_and_audio_in_background(
            source=self.source,
            callback=audioRecordCallback,
            phrase_time_limit=self.phrase_time_limit,
            callback_energy=(
                energyRecordCallback
                if energy_queue is not None or on_heartbeat is not None
                else None
            ),
            phrase_timeout=self.phrase_timeout,
            record_timeout=self.record_timeout,
        )


class SelectedMicEnergyAndAudioRecorder(BaseEnergyAndAudioRecorder):
    def __init__(
        self,
        device: dict,
        energy_threshold: int,
        dynamic_energy_threshold: bool,
        phrase_time_limit: int,
        phrase_timeout: int = 1,
        record_timeout: int = 5,
    ) -> None:
        try:
            device_index = int(device.get('index', -1))
            sample_rate = int(device.get("defaultSampleRate", 16000))
            if device_index < 0:
                raise ValueError("invalid device index")
            source = Microphone(
                device_index=device_index,
                sample_rate=sample_rate,
            )
        except Exception:
            try:
                source = Microphone()
            except Exception:
                raise
        super().__init__(
            source=source,
            energy_threshold=energy_threshold,
            dynamic_energy_threshold=dynamic_energy_threshold,
            phrase_time_limit=phrase_time_limit,
            phrase_timeout=phrase_timeout,
            record_timeout=record_timeout,
        )
        # self.adjustForNoise()


class SelectedSpeakerEnergyAndAudioRecorder(BaseEnergyAndAudioRecorder):
    def __init__(
        self,
        device: dict,
        energy_threshold: int,
        dynamic_energy_threshold: bool,
        phrase_time_limit: int,
        phrase_timeout: int = 1,
        record_timeout: int = 5,
    ) -> None:

        try:
            device_index = int(device.get('index', -1))
            sample_rate = int(device.get("defaultSampleRate", 16000))
            channels = int(device.get("maxInputChannels", 1))
            if device_index < 0:
                raise ValueError("invalid device index")
            source = Microphone(speaker=True,
                device_index=device_index,
                sample_rate=sample_rate,
                channels=channels,
            )
        except Exception:
            try:
                source = Microphone(speaker=True)
            except Exception:
                raise
        super().__init__(
            source=source,
            energy_threshold=energy_threshold,
            dynamic_energy_threshold=dynamic_energy_threshold,
            phrase_time_limit=phrase_time_limit,
            phrase_timeout=phrase_timeout,
            record_timeout=record_timeout,
        )
        # self.adjustForNoise()
