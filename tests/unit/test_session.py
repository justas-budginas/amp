import asyncio
import logging
from types import SimpleNamespace

import pytest

from discord_music_bot.music.models import PlaybackState, Track
from discord_music_bot.music.session import MusicSession
from discord_music_bot.music.sources.base import ExtractionError


class FakeSource:
    async def load(self, _url: str):
        raise NotImplementedError

    async def resolve(self, track: Track) -> Track:
        return track

    async def open_stream(self, _track: Track):
        raise NotImplementedError


class FakePlayer:
    def create_source(self, _track: Track, _stream):
        raise NotImplementedError


class FakeVoice:
    def __init__(self) -> None:
        self.channel = SimpleNamespace(id=1)
        self.playing = True

    def is_connected(self) -> bool:
        return True

    def is_playing(self) -> bool:
        return self.playing

    def stop(self) -> None:
        self.playing = False


class FakeChannel:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, object]]] = []

    async def send(self, message: str, **kwargs: object) -> None:
        self.messages.append((message, kwargs))


class PlaybackSource:
    async def resolve(self, track: Track) -> Track:
        return track

    async def open_stream(self, _track: Track):
        return PlaybackStream()


class PlaybackStream:
    def read(self, _size: int = -1) -> bytes:
        return b""

    def close(self) -> None:
        pass


class PlaybackAudio:
    def cleanup(self) -> None:
        pass


class PlaybackPlayer:
    def create_source(self, _track: Track, _stream: PlaybackStream) -> PlaybackAudio:
        return PlaybackAudio()


class PlaybackVoice:
    def __init__(self) -> None:
        self.channel = SimpleNamespace(id=1)
        self.playing = False

    def is_connected(self) -> bool:
        return True

    def is_playing(self) -> bool:
        return self.playing

    def play(self, _audio: PlaybackAudio, *, after) -> None:
        self.playing = True
        loop = asyncio.get_running_loop()

        def finish() -> None:
            self.playing = False
            after(None)

        loop.call_soon(finish)

    def stop(self) -> None:
        self.playing = False

    async def disconnect(self, *, force: bool) -> None:
        self.playing = False


def make_track(title: str) -> Track:
    return Track(title=title, source_url=f"https://youtube.com/watch?v={title}")


@pytest.mark.asyncio
async def test_previous_prepends_previous_and_replays_current() -> None:
    session = MusicSession(
        1,
        FakeSource(),
        FakePlayer(),
        max_queue_size=5,
        idle_disconnect_seconds=60,
    )
    current = make_track("current")
    previous = make_track("previous")
    session._voice = FakeVoice()
    session._current = current
    session._state = PlaybackState.PLAYING
    session._history.append(previous)

    await session.previous()

    assert [track.title for track in await session.queue_snapshot()] == [
        "previous",
        "current",
    ]
    assert session._voice.playing is False


@pytest.mark.asyncio
async def test_clear_queue_stops_current_track() -> None:
    session = MusicSession(
        1,
        FakeSource(),
        FakePlayer(),
        max_queue_size=5,
        idle_disconnect_seconds=60,
    )
    voice = FakeVoice()
    session._voice = voice
    session._current = make_track("current")
    session._state = PlaybackState.PLAYING
    session._queue.add_many((make_track("queued"),))

    await session.clear_queue()

    assert await session.queue_snapshot() == ()
    assert voice.playing is False
    current, state = await session.current()
    assert current is None
    assert state is PlaybackState.IDLE


@pytest.mark.asyncio
async def test_now_playing_notification_is_silent_and_clickable() -> None:
    session = MusicSession(
        1,
        FakeSource(),
        FakePlayer(),
        max_queue_size=5,
        idle_disconnect_seconds=60,
    )
    channel = FakeChannel()
    session.set_notification_channel(channel)
    track = make_track("current")

    await session._notify_now_playing(track)

    message, options = channel.messages[0]
    assert message == "Now playing: [current](https://youtube.com/watch?v=current)"
    assert options["silent"] is True


@pytest.mark.asyncio
async def test_playback_announces_each_track_transition() -> None:
    session = MusicSession(
        1,
        PlaybackSource(),
        PlaybackPlayer(),
        max_queue_size=5,
        idle_disconnect_seconds=60,
    )
    channel = FakeChannel()
    session.set_notification_channel(channel)
    session._voice = PlaybackVoice()
    session._queue.add_many((make_track("first"), make_track("second")))
    session._playback_task = asyncio.create_task(session._playback_loop())

    async def wait_for_announcements() -> None:
        while len(channel.messages) < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_announcements(), timeout=1)
    await session.leave()

    assert [message for message, _ in channel.messages] == [
        "Now playing: [first](https://youtube.com/watch?v=first)",
        "Now playing: [second](https://youtube.com/watch?v=second)",
    ]


@pytest.mark.asyncio
async def test_playback_re_resolves_stream_open_failures() -> None:
    class RetrySource(PlaybackSource):
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.retry_calls = 0
            self.open_calls = 0

        async def resolve(self, track: Track) -> Track:
            self.resolve_calls += 1
            return track

        async def resolve_retry(self, track: Track, _failed: Track) -> Track:
            self.retry_calls += 1
            return await self.resolve(track)

        async def open_stream(self, _track: Track):
            self.open_calls += 1
            if self.open_calls <= 2:
                raise ExtractionError("stream rejected")
            return PlaybackStream()

    source = RetrySource()
    session = MusicSession(
        1,
        source,
        PlaybackPlayer(),
        max_queue_size=5,
        idle_disconnect_seconds=60,
    )
    channel = FakeChannel()
    session.set_notification_channel(channel)
    session._voice = PlaybackVoice()
    session._queue.add_many((make_track("track"),))
    session._playback_task = asyncio.create_task(session._playback_loop())

    async def wait_for_message() -> None:
        while not channel.messages:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_message(), timeout=1)
    await session.leave()

    assert source.resolve_calls == 3
    assert source.retry_calls == 2
    assert source.open_calls == 3
    assert channel.messages[0][0] == "Now playing: [track](https://youtube.com/watch?v=track)"


@pytest.mark.asyncio
async def test_successful_playback_callback_logs_info(caplog) -> None:
    session = MusicSession(
        1,
        PlaybackSource(),
        PlaybackPlayer(),
        max_queue_size=5,
        idle_disconnect_seconds=60,
    )
    session.set_notification_channel(FakeChannel())
    session._voice = PlaybackVoice()
    session._queue.add_many((make_track("track"),))

    with caplog.at_level("INFO", logger="discord_music_bot.music.session"):
        session._playback_task = asyncio.create_task(session._playback_loop())

        async def wait_for_callback() -> None:
            while not any("Voice playback callback" in record.message for record in caplog.records):
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_callback(), timeout=1)
        await session.leave()

    callback_record = next(
        record for record in caplog.records if "Voice playback callback" in record.message
    )
    assert callback_record.levelno == logging.INFO
    assert "error_type=none" in callback_record.message
