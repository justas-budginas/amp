import pytest

from discord_music_bot.music.models import SourceResult
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

    assert captured["format"] == "18"
    assert captured["js_runtimes"] == {"node": {}}


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
