from datetime import datetime, timezone

from termchat.domain.message import Message


def test_message_construction():
    msg = Message(
        id="abc123",
        author="streamer",
        text="Hello world",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform="twitch",
    )
    assert msg.id == "abc123"
    assert msg.author == "streamer"
    assert msg.text == "Hello world"
    assert msg.platform == "twitch"


def test_message_is_frozen():
    msg = Message(
        id="x",
        author="a",
        text="t",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform="twitch",
    )
    try:
        msg.text = "changed"  # type: ignore[misc]
        assert False, "should have raised"
    except Exception:
        pass


def test_message_equality():
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    m1 = Message(id="1", author="a", text="t", timestamp=ts, platform="p")
    m2 = Message(id="1", author="a", text="t", timestamp=ts, platform="p")
    assert m1 == m2
