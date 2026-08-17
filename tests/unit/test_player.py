import io
import shlex
from types import SimpleNamespace

import discord_music_bot.music.player as player_module
from discord_music_bot.music.models import Track
from discord_music_bot.music.player import FfmpegPlayer


def test_ffmpeg_player_reads_relayed_stream_at_real_time(monkeypatch) -> None:
    captured: dict[str, object] = {}
    stream = io.BytesIO(b"audio")

    def fake_ffmpeg(source: object, **kwargs: object) -> object:
        captured["source"] = source
        captured.update(kwargs)
        return SimpleNamespace(cleanup=lambda: None, is_opus=lambda: False)

    monkeypatch.setattr(player_module.discord, "FFmpegPCMAudio", fake_ffmpeg)

    FfmpegPlayer("ffmpeg").create_source(
        Track(
            title="track",
            source_url="https://youtube.com/watch?v=track",
            stream_url="https://stream.example/track",
        ),
        stream,
    )

    options = shlex.split(captured["before_options"])
    assert captured["source"] is stream
    assert captured["pipe"] is True
    assert options == ["-re"]


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
            ),
            io.BytesIO(),
        )
        audio.read()

    assert "FFmpeg stream ended for track" in caplog.text
    assert "http_" not in caplog.text
    assert "googlevideo.example" not in caplog.text
    assert "sig=secret" not in caplog.text


def test_ffmpeg_diagnostics_capture_stderr_after_zero_frame_exit(monkeypatch, caplog) -> None:
    class FakeProcess:
        pid = 123

        @staticmethod
        def poll() -> int:
            return 1

        @staticmethod
        def wait(*, timeout: float) -> int:
            assert timeout == 0.1
            return 1

    class FakeAudio:
        _process = FakeProcess()
        _current_error = None
        _pipe_reader_thread = None

        def read(self) -> bytes:
            return b""

        def is_opus(self) -> bool:
            return False

        def cleanup(self) -> None:
            pass

    def fake_ffmpeg(*_args, **kwargs):
        stderr = kwargs["stderr"]
        assert isinstance(stderr, io.BytesIO)
        stderr.write(b"Server returned 403 Forbidden https://stream.example?sig=secret")
        return FakeAudio()

    monkeypatch.setattr(player_module.discord, "FFmpegPCMAudio", fake_ffmpeg)

    with caplog.at_level("INFO", logger="discord_music_bot.music.player"):
        audio = FfmpegPlayer("ffmpeg").create_source(
            Track(
                title="track",
                source_url="https://youtube.com/watch?v=track",
                stream_url="https://rr.example/videoplayback?itag=251&sig=secret",
            ),
            io.BytesIO(),
        )
        audio.read()

    assert "returncode=1" in caplog.text
    assert "error=http_403" in caplog.text
    assert "stream.example" not in caplog.text
    assert "sig=secret" not in caplog.text
