"""Shared lifecycle and inference serialization for faster-whisper models."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Condition
from typing import Any, Callable, Optional

from .transcription_whisper import getWhisperModel, unloadWhisperModel


@dataclass(frozen=True)
class WhisperRuntimeKey:
    weight_type: str
    device: str
    device_index: int
    compute_type: str


@dataclass(frozen=True)
class WhisperInferenceResult:
    segments: tuple[Any, ...]
    info: Any


class WhisperRuntimeBusy(RuntimeError):
    """Raised when a different Whisper runtime is still in use."""


class WhisperRuntimeClosed(RuntimeError):
    """Raised when work is requested from a closed lease or manager."""


def _default_factory(root: str, key: WhisperRuntimeKey) -> object:
    return getWhisperModel(
        root,
        key.weight_type,
        device=key.device,
        device_index=key.device_index,
        compute_type=key.compute_type,
    )


class WhisperRuntimeLease:
    """A closeable reference to the manager's currently loaded model."""

    def __init__(
        self,
        manager: WhisperRuntimeManager,
        root: str,
        key: WhisperRuntimeKey,
    ) -> None:
        self._manager = manager
        self._root = root
        self._key = key
        self._closed = False

    @property
    def root(self) -> str:
        return self._root

    @property
    def key(self) -> WhisperRuntimeKey:
        return self._key

    @property
    def closed(self) -> bool:
        with self._manager._condition:
            return self._closed

    def transcribe(self, audio: Any, **options: Any) -> WhisperInferenceResult:
        return self._manager._transcribe(self, audio, options)

    def close(self) -> None:
        self._manager._close_lease(self)


class WhisperRuntimeManager:
    """Own one model and serialize every native inference performed on it."""

    def __init__(
        self,
        factory: Optional[Callable[[str, WhisperRuntimeKey], object]] = None,
        unload: Optional[Callable[[object], None]] = None,
        *,
        model_factory: Optional[Callable[[str, WhisperRuntimeKey], object]] = None,
        unload_model: Optional[Callable[[object], None]] = None,
    ) -> None:
        if factory is not None and model_factory is not None:
            raise TypeError("pass either factory or model_factory, not both")
        if unload is not None and unload_model is not None:
            raise TypeError("pass either unload or unload_model, not both")
        self._factory = model_factory or factory or _default_factory
        self._unload = unload_model or unload or unloadWhisperModel
        self._condition = Condition()
        self._model: Optional[object] = None
        self._root: Optional[str] = None
        self._key: Optional[WhisperRuntimeKey] = None
        self._leases: set[WhisperRuntimeLease] = set()
        self._active_inference = 0
        self._closing = False
        self._shutdown = False

    def acquire(self, root: str, key: WhisperRuntimeKey) -> WhisperRuntimeLease:
        with self._condition:
            if self._shutdown:
                raise WhisperRuntimeClosed("Whisper runtime manager is shut down")
            if self._closing:
                raise WhisperRuntimeBusy("Whisper runtime is closing")
            if self._model is not None and self._key != key:
                raise WhisperRuntimeBusy(
                    f"Whisper runtime {self._key!r} is still in use"
                )
            if self._model is None:
                self._model = self._factory(root, key)
                self._root = root
                self._key = key
            lease = WhisperRuntimeLease(self, root, key)
            self._leases.add(lease)
            return lease

    def _ensure_lease_can_transcribe(self, lease: WhisperRuntimeLease) -> None:
        if self._shutdown:
            raise WhisperRuntimeClosed("Whisper runtime manager is shut down")
        if lease._closed or lease not in self._leases:
            raise WhisperRuntimeClosed("Whisper runtime lease is closed")
        if self._model is None or self._key != lease._key:
            raise WhisperRuntimeClosed("Whisper runtime lease is stale")

    def _transcribe(
        self,
        lease: WhisperRuntimeLease,
        audio: Any,
        options: dict[str, Any],
    ) -> WhisperInferenceResult:
        with self._condition:
            self._ensure_lease_can_transcribe(lease)
            while self._active_inference:
                self._condition.wait()
                self._ensure_lease_can_transcribe(lease)
            model = self._model
            if model is None:
                raise WhisperRuntimeClosed("Whisper runtime lease is stale")
            self._active_inference = 1

        try:
            segments, info = model.transcribe(audio, **options)
            materialized_segments = tuple(segments)
            return WhisperInferenceResult(materialized_segments, info)
        finally:
            with self._condition:
                self._active_inference = 0
                self._condition.notify_all()

    def _unload_current_locked(self) -> None:
        model = self._model
        if model is None:
            return
        self._model = None
        self._root = None
        self._key = None
        try:
            self._unload(model)
        finally:
            del model

    def _close_lease(self, lease: WhisperRuntimeLease) -> None:
        with self._condition:
            if lease._closed:
                return
            lease._closed = True
            self._leases.discard(lease)
            if self._leases:
                return

            self._closing = True
            self._condition.notify_all()
            try:
                while self._active_inference:
                    self._condition.wait()
                self._unload_current_locked()
            finally:
                self._closing = False
                self._condition.notify_all()

    def shutdown(self) -> None:
        with self._condition:
            self._shutdown = True
            for lease in self._leases:
                lease._closed = True
            self._leases.clear()
            self._closing = True
            self._condition.notify_all()
            try:
                while self._active_inference:
                    self._condition.wait()
                self._unload_current_locked()
            finally:
                self._closing = False
                self._condition.notify_all()


__all__ = [
    "WhisperInferenceResult",
    "WhisperRuntimeBusy",
    "WhisperRuntimeClosed",
    "WhisperRuntimeKey",
    "WhisperRuntimeLease",
    "WhisperRuntimeManager",
]
