from __future__ import annotations

import io
import logging
import shlex
import subprocess
import time
from typing import cast
from urllib.parse import parse_qs, urlparse

import discord

from .models import Track
from .sources.base import MediaStream

logger = logging.getLogger(__name__)


class PlaybackError(RuntimeError):
    """Raised when FFmpeg cannot create an audio source."""


class _LoggedAudioSource(discord.AudioSource):
    def __init__(
        self,
        source: discord.AudioSource,
        track: Track,
        stderr: io.BytesIO,
        stream: MediaStream,
    ) -> None:
        self._source = source
        self._stderr = stderr
        self._stream = stream
        self._title = track.title
        self._stream_host = urlparse(track.stream_url or "").hostname or "unknown"
        self._format_id = parse_qs(urlparse(track.stream_url or "").query).get(
            "itag", ["unknown"]
        )[0]
        self._started_at = time.monotonic()
        self._frames_read = 0
        self._cleaned_up = False
        self._end_logged = False
        self._current_error: Exception | None = None

    def read(self) -> bytes:
        data = self._source.read()
        if data:
            self._frames_read += 1
            if self._frames_read == 1:
                logger.info(
                    "FFmpeg produced first audio frame for track %r: host=%s format=%s "
                    "pid=%s",
                    self._title,
                    self._stream_host,
                    self._format_id,
                    self._process_id(),
                )
            return data

        self._settle_process()
        error = getattr(self._source, "_current_error", None)
        if isinstance(error, Exception):
            self._current_error = error
        if not self._end_logged:
            self._end_logged = True
            returncode = self._process_returncode()
            log = logger.info if returncode == 0 and self._current_error is None else logger.warning
            log(
                "FFmpeg stream ended for track %r: host=%s format=%s pid=%s "
                "returncode=%s frames=%s elapsed_ms=%s error=%s",
                self._title,
                self._stream_host,
                self._format_id,
                self._process_id(),
                returncode,
                self._frames_read,
                self._elapsed_ms(),
                self._error_kind(self._current_error, self._stderr.getvalue(), returncode),
            )
        return data

    def is_opus(self) -> bool:
        return self._source.is_opus()

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        process = getattr(self._source, "_process", None)
        pid = getattr(process, "pid", "unknown")
        returncode_before = self._process_returncode(process)
        try:
            self._source.cleanup()
        finally:
            self._stream.close()
        logger.info(
            "FFmpeg cleanup for track %r: pid=%s returncode=%s->%s "
            "frames=%s elapsed_ms=%s",
            self._title,
            pid,
            returncode_before,
            self._process_returncode(process),
            self._frames_read,
            self._elapsed_ms(),
        )

    def _process_id(self) -> int | str:
        process = getattr(self._source, "_process", None)
        pid = getattr(process, "pid", None)
        return pid if isinstance(pid, int) else "unknown"

    def _process_returncode(self, process: object | None = None) -> int | str:
        if process is None:
            process = getattr(self._source, "_process", None)
        poll = getattr(process, "poll", None)
        if not callable(poll):
            return "unknown"
        try:
            return poll()
        except Exception:
            return "unavailable"

    def _elapsed_ms(self) -> int:
        return round((time.monotonic() - self._started_at) * 1000)

    @staticmethod
    def _error_kind(
        error: Exception | None,
        stderr: bytes = b"",
        returncode: int | str | None = None,
    ) -> str:
        if returncode == 0 and error is None:
            return "none"
        message = f"{error or ''}\n{stderr.decode(errors='replace')}"
        for status in ("403", "404", "429", "500", "502", "503"):
            if status in message:
                return f"http_{status}"
        if error is None and not stderr:
            return "none"
        return type(error).__name__ if error is not None else "ffmpeg_error"

    def _settle_process(self) -> None:
        process = getattr(self._source, "_process", None)
        wait = getattr(process, "wait", None)
        if callable(wait):
            try:
                wait(timeout=0.1)
            except (subprocess.TimeoutExpired, TimeoutError):
                pass
        reader = getattr(self._source, "_pipe_reader_thread", None)
        join = getattr(reader, "join", None)
        if callable(join):
            join(timeout=0.1)


class FfmpegPlayer:
    def __init__(self, executable: str) -> None:
        self._executable = executable

    def create_source(self, track: Track, stream: MediaStream) -> discord.AudioSource:
        if not track.stream_url:
            raise PlaybackError(f"no stream URL is available for {track.title}")
        try:
            stderr = io.BytesIO()
            source = discord.FFmpegPCMAudio(
                cast(io.BufferedIOBase, stream),
                executable=self._executable,
                pipe=True,
                stderr=stderr,
                before_options=shlex.join(["-re"]),
                options="-vn -loglevel error",
            )
            logger.info(
                "Created FFmpeg source for track %r: host=%s format=%s pid=%s "
                "headers=%s",
                track.title,
                urlparse(track.stream_url).hostname or "unknown",
                parse_qs(urlparse(track.stream_url).query).get("itag", ["unknown"])[0],
                getattr(getattr(source, "_process", None), "pid", "unknown"),
                tuple(header_name for header_name, _ in track.http_headers),
            )
            return _LoggedAudioSource(source, track, stderr, stream)
        except (OSError, discord.ClientException) as exc:
            stream.close()
            logger.warning(
                "FFmpeg source creation failed for track %r: error_type=%s",
                track.title,
                type(exc).__name__,
            )
            raise PlaybackError(f"FFmpeg could not start for {track.title}") from exc
