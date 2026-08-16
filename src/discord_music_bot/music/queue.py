from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from .models import Track


class QueueFullError(ValueError):
    """Raised when adding tracks would exceed the queue limit."""


class TrackQueue:
    def __init__(self, max_size: int) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be greater than zero")
        self._max_size = max_size
        self._items: deque[Track] = deque()

    @property
    def max_size(self) -> int:
        return self._max_size

    def __len__(self) -> int:
        return len(self._items)

    def add_many(self, tracks: Iterable[Track]) -> int:
        pending = tuple(tracks)
        if len(self._items) + len(pending) > self._max_size:
            available = self._max_size - len(self._items)
            raise QueueFullError(
                f"queue limit is {self._max_size}; only {max(available, 0)} slots remain"
            )
        self._items.extend(pending)
        return len(pending)

    def prepend_many(self, tracks: Iterable[Track]) -> int:
        pending = tuple(tracks)
        if len(self._items) + len(pending) > self._max_size:
            available = self._max_size - len(self._items)
            raise QueueFullError(
                f"queue limit is {self._max_size}; only {max(available, 0)} slots remain"
            )
        self._items.extendleft(reversed(pending))
        return len(pending)

    def pop_next(self) -> Track | None:
        return self._items.popleft() if self._items else None

    def clear(self) -> None:
        self._items.clear()

    def snapshot(self) -> tuple[Track, ...]:
        return tuple(self._items)
