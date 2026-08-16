import shlex

import discord_music_bot.music.player as player_module
from discord_music_bot.music.models import Track
from discord_music_bot.music.player import FfmpegPlayer


def test_ffmpeg_player_reads_stream_at_real_time(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_ffmpeg(_url: str, **kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(player_module.discord, "FFmpegPCMAudio", fake_ffmpeg)

    FfmpegPlayer("ffmpeg").create_source(
        Track(
            title="track",
            source_url="https://youtube.com/watch?v=track",
            stream_url="https://stream.example/track",
        )
    )

    options = shlex.split(captured["before_options"])
    assert options[:2] == ["-re", "-reconnect"]
