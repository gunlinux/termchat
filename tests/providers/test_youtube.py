import os
from datetime import timezone
from unittest.mock import MagicMock, patch

import pytest

from termchat.providers.youtube import YouTubeProvider, _map_entry


# --- unit tests: entry mapping ---

def test_map_entry_basic():
    entry = {
        "id": "abc",
        "author": "viewer1",
        "text": "Nice stream!",
        "timestamp": 1_700_000_000_000_000,
    }
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.id == "abc"
    assert msg.author == "viewer1"
    assert msg.text == "Nice stream!"
    assert msg.platform == "youtube"
    assert msg.timestamp.tzinfo is not None


def test_map_entry_author_as_dict():
    entry = {
        "author": {"name": "viewer2"},
        "text": "Hello",
    }
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.author == "viewer2"


def test_map_entry_no_text_returns_none():
    entry = {"author": "user", "text": ""}
    assert _map_entry(entry) is None


def test_map_entry_missing_id_generates_uuid():
    entry = {"author": "a", "text": "hi"}
    msg = _map_entry(entry)
    assert msg is not None
    assert len(msg.id) > 0


def test_map_entry_no_timestamp_uses_now():
    entry = {"author": "a", "text": "hi"}
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.timestamp.tzinfo == timezone.utc


async def test_youtube_provider_maps_entries():
    entries = [
        {"id": "1", "author": "alice", "text": "Hey", "timestamp": 1_700_000_000_000_000},
        {"id": "2", "author": "bob", "text": "", "timestamp": None},
        {"id": "3", "author": "carol", "text": "Hi", "timestamp": None},
    ]
    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_extract_comments", return_value=entries):
        msgs = [m async for m in provider.messages()]

    assert len(msgs) == 2  # empty text entry is skipped
    assert msgs[0].author == "alice"
    assert msgs[1].author == "carol"


def test_youtube_live_url_builds_from_channel():
    assert YouTubeProvider("somechannel").live_url == "https://www.youtube.com/@somechannel/live"


def test_youtube_live_url_strips_leading_at():
    assert YouTubeProvider("@somechannel").live_url == "https://www.youtube.com/@somechannel/live"


# --- integration test: requires env var ---

@pytest.mark.skipif(
    not os.getenv("YOUTUBE_CHANNEL"),
    reason="YOUTUBE_CHANNEL not set",
)
async def test_youtube_integration():
    provider = YouTubeProvider.from_env()
    received = []
    async for msg in provider.messages():
        received.append(msg)
        if len(received) >= 1:
            break
    assert len(received) >= 1
    assert received[0].platform == "youtube"
