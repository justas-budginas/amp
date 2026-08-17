from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from ..models import SourceResult, Track


class SourceError(RuntimeError):
    """Base error for source loading and stream resolution."""


class InvalidSourceError(SourceError):
    """Raised when a URL is not supported by the source adapter."""


class ExtractionError(SourceError):
    """Raised when source metadata or a stream cannot be extracted."""


class PlaylistTooLargeError(SourceError):
    """Raised when a playlist exceeds the configured entry limit."""


class NoPlayableTracksError(SourceError):
    """Raised when a source contains no playable tracks."""


class MediaStream(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class SourceAdapter(ABC):
    @abstractmethod
    async def load(self, url: str) -> SourceResult:
        """Load one track or an ordered batch of tracks from a URL."""

    @abstractmethod
    async def resolve(self, track: Track) -> Track:
        """Resolve a fresh stream URL for a queued track."""

    @abstractmethod
    async def open_stream(self, track: Track) -> MediaStream:
        """Open the resolved media stream without blocking the event loop."""
