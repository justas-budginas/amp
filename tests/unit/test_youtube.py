import pytest

import discord_music_bot.music.sources.youtube as youtube_module
from discord_music_bot.music.models import SourceResult, Track
from discord_music_bot.music.sources.base import InvalidSourceError
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
    assert stream.read() == b"audio"
    stream.close()

    options = captured["options"]
    assert isinstance(options, dict)
    assert options["http_headers"] == {"User-Agent": "test-agent"}
    assert captured["url"] == track.stream_url
    assert captured["response"].closed is True
    assert captured["downloader_closed"] is True


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
