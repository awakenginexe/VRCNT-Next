"""Shared lifecycle and inference serialization for faster-whisper models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from threading import Condition, get_ident
from typing import Any, Callable, Optional


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
    from .transcription_whisper import getWhisperModel

    return getWhisperModel(
        root,
        key.weight_type,
        device=key.device,
        device_index=key.device_index,
        compute_type=key.compute_type,
    )


def _default_unload(model: object) -> None:
    from .transcription_whisper import unloadWhisperModel

    unloadWhisperModel(model)


class _RuntimeState(Enum):
    EMPTY = auto()
    READY = auto()
    DRAINING = auto()
    UNLOADING = auto()
    UNLOAD_FAILED = auto()
    SHUTDOWN = auto()


@dataclass(frozen=True)
class _UnloadFailure:
    error_type: type[BaseException]
    message: str

    def new_exception(self) -> BaseException:
        try:
            return self.error_type(self.message)
        except BaseException:
            return RuntimeError(
                f"Whisper runtime unload failed: {self.error_type.__name__}: "
                f"{self.message}"
            )


@dataclass
class _UnloadAttempt:
    attempt_id: int
    generation: int
    model: Optional[object]
    owner_thread_id: Optional[int] = None
    completed: bool = False
    failure: Optional[_UnloadFailure] = None


class WhisperRuntimeLease:
    """A closeable reference to the manager's currently loaded model."""

    def __init__(
        self,
        manager: WhisperRuntimeManager,
        root: str,
        key: WhisperRuntimeKey,
        generation: int,
    ) -> None:
        self._manager = manager
        self._root = root
        self._key = key
        self._generation = generation
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
        self._unload = unload_model or unload or _default_unload
        self._condition = Condition()
        self._model: Optional[object] = None
        self._root: Optional[str] = None
        self._key: Optional[WhisperRuntimeKey] = None
        self._leases: set[WhisperRuntimeLease] = set()
        self._active_inference = 0
        self._state = _RuntimeState.EMPTY
        self._shutdown_requested = False
        self._generation = 0
        self._next_unload_attempt_id = 0
        self._unload_attempt: Optional[_UnloadAttempt] = None

    def acquire(self, root: str, key: WhisperRuntimeKey) -> WhisperRuntimeLease:
        with self._condition:
            if self._shutdown_requested:
                raise WhisperRuntimeClosed("Whisper runtime manager is shut down")
            if self._state not in (_RuntimeState.EMPTY, _RuntimeState.READY):
                raise WhisperRuntimeBusy("Whisper runtime is not available")
            if self._state is _RuntimeState.READY and self._key != key:
                raise WhisperRuntimeBusy(
                    f"Whisper runtime {self._key!r} is still in use"
                )
            if self._state is _RuntimeState.EMPTY:
                model = self._factory(root, key)
                self._model = model
                self._root = root
                self._key = key
                self._generation += 1
                self._state = _RuntimeState.READY
            lease = WhisperRuntimeLease(self, root, key, self._generation)
            self._leases.add(lease)
            return lease

    def _ensure_lease_can_transcribe(self, lease: WhisperRuntimeLease) -> None:
        if self._shutdown_requested:
            raise WhisperRuntimeClosed("Whisper runtime manager is shut down")
        if lease._closed or lease not in self._leases:
            raise WhisperRuntimeClosed("Whisper runtime lease is closed")
        if (
            self._state is not _RuntimeState.READY
            or self._model is None
            or self._key != lease._key
        ):
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

    def _begin_drain_locked(self) -> _UnloadAttempt:
        model = self._model
        if model is None:
            raise RuntimeError("Whisper runtime lost model ownership")
        self._next_unload_attempt_id += 1
        attempt = _UnloadAttempt(
            attempt_id=self._next_unload_attempt_id,
            generation=self._generation,
            model=model,
        )
        self._unload_attempt = attempt
        self._state = _RuntimeState.DRAINING
        self._condition.notify_all()
        return attempt

    def _run_unload_attempt(self, attempt: _UnloadAttempt) -> None:
        model = attempt.model
        if model is None:
            raise RuntimeError("Whisper runtime unload attempt lost its model")
        owner_error: Optional[BaseException] = None
        failure: Optional[_UnloadFailure] = None
        try:
            self._unload(model)
        except BaseException as unload_error:
            owner_error = unload_error
            try:
                error_message = str(unload_error)
            except BaseException:
                error_message = type(unload_error).__name__
            failure = _UnloadFailure(type(unload_error), error_message)

        with self._condition:
            attempt.failure = failure
            attempt.completed = True
            attempt.model = None
            attempt.owner_thread_id = None
            if failure is None:
                self._model = None
                self._root = None
                self._key = None
                self._state = (
                    _RuntimeState.SHUTDOWN
                    if self._shutdown_requested
                    else _RuntimeState.EMPTY
                )
                self._unload_attempt = None
            else:
                self._state = _RuntimeState.UNLOAD_FAILED
            self._condition.notify_all()

        if owner_error is not None:
            raise owner_error

    def _drain_and_unload(self, attempt: _UnloadAttempt) -> None:
        owns_attempt = False
        failure: Optional[_UnloadFailure] = None
        with self._condition:
            while not attempt.completed:
                if self._unload_attempt is not attempt:
                    raise RuntimeError("Whisper runtime replaced an active unload attempt")
                if (
                    self._state is _RuntimeState.DRAINING
                    and not self._active_inference
                ):
                    self._state = _RuntimeState.UNLOADING
                    attempt.owner_thread_id = get_ident()
                    self._condition.notify_all()
                    owns_attempt = True
                    break
                if attempt.owner_thread_id == get_ident():
                    raise RuntimeError(
                        "unload callback cannot join its own unload attempt"
                    )
                self._condition.wait()

            if not owns_attempt:
                failure = attempt.failure

        if owns_attempt:
            self._run_unload_attempt(attempt)
        elif failure is not None:
            raise failure.new_exception()

    def _close_lease(self, lease: WhisperRuntimeLease) -> None:
        attempt: Optional[_UnloadAttempt] = None
        with self._condition:
            if lease._closed:
                if (
                    lease._generation == self._generation
                    and self._state
                    in (_RuntimeState.DRAINING, _RuntimeState.UNLOADING)
                ):
                    attempt = self._unload_attempt
            else:
                lease._closed = True
                self._leases.discard(lease)
                self._condition.notify_all()
                if self._leases:
                    return
                if self._state is _RuntimeState.READY:
                    attempt = self._begin_drain_locked()
                elif self._state in (
                    _RuntimeState.DRAINING,
                    _RuntimeState.UNLOADING,
                ):
                    attempt = self._unload_attempt

            if attempt is None:
                return

        self._drain_and_unload(attempt)

    def shutdown(self) -> None:
        attempt: Optional[_UnloadAttempt] = None
        with self._condition:
            self._shutdown_requested = True
            for lease in self._leases:
                lease._closed = True
            self._leases.clear()
            self._condition.notify_all()
            if self._state is _RuntimeState.EMPTY:
                self._state = _RuntimeState.SHUTDOWN
                self._condition.notify_all()
                return
            if self._state is _RuntimeState.SHUTDOWN:
                return
            if self._state in (_RuntimeState.READY, _RuntimeState.UNLOAD_FAILED):
                attempt = self._begin_drain_locked()
            elif self._state in (
                _RuntimeState.DRAINING,
                _RuntimeState.UNLOADING,
            ):
                attempt = self._unload_attempt
            if attempt is None:
                raise RuntimeError("Whisper runtime has no shutdown unload attempt")

        self._drain_and_unload(attempt)


__all__ = [
    "WhisperInferenceResult",
    "WhisperRuntimeBusy",
    "WhisperRuntimeClosed",
    "WhisperRuntimeKey",
    "WhisperRuntimeLease",
    "WhisperRuntimeManager",
]
