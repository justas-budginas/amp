from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlaybackState(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class Track:
    title: str
    source_url: str
    stream_url: str | None = None
    http_headers: tuple[tuple[str, str], ...] = ()
    duration: int | None = None
    thumbnail: str | None = None
    uploader: str | None = None
    source: str = "youtube"
    playlist_title: str | None = None
    playlist_index: int | None = None
    requester_id: int | None = None

    @property
    def duration_text(self) -> str:
        if self.duration is None:
            return "unknown"
        minutes, seconds = divmod(max(self.duration, 0), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


@dataclass(frozen=True, slots=True)
class SourceResult:
    tracks: tuple[Track, ...]
    skipped_count: int = 0
    skipped_reasons: tuple[str, ...] = ()
    source_title: str | None = None
    is_playlist: bool = False
    playlist_truncated: bool = False
