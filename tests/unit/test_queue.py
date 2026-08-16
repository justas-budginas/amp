import pytest

from discord_music_bot.music.models import Track
from discord_music_bot.music.queue import QueueFullError, TrackQueue


def track(title: str) -> Track:
    return Track(title=title, source_url=f"https://youtube.com/watch?v={title}")


def test_queue_preserves_order() -> None:
    queue = TrackQueue(max_size=3)

    queue.add_many((track("one"), track("two")))

    assert queue.pop_next().title == "one"
    assert queue.snapshot()[0].title == "two"


def test_queue_rejects_batch_atomically_when_full() -> None:
    queue = TrackQueue(max_size=2)
    queue.add_many((track("one"),))

    with pytest.raises(QueueFullError):
        queue.add_many((track("two"), track("three")))

    assert [item.title for item in queue.snapshot()] == ["one"]


def test_queue_can_prepend_tracks_in_order() -> None:
    queue = TrackQueue(max_size=4)
    queue.add_many((track("current"), track("later")))

    queue.prepend_many((track("previous"), track("replay")))

    assert [item.title for item in queue.snapshot()] == [
        "previous",
        "replay",
        "current",
        "later",
    ]


def test_queue_clear_removes_all_tracks() -> None:
    queue = TrackQueue(max_size=2)
    queue.add_many((track("one"), track("two")))

    queue.clear()

    assert len(queue) == 0
