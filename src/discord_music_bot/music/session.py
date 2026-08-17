from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import discord

from .models import PlaybackState, Track
from .player import FfmpegPlayer
from .queue import QueueFullError, TrackQueue
from .sources.base import ExtractionError, MediaStream, SourceAdapter

logger = logging.getLogger(__name__)


class VoiceChannelError(RuntimeError):
    """Raised when a command cannot use the current voice session."""


class NothingPlayingError(RuntimeError):
    """Raised when a playback command has no active track."""


class NoPreviousTrackError(RuntimeError):
    """Raised when there is no previous track to replay."""


class MusicSession:
    _stream_open_attempts = 3

    def __init__(
        self,
        guild_id: int,
        source: SourceAdapter,
        player: FfmpegPlayer,
        *,
        max_queue_size: int,
        idle_disconnect_seconds: int,
    ) -> None:
        self.guild_id = guild_id
        self._source = source
        self._player = player
        self._queue = TrackQueue(max_queue_size)
        self._idle_disconnect_seconds = idle_disconnect_seconds
        self._lock = asyncio.Lock()
        self._activity = asyncio.Event()
        self._voice: discord.VoiceClient | None = None
        self._current: Track | None = None
        self._history: deque[Track] = deque(maxlen=50)
        self._state = PlaybackState.IDLE
        self._playback_task: asyncio.Task[None] | None = None
        self._notification_channel: Any | None = None
        self._closing = False
        self._previous_requested = False
        self._discard_track: Track | None = None

    def set_notification_channel(self, channel: Any) -> None:
        self._notification_channel = channel

    async def enqueue(
        self,
        tracks: Iterable[Track],
        voice_channel: discord.abc.Connectable,
        requester_id: int,
    ) -> int:
        requested_tracks = tuple(replace(track, requester_id=requester_id) for track in tracks)
        if not requested_tracks:
            raise ValueError("no playable tracks were provided")

        async with self._lock:
            if len(self._queue) + len(requested_tracks) > self._queue.max_size:
                available = self._queue.max_size - len(self._queue)
                raise QueueFullError(
                    f"the queue has {max(available, 0)} available slots, "
                    f"but {len(requested_tracks)} tracks were requested"
                )
            await self._ensure_voice(voice_channel)
            added = self._queue.add_many(requested_tracks)
            self._activity.set()
            if self._playback_task is None or self._playback_task.done():
                self._closing = False
                self._playback_task = asyncio.create_task(self._playback_loop())
            return added

    async def skip(self) -> None:
        async with self._lock:
            if self._voice is None or self._current is None or not self._voice.is_playing():
                raise NothingPlayingError("nothing is currently playing")
            self._voice.stop()

    async def previous(self) -> None:
        async with self._lock:
            if not self._history:
                raise NoPreviousTrackError("there is no previous track")

            previous_track = self._history.pop()
            if self._current is not None and self._voice is not None and self._voice.is_playing():
                try:
                    self._queue.prepend_many((previous_track, self._current))
                except QueueFullError:
                    self._history.append(previous_track)
                    raise
                self._previous_requested = True
                self._voice.stop()
                return

            try:
                self._queue.prepend_many((previous_track,))
            except QueueFullError:
                self._history.append(previous_track)
                raise
            self._activity.set()
            if self._playback_task is None or self._playback_task.done():
                self._closing = False
                self._playback_task = asyncio.create_task(self._playback_loop())

    async def clear_queue(self) -> None:
        async with self._lock:
            self._queue.clear()
            self._history.clear()
            self._previous_requested = False
            self._discard_track = self._current
            self._current = None
            self._state = PlaybackState.IDLE
            self._activity.set()
            if self._voice is not None and self._voice.is_playing():
                self._voice.stop()

    async def stop(self) -> None:
        async with self._lock:
            self._queue.clear()
            self._history.clear()
            self._previous_requested = False
            self._discard_track = self._current
            self._current = None
            self._state = PlaybackState.IDLE
            self._activity.set()
            if self._voice is not None and self._voice.is_playing():
                self._voice.stop()

    async def leave(self) -> None:
        async with self._lock:
            self._closing = True
            self._queue.clear()
            self._history.clear()
            self._previous_requested = False
            self._discard_track = self._current
            self._activity.set()
            voice = self._voice
            task = self._playback_task
            if voice is not None and voice.is_playing():
                voice.stop()

        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if voice is not None and voice.is_connected():
            with contextlib.suppress(discord.ClientException):
                await voice.disconnect(force=True)

        async with self._lock:
            self._voice = None
            self._current = None
            self._state = PlaybackState.IDLE
            self._playback_task = None

    async def shutdown(self) -> None:
        await self.leave()

    async def queue_snapshot(self) -> tuple[Track, ...]:
        async with self._lock:
            return self._queue.snapshot()

    async def current(self) -> tuple[Track | None, PlaybackState]:
        async with self._lock:
            return self._current, self._state

    async def is_in_channel(self, channel_id: int) -> bool:
        async with self._lock:
            return (
                self._voice is not None and getattr(self._voice.channel, "id", None) == channel_id
            )

    async def has_voice_connection(self) -> bool:
        async with self._lock:
            return self._voice is not None and self._voice.is_connected()

    async def _ensure_voice(self, channel: discord.abc.Connectable) -> None:
        if self._voice is not None and self._voice.is_connected():
            current_channel_id = getattr(self._voice.channel, "id", None)
            requested_channel_id = getattr(channel, "id", None)
            if requested_channel_id is None or current_channel_id != requested_channel_id:
                raise VoiceChannelError("the bot is already playing in another voice channel")
            return

        try:
            self._voice = await channel.connect()
        except (discord.ClientException, discord.HTTPException) as exc:
            raise VoiceChannelError("the bot could not join that voice channel") from exc

    async def _playback_loop(self) -> None:
        try:
            while True:
                async with self._lock:
                    if self._closing:
                        return
                    track = self._queue.pop_next()
                    voice = self._voice
                    if track is None:
                        self._current = None
                        self._state = PlaybackState.IDLE
                        self._activity.clear()
                    else:
                        logger.info(
                            "Dequeued track in guild %s: title=%r queue_remaining=%s",
                            self.guild_id,
                            track.title,
                            len(self._queue),
                        )

                if track is None:
                    try:
                        await asyncio.wait_for(
                            self._activity.wait(), timeout=self._idle_disconnect_seconds
                        )
                    except TimeoutError:
                        await self._disconnect_if_idle()
                        return
                    continue

                if voice is None or not voice.is_connected():
                    await self._notify("Playback stopped because the voice connection was lost.")
                    return

                audio: discord.AudioSource | None = None
                stream: MediaStream | None = None
                finished = asyncio.Event()
                callback_error: list[Exception | None] = [None]
                loop = asyncio.get_running_loop()
                try:
                    async with self._lock:
                        self._current = track
                        self._state = PlaybackState.PLAYING
                    logger.info(
                        "Resolving track for playback in guild %s: title=%r",
                        self.guild_id,
                        track.title,
                    )
                    resolved = await self._source.resolve(track)
                    for attempt in range(1, self._stream_open_attempts + 1):
                        try:
                            stream = await self._source.open_stream(resolved)
                            break
                        except ExtractionError:
                            if attempt >= self._stream_open_attempts:
                                raise
                            logger.warning(
                                "Stream opening failed in guild %s; retrying with a fresh "
                                "resolution: title=%r attempt=%s/%s",
                                self.guild_id,
                                track.title,
                                attempt + 1,
                                self._stream_open_attempts,
                            )
                            resolved = await self._source.resolve_retry(track, resolved)
                    if stream is None:
                        raise ExtractionError("stream opening did not return a media stream")
                    audio = self._player.create_source(resolved, stream)
                    stream = None
                    logger.info(
                        "Starting voice playback in guild %s: title=%r",
                        self.guild_id,
                        resolved.title,
                    )
                    track_title = track.title

                    def after(
                        error: Exception | None,
                        *,
                        current_title: str = track_title,
                        callback_error: list[Exception | None] = callback_error,
                        finished: asyncio.Event = finished,
                        loop: asyncio.AbstractEventLoop = loop,
                    ) -> None:
                        callback_error[0] = error
                        log = logger.warning if error is not None else logger.info
                        log(
                            "Voice playback callback in guild %s: title=%r error_type=%s",
                            self.guild_id,
                            current_title,
                            type(error).__name__ if error is not None else "none",
                        )
                        loop.call_soon_threadsafe(finished.set)

                    async with self._lock:
                        if self._voice is None or not self._voice.is_connected() or self._closing:
                            return
                        self._voice.play(audio, after=after)
                    await self._notify_now_playing(resolved)
                    await finished.wait()
                    logger.info(
                        "Voice playback finished in guild %s: title=%r callback_error=%s",
                        self.guild_id,
                        track.title,
                        type(callback_error[0]).__name__
                        if callback_error[0] is not None
                        else "none",
                    )

                    if callback_error[0] is not None:
                        logger.warning(
                            "Playback callback failed in guild %s: title=%r error_type=%s",
                            self.guild_id,
                            track.title,
                            type(callback_error[0]).__name__,
                        )
                        await self._notify(f"Playback failed for **{track.title}**; skipping it.")
                    else:
                        async with self._lock:
                            if self._discard_track is track:
                                self._discard_track = None
                            elif self._previous_requested:
                                self._previous_requested = False
                            else:
                                self._history.append(track)
                except ExtractionError as exc:
                    logger.info(
                        "Could not resolve track in guild %s: error_type=%s",
                        self.guild_id,
                        type(exc).__name__,
                    )
                    await self._notify(f"Could not play **{track.title}**; skipping it.")
                except Exception as exc:
                    logger.error(
                        "Unexpected playback failure in guild %s: error_type=%s",
                        self.guild_id,
                        type(exc).__name__,
                    )
                    await self._notify(f"Playback failed for **{track.title}**; skipping it.")
                finally:
                    if audio is not None:
                        audio.cleanup()
                    elif stream is not None:
                        stream.close()
                    async with self._lock:
                        if self._discard_track is track:
                            self._discard_track = None
                        self._current = None
                        self._state = PlaybackState.IDLE
                    logger.info(
                        "Playback state reset in guild %s: title=%r",
                        self.guild_id,
                        track.title,
                    )
        except asyncio.CancelledError:
            raise
        finally:
            async with self._lock:
                if self._playback_task is asyncio.current_task():
                    self._playback_task = None

    async def _disconnect_if_idle(self) -> None:
        async with self._lock:
            if self._queue.snapshot() or self._current is not None or self._closing:
                return
            voice = self._voice
            self._voice = None
            self._history.clear()
        if voice is not None and voice.is_connected():
            with contextlib.suppress(discord.ClientException):
                await voice.disconnect(force=True)

    async def _notify(self, message: str) -> None:
        channel = self._notification_channel
        if channel is None:
            return
        try:
            await channel.send(message)
        except (discord.HTTPException, discord.Forbidden):
            logger.debug("Could not send playback notification in guild %s", self.guild_id)

    async def _notify_now_playing(self, track: Track) -> None:
        channel = self._notification_channel
        if channel is None:
            logger.warning("No notification channel configured in guild %s", self.guild_id)
            return
        try:
            await channel.send(
                f"Now playing: [{track.title}]({track.source_url})",
                silent=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            logger.info("Announced now playing in guild %s: %s", self.guild_id, track.title)
        except Exception as exc:
            logger.warning(
                "Could not send now-playing notification in guild %s: error_type=%s",
                self.guild_id,
                type(exc).__name__,
            )
