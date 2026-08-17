from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
import time
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
        title: str,
    ) -> None:
        self._response = response
        self._downloader = downloader
        self._stream_url = stream_url
        self._title = title
        self._closed = False
        self._state_lock = threading.Lock()
        self._offset = 0
        self._reconnects = 0

    def read(self, size: int = -1) -> bytes:
        while not self._closed:
            try:
                data = self._response.read(size)
                self._offset += len(data)
                return data
            except yt_dlp.networking.exceptions.IncompleteRead as exc:
                discarded_bytes = max(exc.partial, 0)
                if self._reconnects >= self._max_reconnects:
                    logger.warning(
                        "YouTube media relay exhausted reconnects: title=%r bytes_read=%s "
                        "bytes_discarded=%s bytes_expected=%s reconnects=%s",
                        self._title,
                        self._offset,
                        discarded_bytes,
                        exc.expected if isinstance(exc.expected, int) else "unknown",
                        self._reconnects,
                    )
                    self._close_response()
                    return b""
                if not self._resume(discarded_bytes):
                    return b""
            except yt_dlp.networking.exceptions.TransportError as exc:
                logger.warning(
                    "YouTube media relay failed: title=%r error_type=%s bytes_read=%s "
                    "reconnects=%s",
                    self._title,
                    type(exc).__name__,
                    self._offset,
                    self._reconnects,
                )
                self._close_response()
                return b""
        return b""

    def _resume(self, discarded_bytes: int) -> bool:
        started_at = time.monotonic()
        try:
            response = self._downloader.urlopen(
                yt_dlp.networking.Request(
                    self._stream_url,
                    headers={"Range": f"bytes={self._offset}-"},
                )
            )
        except Exception as exc:
            logger.warning(
                "YouTube media relay reconnect failed: title=%r error_type=%s "
                "bytes_read=%s bytes_discarded=%s reconnects=%s elapsed_ms=%s",
                self._title,
                type(exc).__name__,
                self._offset,
                discarded_bytes,
                self._reconnects,
                round((time.monotonic() - started_at) * 1000),
            )
            self._close_response()
            return False

        status = getattr(response, "status", None)
        if status != 206:
            logger.warning(
                "YouTube media relay reconnect rejected: title=%r status=%s "
                "bytes_read=%s bytes_discarded=%s elapsed_ms=%s",
                self._title,
                status if isinstance(status, int) else "unknown",
                self._offset,
                discarded_bytes,
                round((time.monotonic() - started_at) * 1000),
            )
            with contextlib.suppress(Exception):
                response.close()
            self._close_response()
            return False

        with self._state_lock:
            if self._closed:
                with contextlib.suppress(Exception):
                    response.close()
                return False
            previous_response = self._response
            self._response = response
            self._reconnects += 1
        with contextlib.suppress(Exception):
            previous_response.close()
        logger.info(
            "Resumed YouTube media relay: title=%r bytes_read=%s reconnects=%s "
            "bytes_discarded=%s elapsed_ms=%s",
            self._title,
            self._offset,
            self._reconnects,
            discarded_bytes,
            round((time.monotonic() - started_at) * 1000),
        )
        return True

    def _close_response(self) -> None:
        with self._state_lock:
            response = self._response
        with contextlib.suppress(Exception):
            response.close()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            response = self._response
        try:
            with contextlib.suppress(Exception):
                response.close()
        finally:
            self._downloader.close()


class _BufferedYoutubeStream:
    _chunk_size = 8 * 1024
    _max_chunks = 128
    _underrun_log_threshold_seconds = 2.0

    def __init__(self, source: MediaStream, title: str) -> None:
        self._source = source
        self._title = title
        self._chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=self._max_chunks)
        self._buffer = bytearray()
        self._stop = threading.Event()
        self._closed = False
        self._eof = False
        self._delivered_data = False
        self._underruns = 0
        self._state_lock = threading.Lock()
        self._producer: threading.Thread | None = None

    def read(self, size: int = -1) -> bytes:
        if size == 0 or not self._start_producer():
            return b""
        if size < 0:
            while not self._eof:
                self._receive_chunk()
            data = bytes(self._buffer)
            self._buffer.clear()
            self._delivered_data = self._delivered_data or bool(data)
            return data

        while len(self._buffer) < size and not self._eof:
            self._receive_chunk()
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        self._delivered_data = self._delivered_data or bool(data)
        return data

    def _start_producer(self) -> bool:
        with self._state_lock:
            if self._closed:
                return False
            if self._producer is None:
                self._producer = threading.Thread(
                    target=self._produce,
                    daemon=True,
                    name="youtube-media-prefetch",
                )
                self._producer.start()
            return True

    def _receive_chunk(self) -> None:
        queue_was_empty = self._chunks.empty()
        started_at = time.monotonic()
        chunk = self._chunks.get()
        elapsed_seconds = time.monotonic() - started_at
        if (
            queue_was_empty
            and self._delivered_data
            and elapsed_seconds >= self._underrun_log_threshold_seconds
        ):
            self._underruns += 1
            logger.warning(
                "YouTube media buffer underrun: title=%r wait_ms=%s occurrences=%s",
                self._title,
                round(elapsed_seconds * 1000),
                self._underruns,
            )
        if chunk is None:
            self._eof = True
        else:
            self._buffer.extend(chunk)

    def _produce(self) -> None:
        try:
            while not self._stop.is_set():
                data = self._source.read(self._chunk_size)
                if not data or not self._put(data):
                    return
        except Exception as exc:
            logger.warning(
                "YouTube media prefetch failed: title=%r error_type=%s",
                self._title,
                type(exc).__name__,
            )
        finally:
            self._put(None)

    def _put(self, chunk: bytes | None) -> bool:
        while not self._stop.is_set():
            try:
                self._chunks.put(chunk, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            producer = self._producer
        self._stop.set()
        self._source.close()
        with contextlib.suppress(queue.Full):
            self._chunks.put_nowait(None)
        if producer is not None and producer is not threading.current_thread():
            producer.join(timeout=0)


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
        return await self._resolve(track)

    async def resolve_retry(self, track: Track, failed: Track) -> Track:
        failed_format = parse_qs(urlparse(failed.stream_url or "").query).get("itag", [None])[0]
        return await self._resolve(
            failed,
            excluded_format_id=failed_format,
            excluded_stream_url=failed.stream_url,
        )

    async def _resolve(
        self,
        track: Track,
        excluded_format_id: str | None = None,
        excluded_stream_url: str | None = None,
    ) -> Track:
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
        selected_info = info
        if excluded_format_id or excluded_stream_url:
            alternate = self._alternate_audio_format(
                info,
                excluded_format_id,
                excluded_stream_url,
            )
            if alternate is not None:
                selected_info = dict(info)
                selected_info.update(alternate)
        resolved = self._track_from_info(selected_info, existing=track)
        if resolved is None or not resolved.stream_url:
            raise ExtractionError(f"no playable stream found for {track.title}")
        stream_url = urlparse(resolved.stream_url)
        logger.info(
            "Resolved YouTube stream: title=%r host=%s format=%s protocol=%s "
            "headers=%s duration=%s",
            resolved.title,
            stream_url.hostname or "unknown",
            parse_qs(stream_url.query).get("itag", ["unknown"])[0],
            selected_info.get("protocol", "unknown"),
            tuple(header_name for header_name, _ in resolved.http_headers),
            resolved.duration,
        )
        return resolved

    async def open_stream(self, track: Track) -> MediaStream:
        if not track.stream_url:
            raise ExtractionError(f"no playable stream found for {track.title}")
        stream_url = urlparse(track.stream_url)
        open_task = asyncio.create_task(asyncio.to_thread(self._open_stream_sync, track))
        try:
            stream = await asyncio.wait_for(
                asyncio.shield(open_task),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            open_task.add_done_callback(self._close_late_stream)
            logger.warning(
                "YouTube media stream opening timed out: title=%r host=%s",
                track.title,
                stream_url.hostname or "unknown",
            )
            raise ExtractionError("YouTube stream opening timed out") from exc
        except asyncio.CancelledError:
            open_task.add_done_callback(self._close_late_stream)
            raise
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
            "Opened YouTube media stream: title=%r host=%s format=%s transport=relay "
            "buffer_bytes=%s",
            track.title,
            stream_url.hostname or "unknown",
            parse_qs(stream_url.query).get("itag", ["unknown"])[0],
            _BufferedYoutubeStream._chunk_size * _BufferedYoutubeStream._max_chunks,
        )
        return stream

    @staticmethod
    def _close_late_stream(task: asyncio.Task[MediaStream]) -> None:
        if task.cancelled():
            return
        try:
            stream = task.result()
        except Exception:
            return
        stream.close()

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
        stream = _YoutubeStream(response, downloader, track.stream_url or "", track.title)
        return _BufferedYoutubeStream(stream, track.title)

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

    @classmethod
    def _alternate_audio_format(
        cls,
        info: Mapping[str, object],
        excluded_format_id: str | None,
        excluded_stream_url: str | None,
    ) -> Mapping[str, object] | None:
        formats = info.get("formats")
        if not isinstance(formats, list):
            return None
        candidates: list[Mapping[str, object]] = []
        for candidate in formats:
            if not isinstance(candidate, Mapping):
                continue
            candidate_url = cls._text(candidate.get("url"))
            if cls._text(candidate.get("acodec")) in {None, "none"}:
                continue
            if cls._text(candidate.get("protocol")) not in {"http", "https"}:
                continue
            if candidate_url:
                candidates.append(candidate)

        preferred_fallbacks = {
            "251": ("140", "139", "18"),
            "140": ("139", "18"),
            "250": ("139", "18"),
            "249": ("18",),
            "139": ("18",),
        }
        for fallback_id in preferred_fallbacks.get(excluded_format_id or "", ()):
            for candidate in candidates:
                candidate_url = cls._text(candidate.get("url"))
                candidate_format = cls._text(candidate.get("format_id"))
                candidate_itag = parse_qs(urlparse(candidate_url or "").query).get(
                    "itag", [None]
                )[0]
                if candidate_format == fallback_id or candidate_itag == fallback_id:
                    return candidate

        for index, candidate in enumerate(candidates):
            candidate_url = cls._text(candidate.get("url"))
            candidate_format = cls._text(candidate.get("format_id"))
            candidate_itag = parse_qs(urlparse(candidate_url or "").query).get(
                "itag", [None]
            )[0]
            if candidate_url == excluded_stream_url or excluded_format_id and (
                candidate_format == excluded_format_id
                or candidate_format is not None
                and candidate_format.split("-", 1)[0] == excluded_format_id
                or candidate_itag == excluded_format_id
            ):
                return candidates[index - 1] if index > 0 else None
        return candidates[-1] if candidates else None

    @staticmethod
    def _clean_error(message: str) -> str:
        return message.split("\n", 1)[0][:300] or "YouTube request failed"
