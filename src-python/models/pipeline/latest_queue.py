from collections import deque
from dataclasses import dataclass
from queue import Empty
from threading import Condition
from time import monotonic
from typing import Deque, Generic, Optional, TypeVar


T = TypeVar("T")


class QueueClosed(Exception):
    """Raised when a consumer reads after pipeline queue shutdown."""


@dataclass(frozen=True)
class OfferResult(Generic[T]):
    accepted: bool
    dropped: Optional[T]
    depth: int


class LatestQueue(Generic[T]):
    """A bounded queue where producers replace the oldest item when full."""

    def __init__(self, maxsize: int):
        if maxsize <= 0:
            raise ValueError("maxsize must be greater than zero")
        self._maxsize = maxsize
        self._items: Deque[T] = deque()
        self._condition = Condition()
        self._closed = False

    def offer(self, item: T) -> OfferResult[T]:
        with self._condition:
            if self._closed:
                return OfferResult(False, None, len(self._items))
            dropped = (
                self._items.popleft()
                if len(self._items) == self._maxsize
                else None
            )
            self._items.append(item)
            self._condition.notify()
            return OfferResult(True, dropped, len(self._items))

    def get(self, timeout: Optional[float] = None) -> T:
        with self._condition:
            if self._closed:
                raise QueueClosed()

            if timeout is None:
                while not self._items:
                    self._condition.wait()
                    if self._closed:
                        raise QueueClosed()
            else:
                deadline = monotonic() + timeout
                while not self._items:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise Empty()
                    self._condition.wait(remaining)
                    if self._closed:
                        raise QueueClosed()

            return self._items.popleft()

    def get_nowait(self) -> T:
        return self.get(timeout=0)

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def empty(self) -> bool:
        with self._condition:
            return not self._items

    def drain(self) -> list[T]:
        with self._condition:
            items = list(self._items)
            self._items.clear()
            return items

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
