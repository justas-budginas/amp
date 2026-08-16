from __future__ import annotations

from ..config import Settings
from .player import FfmpegPlayer
from .session import MusicSession
from .sources.youtube import YoutubeSource


class MusicManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.source = YoutubeSource(
            max_queue_size=settings.max_queue_size,
            timeout_seconds=settings.extraction_timeout_seconds,
        )
        self._player = FfmpegPlayer(settings.ffmpeg_path)
        self._sessions: dict[int, MusicSession] = {}

    def get_or_create(self, guild_id: int) -> MusicSession:
        session = self._sessions.get(guild_id)
        if session is None:
            session = MusicSession(
                guild_id,
                self.source,
                self._player,
                max_queue_size=self._settings.max_queue_size,
                idle_disconnect_seconds=self._settings.idle_disconnect_seconds,
            )
            self._sessions[guild_id] = session
        return session

    def get(self, guild_id: int) -> MusicSession | None:
        return self._sessions.get(guild_id)

    async def shutdown(self) -> None:
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            await session.shutdown()
