import os
from datetime import timezone
from unittest.mock import patch

import pytest

from termchat.domain.message import EmojiRun, TextRun
from termchat.providers.youtube import YouTubeProvider, _map_entry, _tokenize


# --- unit tests: entry mapping ---

def test_map_entry_basic():
    entry = {
        "message_id": "abc",
        "author": {"name": "viewer1"},
        "message": "Nice stream!",
        "timestamp": 1_700_000_000_000_000,
    }
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.id == "abc"
    assert msg.author == "viewer1"
    assert msg.text == "Nice stream!"
    assert msg.platform == "youtube"
    assert msg.timestamp.tzinfo is not None
    assert msg.runs == (TextRun(text="Nice stream!"),)


def test_map_entry_author_as_string():
    entry = {"author": "viewer2", "message": "Hello"}
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.author == "viewer2"


def test_map_entry_no_text_returns_none():
    entry = {"author": "user", "message": ""}
    assert _map_entry(entry) is None


def test_map_entry_missing_id_generates_uuid():
    entry = {"author": "a", "message": "hi"}
    msg = _map_entry(entry)
    assert msg is not None
    assert len(msg.id) > 0


def test_map_entry_no_timestamp_uses_now():
    entry = {"author": "a", "message": "hi"}
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.timestamp.tzinfo == timezone.utc


def test_map_entry_with_mixed_emotes():
    entry = {
        "message_id": "m1",
        "author": {"name": "alice"},
        "message": "hi :smile: friends :_custom_:!",
        "timestamp": 1_700_000_000_000_000,
        "emotes": [
            {
                "id": "U+1F600",
                "name": ":smile:",
                "shortcuts": [":smile:"],
                "images": [{"url": "https://yt/smile.png", "width": 24, "height": 24}],
                "is_custom_emoji": False,
            },
            {
                "id": "UCabc/custom1",
                "name": ":_custom_:",
                "shortcuts": [":_custom_:"],
                "images": [
                    {"url": "https://yt/custom_24.png", "width": 24, "height": 24},
                    {"url": "https://yt/custom_48.png", "width": 48, "height": 48},
                ],
                "is_custom_emoji": True,
            },
        ],
    }
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.runs == (
        TextRun(text="hi "),
        EmojiRun(shortcut=":smile:", image_url="https://yt/smile.png", is_custom=False),
        TextRun(text=" friends "),
        EmojiRun(shortcut=":_custom_:", image_url="https://yt/custom_48.png", is_custom=True),
        TextRun(text="!"),
    )


# --- _tokenize ---

def test_tokenize_without_emotes():
    assert _tokenize("plain text", []) == (TextRun(text="plain text"),)


def test_tokenize_emote_at_start():
    emotes = [
        {"name": ":wave:", "images": [{"url": "u", "width": 1, "height": 1}], "is_custom_emoji": False}
    ]
    runs = _tokenize(":wave: hello", emotes)
    assert runs == (
        EmojiRun(shortcut=":wave:", image_url="u", is_custom=False),
        TextRun(text=" hello"),
    )


def test_tokenize_emote_at_end():
    emotes = [
        {"name": ":wave:", "images": [{"url": "u", "width": 1, "height": 1}], "is_custom_emoji": False}
    ]
    runs = _tokenize("hi :wave:", emotes)
    assert runs == (
        TextRun(text="hi "),
        EmojiRun(shortcut=":wave:", image_url="u", is_custom=False),
    )


def test_tokenize_overlapping_names_picks_longest():
    # If two emote names overlap by prefix, the longer one wins
    emotes = [
        {"name": ":x:", "images": [{"url": "a", "width": 1, "height": 1}]},
        {"name": ":xyz:", "images": [{"url": "b", "width": 1, "height": 1}]},
    ]
    runs = _tokenize(":xyz: foo :x: bar", emotes)
    assert runs == (
        EmojiRun(shortcut=":xyz:", image_url="b", is_custom=False),
        TextRun(text=" foo "),
        EmojiRun(shortcut=":x:", image_url="a", is_custom=False),
        TextRun(text=" bar"),
    )


def test_tokenize_emote_without_images():
    emotes = [{"name": ":nopic:"}]
    runs = _tokenize(":nopic:", emotes)
    assert runs == (EmojiRun(shortcut=":nopic:", image_url=None, is_custom=False),)


# --- provider iteration ---

async def test_youtube_provider_maps_entries():
    entries = [
        {
            "message_id": "1",
            "author": {"name": "alice"},
            "message": "Hey",
            "timestamp": 1_700_000_000_000_000,
        },
        {"message_id": "2", "author": {"name": "bob"}, "message": "", "timestamp": None},
        {"message_id": "3", "author": {"name": "carol"}, "message": "Hi", "timestamp": None},
    ]
    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_open_chat", return_value=iter(entries)):
        msgs = [m async for m in provider.messages()]

    assert len(msgs) == 2  # empty text entry is skipped
    assert msgs[0].author == "alice"
    assert msgs[1].author == "carol"


async def test_youtube_provider_exits_when_chat_ends():
    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_open_chat", return_value=iter([])):
        msgs = [m async for m in provider.messages()]
    assert msgs == []


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
