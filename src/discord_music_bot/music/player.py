from __future__ import annotations

import shlex
import sys

import discord

from .models import Track


class PlaybackError(RuntimeError):
    """Raised when FFmpeg cannot create an audio source."""


class FfmpegPlayer:
    def __init__(self, executable: str) -> None:
        self._executable = executable

    def create_source(self, track: Track) -> discord.AudioSource:
        if not track.stream_url:
            raise PlaybackError(f"no stream URL is available for {track.title}")
        before_options = [
            "-re",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
        if track.http_headers:
            header_value = "".join(
                f"{header_name}: {header_value}\r\n"
                for header_name, header_value in track.http_headers
            )
            before_options.extend(("-headers", header_value))
        try:
            return discord.FFmpegPCMAudio(
                track.stream_url,
                executable=self._executable,
                stderr=sys.stderr.buffer,
                before_options=shlex.join(before_options),
                options="-vn -loglevel error",
            )
        except (OSError, discord.ClientException) as exc:
            raise PlaybackError(f"FFmpeg could not start for {track.title}") from exc
