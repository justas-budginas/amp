import asyncio
import threading

import pytest

import discord_music_bot.music.sources.youtube as youtube_module
from discord_music_bot.music.models import SourceResult, Track
from discord_music_bot.music.sources.base import ExtractionError, InvalidSourceError
from discord_music_bot.music.sources.youtube import YoutubeSource


def test_validate_url_accepts_youtube_hosts() -> None:
    assert YoutubeSource.validate_url("https://youtu.be/video") == "https://youtu.be/video"


def test_validate_url_rejects_other_hosts() -> None:
    with pytest.raises(InvalidSourceError):
        YoutubeSource.validate_url("https://example.com/video")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/playlist?list=abc", True),
        ("https://www.youtube.com/watch?v=abc&list=xyz", True),
        ("https://www.youtube.com/watch?v=abc", False),
    ],
)
def test_playlist_detection(url: str, expected: bool) -> None:
    assert YoutubeSource.is_playlist_url(url) is expected


@pytest.mark.asyncio
async def test_playlist_load_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    source = YoutubeSource(max_queue_size=5, timeout_seconds=5)

    async def fake_extract(_url: str, *, playlist: bool) -> dict[str, object]:
        assert playlist is True
        return {
            "_type": "playlist",
            "title": "Test playlist",
            "entries": [
                {
                    "id": "one",
                    "title": "One",
                    "http_headers": {"User-Agent": "test-agent"},
                },
                {"id": "two", "title": "Two"},
            ],
        }

    monkeypatch.setattr(source, "_extract_info", fake_extract)

    result = await source.load("https://www.youtube.com/playlist?list=test")

    assert isinstance(result, SourceResult)
    assert [item.title for item in result.tracks] == ["One", "Two"]
    assert [item.playlist_index for item in result.tracks] == [1, 2]
    assert result.tracks[0].http_headers == (("User-Agent", "test-agent"),)


@pytest.mark.asyncio
async def test_extraction_enables_node_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    source = YoutubeSource(max_queue_size=5, timeout_seconds=5)
    captured: dict[str, object] = {}

    def fake_extract(_url: str, options: dict[str, object]) -> dict[str, object]:
        captured.update(options)
        return {}

    monkeypatch.setattr(source, "_extract_sync", fake_extract)

    await source._extract_info("https://www.youtube.com/watch?v=test", playlist=False)

    assert captured["format"] == "bestaudio/best"
    assert captured["js_runtimes"] == {"node": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_itag", "expected_itag"),
    [("251", "140"), ("140", "139"), ("249", "18")],
)
async def test_retry_resolution_uses_alternate_audio_format(
    monkeypatch: pytest.MonkeyPatch,
    failed_itag: str,
    expected_itag: str,
) -> None:
    source = YoutubeSource(max_queue_size=5, timeout_seconds=5)

    async def fake_extract(_url: str, *, playlist: bool) -> dict[str, object]:
        assert playlist is False
        return {
            "id": "track",
            "title": "Track",
            "url": "https://media.example/audio?itag=251",
            "format_id": "251",
            "acodec": "opus",
            "vcodec": "none",
            "protocol": "https",
            "formats": [
                {
                    "url": "https://media.example/audio?itag=139",
                    "format_id": "139",
                    "acodec": "mp4a.40.5",
                    "vcodec": "none",
                    "protocol": "https",
                },
                {
                    "url": "https://media.example/audio?itag=249",
                    "format_id": "249",
                    "acodec": "opus",
                    "vcodec": "none",
                    "protocol": "https",
                },
                {
                    "url": "https://media.example/audio?itag=140",
                    "format_id": "140",
                    "acodec": "mp4a.40.2",
                    "vcodec": "none",
                    "protocol": "https",
                },
                {
                    "url": "https://media.example/audio?itag=251",
                    "format_id": "251-drc",
                    "acodec": "opus",
                    "vcodec": "none",
                    "protocol": "https",
                },
                {
                    "url": "https://media.example/audio?itag=18",
                    "format_id": "18",
                    "acodec": "mp4a.40.2",
                    "vcodec": "avc1.42001E",
                    "protocol": "https",
                },
            ],
        }

    monkeypatch.setattr(source, "_extract_info", fake_extract)
    track = Track(title="Track", source_url="https://youtube.com/watch?v=track")
    failed = Track(
        title="Track",
        source_url=track.source_url,
        stream_url=f"https://media.example/audio?itag={failed_itag}",
    )

    resolved = await source.resolve_retry(track, failed)

    assert resolved.stream_url == f"https://media.example/audio?itag={expected_itag}"
    assert resolved.source_url == "https://www.youtube.com/watch?v=track"


def test_open_stream_uses_ytdlp_http_client_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        closed = False

        def read(self, _size: int = -1) -> bytes:
            return b"audio"

        def close(self) -> None:
            self.closed = True

    class FakeDownloader:
        closed = False

        def __init__(self, options: dict[str, object]) -> None:
            captured["options"] = options

        def urlopen(self, request) -> FakeResponse:
            captured["url"] = request.url
            response = FakeResponse()
            captured["response"] = response
            return response

        def close(self) -> None:
            self.closed = True
            captured["downloader_closed"] = True

    monkeypatch.setattr(youtube_module.yt_dlp, "YoutubeDL", FakeDownloader)
    track = Track(
        title="track",
        source_url="https://youtube.com/watch?v=track",
        stream_url="https://media.example/audio?sig=secret",
        http_headers=(("User-Agent", "test-agent"),),
    )

    stream = YoutubeSource._open_stream_sync(track)
    assert stream.read(5) == b"audio"
    stream.close()

    options = captured["options"]
    assert isinstance(options, dict)
    assert options["http_headers"] == {"User-Agent": "test-agent"}
    assert captured["url"] == track.stream_url
    assert captured["response"].closed is True
    assert captured["downloader_closed"] is True


def test_stream_resumes_after_incomplete_read(caplog: pytest.LogCaptureFixture) -> None:
    class TruncatedResponse:
        status = 200
        closed = False

        def read(self, _size: int = -1) -> bytes:
            raise youtube_module.yt_dlp.networking.exceptions.IncompleteRead(
                partial=4096,
                expected=1024,
            )

        def close(self) -> None:
            self.closed = True

    class ResumedResponse:
        status = 206
        closed = False

        def read(self, _size: int = -1) -> bytes:
            return b"remaining audio"

        def close(self) -> None:
            self.closed = True

    class FakeDownloader:
        closed = False

        def __init__(self) -> None:
            self.requests = []
            self.resumed_response = ResumedResponse()

        def urlopen(self, request) -> ResumedResponse:
            self.requests.append(request)
            return self.resumed_response

        def close(self) -> None:
            self.closed = True

    initial_response = TruncatedResponse()
    downloader = FakeDownloader()
    stream = youtube_module._YoutubeStream(
        initial_response,
        downloader,
        "https://media.example/audio?sig=secret",
        "track",
    )

    with caplog.at_level("INFO", logger="discord_music_bot.music.sources.youtube"):
        assert stream.read(8192) == b"remaining audio"
    assert len(downloader.requests) == 1
    assert downloader.requests[0].headers["Range"] == "bytes=0-"
    assert initial_response.closed is True
    assert "title='track'" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "sig=secret" not in caplog.text

    stream.close()
    assert downloader.resumed_response.closed is True
    assert downloader.closed is True


def test_stream_resumes_from_bytes_delivered_to_ffmpeg() -> None:
    class TruncatedResponse:
        def __init__(self) -> None:
            self.read_count = 0

        def read(self, _size: int = -1) -> bytes:
            self.read_count += 1
            if self.read_count == 1:
                return b"audio"
            raise youtube_module.yt_dlp.networking.exceptions.IncompleteRead(
                partial=4096,
                expected=1024,
            )

        def close(self) -> None:
            pass

    class ResumedResponse:
        status = 206

        def read(self, _size: int = -1) -> bytes:
            return b"remaining"

        def close(self) -> None:
            pass

    class FakeDownloader:
        def __init__(self) -> None:
            self.requests = []

        def urlopen(self, request) -> ResumedResponse:
            self.requests.append(request)
            return ResumedResponse()

        def close(self) -> None:
            pass

    downloader = FakeDownloader()
    stream = youtube_module._YoutubeStream(
        TruncatedResponse(),
        downloader,
        "https://media.example/audio?sig=secret",
        "track",
    )

    assert stream.read(8192) == b"audio"
    assert stream.read(8192) == b"remaining"
    assert downloader.requests[0].headers["Range"] == "bytes=5-"

    stream.close()


def test_buffered_stream_prefetches_media() -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.read_started = threading.Event()
            self.read_finished = threading.Event()
            self.read_count = 0
            self.read_sizes: list[int] = []
            self.closed = False

        def read(self, size: int = -1) -> bytes:
            self.read_count += 1
            self.read_sizes.append(size)
            self.read_started.set()
            if self.read_count == 1:
                return b"audio"
            self.read_finished.set()
            return b""

        def close(self) -> None:
            self.closed = True

    source = FakeStream()
    stream = youtube_module._BufferedYoutubeStream(source, "track")

    assert not source.read_started.wait(timeout=0.01)
    assert stream.read(5) == b"audio"
    assert source.read_finished.wait(timeout=1)
    assert source.read_sizes[0] == 8 * 1024
    stream.close()

    assert source.closed is True


def test_stream_close_during_reconnect_closes_replacement_response() -> None:
    class TruncatedResponse:
        def read(self, _size: int = -1) -> bytes:
            raise youtube_module.yt_dlp.networking.exceptions.IncompleteRead(
                partial=4096,
                expected=1024,
            )

        def close(self) -> None:
            pass

    class ResumedResponse:
        status = 206
        closed = False

        def close(self) -> None:
            self.closed = True

    class BlockingDownloader:
        def __init__(self) -> None:
            self.open_started = threading.Event()
            self.release_open = threading.Event()
            self.response = ResumedResponse()

        def urlopen(self, _request) -> ResumedResponse:
            self.open_started.set()
            self.release_open.wait(timeout=1)
            return self.response

        def close(self) -> None:
            pass

    downloader = BlockingDownloader()
    stream = youtube_module._YoutubeStream(
        TruncatedResponse(),
        downloader,
        "https://media.example/audio?sig=secret",
        "track",
    )
    reader = threading.Thread(target=stream.read, args=(8192,))
    reader.start()
    assert downloader.open_started.wait(timeout=1)

    stream.close()
    downloader.release_open.set()
    reader.join(timeout=1)

    assert not reader.is_alive()
    assert downloader.response.closed is True


@pytest.mark.asyncio
async def test_timed_out_stream_open_closes_late_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LateStream:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def read(self, _size: int = -1) -> bytes:
            return b""

        def close(self) -> None:
            self.closed.set()

    source = YoutubeSource(max_queue_size=5, timeout_seconds=0.01)
    open_started = threading.Event()
    release_open = threading.Event()
    late_stream = LateStream()

    def delayed_open(_track: Track) -> LateStream:
        open_started.set()
        release_open.wait(timeout=1)
        return late_stream

    monkeypatch.setattr(source, "_open_stream_sync", delayed_open)
    track = Track(
        title="track",
        source_url="https://youtube.com/watch?v=track",
        stream_url="https://media.example/audio?itag=251&sig=secret",
    )

    with pytest.raises(ExtractionError, match="opening timed out"):
        await source.open_stream(track)
    assert open_started.wait(timeout=1)
    release_open.set()

    async def wait_until_closed() -> None:
        while not late_stream.closed.is_set():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_closed(), timeout=1)


@pytest.mark.asyncio
async def test_playlist_load_truncates_more_than_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    source = YoutubeSource(max_queue_size=1, timeout_seconds=5)

    async def fake_extract(_url: str, *, playlist: bool) -> dict[str, object]:
        return {
            "playlist_count": 2,
            "entries": [
                {"id": "one", "title": "One"},
                {"id": "two", "title": "Two"},
            ],
        }

    monkeypatch.setattr(source, "_extract_info", fake_extract)

    result = await source.load("https://www.youtube.com/playlist?list=test")

    assert [item.source_url for item in result.tracks] == ["https://www.youtube.com/watch?v=one"]
    assert result.playlist_truncated is True
