import shlex
from types import SimpleNamespace

import discord_music_bot.music.player as player_module
from discord_music_bot.music.models import Track
from discord_music_bot.music.player import FfmpegPlayer


def test_ffmpeg_player_reads_stream_at_real_time(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_ffmpeg(_url: str, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(cleanup=lambda: None, is_opus=lambda: False)

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


def test_ffmpeg_diagnostics_redact_stream_url(monkeypatch, caplog) -> None:
    class FakeProcess:
        pid = 123

        @staticmethod
        def poll() -> int:
            return 1

    class FakeAudio:
        _process = FakeProcess()
        _current_error = RuntimeError(
            "FFmpeg exited with code 1. https://googlevideo.example/stream?sig=secret"
        )

        def read(self) -> bytes:
            return b""

        def is_opus(self) -> bool:
            return False

        def cleanup(self) -> None:
            pass

    monkeypatch.setattr(
        player_module.discord,
        "FFmpegPCMAudio",
        lambda *_args, **_kwargs: FakeAudio(),
    )

    with caplog.at_level("INFO", logger="discord_music_bot.music.player"):
        audio = FfmpegPlayer("ffmpeg").create_source(
            Track(
                title="track",
                source_url="https://youtube.com/watch?v=track",
                stream_url="https://rr.example/videoplayback?itag=251&sig=secret",
            )
        )
        audio.read()

    assert "FFmpeg stream ended for track" in caplog.text
    assert "http_" not in caplog.text
    assert "googlevideo.example" not in caplog.text
    assert "sig=secret" not in caplog.text
