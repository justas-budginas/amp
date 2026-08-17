import io
import logging
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


def test_successful_ffmpeg_exit_logs_info_and_cleans_up_once(monkeypatch, caplog) -> None:
    cleanup_calls = 0
    stream_close_calls = 0

    class FakeProcess:
        pid = 123

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def wait(*, timeout: float) -> int:
            return 0

    class FakeAudio:
        _process = FakeProcess()
        _current_error = None
        _pipe_reader_thread = None

        def __init__(self) -> None:
            self._reads = iter((b"frame", b""))

        def read(self) -> bytes:
            return next(self._reads)

        def is_opus(self) -> bool:
            return False

        def cleanup(self) -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1

    class FakeStream(io.BytesIO):
        def close(self) -> None:
            nonlocal stream_close_calls
            stream_close_calls += 1
            super().close()

    def fake_ffmpeg(*_args, **kwargs):
        stderr = kwargs["stderr"]
        assert isinstance(stderr, io.BytesIO)
        stderr.write(b"benign ffmpeg diagnostic")
        return FakeAudio()

    monkeypatch.setattr(player_module.discord, "FFmpegPCMAudio", fake_ffmpeg)

    with caplog.at_level("INFO", logger="discord_music_bot.music.player"):
        audio = FfmpegPlayer("ffmpeg").create_source(
            Track(
                title="track",
                source_url="https://youtube.com/watch?v=track",
                stream_url="https://rr.example/videoplayback?itag=251&sig=secret",
            ),
            FakeStream(),
        )
        audio.read()
        audio.read()
        audio.cleanup()
        audio.cleanup()

    end_record = next(
        record for record in caplog.records if "FFmpeg stream ended for track" in record.message
    )
    assert end_record.levelno == logging.INFO
    assert "returncode=0" in end_record.message
    assert "error=none" in end_record.message
    assert cleanup_calls == 1
    assert stream_close_calls == 1
    assert caplog.text.count("FFmpeg cleanup for track") == 1
