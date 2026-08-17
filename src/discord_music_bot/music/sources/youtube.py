from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..models import SourceResult, Track
from .base import (
    ExtractionError,
    InvalidSourceError,
    MediaStream,
    NoPlayableTracksError,
    SourceAdapter,
)

logger = logging.getLogger(__name__)


class _YoutubeStream:
    _max_reconnects = 3

    def __init__(
        self,
        response: MediaStream,
        downloader: yt_dlp.YoutubeDL,
        stream_url: str,
    ) -> None:
        self._response = response
        self._downloader = downloader
        self._stream_url = stream_url
        self._closed = False
        self._offset = 0
        self._reconnects = 0

    def read(self, size: int = -1) -> bytes:
        while not self._closed:
            try:
                return self._response.read(size)
            except yt_dlp.networking.exceptions.IncompleteRead as exc:
                self._offset += max(exc.partial, 0)
                if self._reconnects >= self._max_reconnects:
                    logger.warning(
                        "YouTube media relay exhausted reconnects: bytes_read=%s "
                        "bytes_expected=%s reconnects=%s",
                        self._offset,
                        exc.expected if isinstance(exc.expected, int) else "unknown",
                        self._reconnects,
                    )
                    self._close_response()
                    return b""
                if not self._resume():
                    return b""
            except yt_dlp.networking.exceptions.TransportError as exc:
                logger.warning(
                    "YouTube media relay failed: error_type=%s bytes_read=%s reconnects=%s",
                    type(exc).__name__,
                    self._offset,
                    self._reconnects,
                )
                self._close_response()
                return b""
        return b""

    def _resume(self) -> bool:
        try:
            response = self._downloader.urlopen(
                yt_dlp.networking.Request(
                    self._stream_url,
                    headers={"Range": f"bytes={self._offset}-"},
                )
            )
        except Exception as exc:
            logger.warning(
                "YouTube media relay reconnect failed: error_type=%s bytes_read=%s "
                "reconnects=%s",
                type(exc).__name__,
                self._offset,
                self._reconnects,
            )
            self._close_response()
            return False

        status = getattr(response, "status", None)
        if status != 206:
            logger.warning(
                "YouTube media relay reconnect rejected: status=%s bytes_read=%s",
                status if isinstance(status, int) else "unknown",
                self._offset,
            )
            with contextlib.suppress(Exception):
                response.close()
            self._close_response()
            return False

        self._close_response()
        self._response = response
        self._reconnects += 1
        logger.info(
            "Resumed YouTube media relay: bytes_read=%s reconnects=%s",
            self._offset,
            self._reconnects,
        )
        return True

    def _close_response(self) -> None:
        with contextlib.suppress(Exception):
            self._response.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._close_response()
        finally:
            self._downloader.close()


class YoutubeSource(SourceAdapter):
    _allowed_hosts = {"youtube.com", "www.youtube.com", "music.youtube.com", "youtu.be"}

    def __init__(self, max_queue_size: int, timeout_seconds: int) -> None:
        self._max_queue_size = max_queue_size
        self._timeout_seconds = timeout_seconds

    @classmethod
    def validate_url(cls, url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in cls._allowed_hosts:
            raise InvalidSourceError("only YouTube URLs are supported")
        if len(url) > 2048:
            raise InvalidSourceError("the URL is too long")
        return url.strip()

    @classmethod
    def is_playlist_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        return parsed.path.rstrip("/") == "/playlist" or bool(query.get("list"))

    async def load(self, url: str) -> SourceResult:
        validated_url = self.validate_url(url)
        playlist = self.is_playlist_url(validated_url)
        try:
            info = await self._extract_info(validated_url, playlist=playlist)
        except TimeoutError as exc:
            raise ExtractionError("YouTube extraction timed out") from exc
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractionError(self._clean_error(str(exc))) from exc
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError("YouTube extraction failed") from exc

        if not info:
            raise NoPlayableTracksError("YouTube did not return any playable information")

        if playlist:
            return self._playlist_result(info)

        track = self._track_from_info(info)
        if track is None:
            raise NoPlayableTracksError("the YouTube video is unavailable or has no playable URL")
        return SourceResult((track,))

    async def resolve(self, track: Track) -> Track:
        logger.info("Resolving YouTube stream: title=%r", track.title)
        try:
            info = await self._extract_info(track.source_url, playlist=False)
        except TimeoutError as exc:
            raise ExtractionError("YouTube stream extraction timed out") from exc
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractionError(self._clean_error(str(exc))) from exc
        except Exception as exc:
            raise ExtractionError("YouTube stream extraction failed") from exc

        if not info:
            raise ExtractionError(f"no playable stream found for {track.title}")
        resolved = self._track_from_info(info, existing=track)
        if resolved is None or not resolved.stream_url:
            raise ExtractionError(f"no playable stream found for {track.title}")
        stream_url = urlparse(resolved.stream_url)
        logger.info(
            "Resolved YouTube stream: title=%r host=%s format=%s protocol=%s "
            "headers=%s duration=%s",
            resolved.title,
            stream_url.hostname or "unknown",
            parse_qs(stream_url.query).get("itag", ["unknown"])[0],
            info.get("protocol", "unknown"),
            tuple(header_name for header_name, _ in resolved.http_headers),
            resolved.duration,
        )
        return resolved

    async def open_stream(self, track: Track) -> MediaStream:
        if not track.stream_url:
            raise ExtractionError(f"no playable stream found for {track.title}")
        stream_url = urlparse(track.stream_url)
        try:
            stream = await asyncio.wait_for(
                asyncio.to_thread(self._open_stream_sync, track),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            logger.warning(
                "YouTube media stream opening timed out: title=%r host=%s",
                track.title,
                stream_url.hostname or "unknown",
            )
            raise ExtractionError("YouTube stream opening timed out") from exc
        except Exception as exc:
            status = getattr(exc, "status", "unknown")
            logger.warning(
                "YouTube media stream opening failed: title=%r host=%s "
                "error_type=%s status=%s",
                track.title,
                stream_url.hostname or "unknown",
                type(exc).__name__,
                status if isinstance(status, int) else "unknown",
            )
            raise ExtractionError("YouTube stream opening failed") from exc
        logger.info(
            "Opened YouTube media stream: title=%r host=%s format=%s transport=relay",
            track.title,
            stream_url.hostname or "unknown",
            parse_qs(stream_url.query).get("itag", ["unknown"])[0],
        )
        return stream

    @staticmethod
    def _open_stream_sync(track: Track) -> MediaStream:
        downloader = yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "http_headers": dict(track.http_headers),
            }
        )
        try:
            response = downloader.urlopen(yt_dlp.networking.Request(track.stream_url or ""))
        except Exception:
            downloader.close()
            raise
        return _YoutubeStream(response, downloader, track.stream_url or "")

    async def _extract_info(self, url: str, *, playlist: bool) -> Mapping[str, object] | None:
        options: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "format": "bestaudio/best",
            "ignoreerrors": playlist,
            "noplaylist": not playlist,
            "js_runtimes": {"node": {}},
        }
        if playlist:
            options.update(
                {
                    "extract_flat": "in_playlist",
                    "playlistend": self._max_queue_size,
                }
            )
        else:
            options["extract_flat"] = False

        return await asyncio.wait_for(
            asyncio.to_thread(self._extract_sync, url, options),
            timeout=self._timeout_seconds,
        )

    @staticmethod
    def _extract_sync(url: str, options: dict[str, object]) -> Mapping[str, object] | None:
        with yt_dlp.YoutubeDL(options) as downloader:
            return downloader.extract_info(url, download=False)

    def _playlist_result(self, info: Mapping[str, object]) -> SourceResult:
        playlist_count = info.get("playlist_count")
        playlist_truncated = (
            isinstance(playlist_count, int) and playlist_count > self._max_queue_size
        )

        entries = info.get("entries")
        if not isinstance(entries, list):
            raise NoPlayableTracksError("the YouTube playlist has no playable entries")
        if len(entries) > self._max_queue_size:
            entries = entries[: self._max_queue_size]
            playlist_truncated = True

        tracks: list[Track] = []
        skipped_reasons: list[str] = []
        playlist_title = self._text(info.get("title"))
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, Mapping):
                skipped_reasons.append(f"entry {index}: unavailable")
                continue
            track = self._track_from_info(
                entry,
                playlist_title=playlist_title,
                playlist_index=index,
            )
            if track is None:
                skipped_reasons.append(f"entry {index}: unavailable")
                continue
            tracks.append(track)

        if not tracks:
            raise NoPlayableTracksError("the YouTube playlist has no playable entries")
        return SourceResult(
            tracks=tuple(tracks),
            skipped_count=len(skipped_reasons),
            skipped_reasons=tuple(skipped_reasons),
            source_title=playlist_title,
            is_playlist=True,
            playlist_truncated=playlist_truncated,
        )

    @classmethod
    def _track_from_info(
        cls,
        info: Mapping[str, object],
        *,
        existing: Track | None = None,
        playlist_title: str | None = None,
        playlist_index: int | None = None,
    ) -> Track | None:
        source_url = cls._source_url(info) or (existing.source_url if existing else None)
        if not source_url:
            return None
        title = cls._text(info.get("title")) or (existing.title if existing else None)
        if not title:
            return None
        stream_url = cls._text(info.get("url"))
        http_headers = cls._headers(info.get("http_headers"))
        return Track(
            title=title,
            source_url=source_url,
            stream_url=stream_url or (existing.stream_url if existing else None),
            http_headers=http_headers or (existing.http_headers if existing else ()),
            duration=cls._integer(info.get("duration"), existing.duration if existing else None),
            thumbnail=cls._text(info.get("thumbnail"))
            or (existing.thumbnail if existing else None),
            uploader=cls._text(info.get("uploader")) or (existing.uploader if existing else None),
            playlist_title=playlist_title or (existing.playlist_title if existing else None),
            playlist_index=playlist_index or (existing.playlist_index if existing else None),
            requester_id=existing.requester_id if existing else None,
        )

    @staticmethod
    def _source_url(info: Mapping[str, object]) -> str | None:
        webpage_url = YoutubeSource._text(info.get("webpage_url"))
        if webpage_url:
            return webpage_url
        original_url = YoutubeSource._text(info.get("original_url"))
        if original_url:
            return original_url
        video_id = YoutubeSource._text(info.get("id"))
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        candidate = YoutubeSource._text(info.get("url"))
        return candidate if candidate and candidate.startswith("http") else None

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _integer(value: object, fallback: int | None) -> int | None:
        return value if isinstance(value, int) else fallback

    @staticmethod
    def _headers(value: object) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, Mapping):
            return ()
        return tuple(
            (key, header_value)
            for key, header_value in value.items()
            if isinstance(key, str) and isinstance(header_value, str)
        )

    @staticmethod
    def _clean_error(message: str) -> str:
        return message.split("\n", 1)[0][:300] or "YouTube request failed"
