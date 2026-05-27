import os
import pytest

from termchat.domain.message import EmojiRun, TextRun
from termchat.providers.twitch import (
    TwitchProvider,
    _unescape_tag_value,
    build_runs,
    parse_emotes_tag,
    parse_privmsg,
    parse_roomstate,
    parse_tags,
)
from termchat.providers.twitch_emotes import EmoteInfo, TwitchEmoteRegistry


# --- bare PRIVMSG (backward compat) ---

def test_parse_privmsg_basic():
    line = ":johndoe!johndoe@johndoe.tmi.twitch.tv PRIVMSG #channel :Hello world"
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.author == "johndoe"
    assert msg.text == "Hello world"
    assert msg.platform == "twitch"


def test_parse_privmsg_with_crlf():
    line = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #stream :PogChamp\r\n"
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.text == "PogChamp"


def test_parse_privmsg_non_privmsg_returns_none():
    line = ":tmi.twitch.tv 001 justinfan1234 :Welcome, GLHF!"
    assert parse_privmsg(line) is None


def test_parse_privmsg_ping_returns_none():
    line = "PING :tmi.twitch.tv"
    assert parse_privmsg(line) is None


def test_parse_privmsg_colon_in_text():
    line = ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chat :http://example.com rocks"
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.text == "http://example.com rocks"


def test_parse_privmsg_has_unique_ids():
    line = ":user!user@user.tmi.twitch.tv PRIVMSG #ch :hi"
    m1 = parse_privmsg(line)
    m2 = parse_privmsg(line)
    assert m1 is not None and m2 is not None
    assert m1.id != m2.id


# --- IRCv3 tag parsing ---

def test_parse_tags_simple():
    assert parse_tags("color=#FF0000;display-name=Alice") == {
        "color": "#FF0000",
        "display-name": "Alice",
    }


def test_parse_tags_empty_value():
    assert parse_tags("badge-info=;badges=") == {"badge-info": "", "badges": ""}


def test_parse_tags_empty_string():
    assert parse_tags("") == {}


def test_unescape_tag_value_full_set():
    assert _unescape_tag_value(r"hello\sworld") == "hello world"
    assert _unescape_tag_value(r"a\:b") == "a;b"
    assert _unescape_tag_value(r"a\\b") == "a\\b"
    assert _unescape_tag_value(r"line\r\n") == "line\r\n"


def test_unescape_tag_value_unknown_escape_keeps_char():
    assert _unescape_tag_value(r"\x") == "x"


# --- emotes= tag parsing ---

def test_parse_emotes_tag_single():
    assert parse_emotes_tag("25:0-4") == [("25", 0, 4)]


def test_parse_emotes_tag_multi_position():
    assert parse_emotes_tag("25:0-4,12-16") == [("25", 0, 4), ("25", 12, 16)]


def test_parse_emotes_tag_multi_emote():
    assert parse_emotes_tag("25:0-4,12-16/1902:6-10") == [
        ("25", 0, 4),
        ("25", 12, 16),
        ("1902", 6, 10),
    ]


def test_parse_emotes_tag_empty():
    assert parse_emotes_tag("") == []


def test_parse_emotes_tag_malformed_skipped():
    assert parse_emotes_tag("25:bogus,3-7") == [("25", 3, 7)]


# --- parse_roomstate ---

def test_parse_roomstate_returns_room_id():
    line = "@emote-only=0;room-id=12345;slow=0 :tmi.twitch.tv ROOMSTATE #channel"
    assert parse_roomstate(line) == "12345"


def test_parse_roomstate_returns_none_for_non_roomstate():
    assert parse_roomstate("PING :tmi.twitch.tv") is None


# --- runs construction ---

def test_build_runs_native_only():
    text = "Kappa hi Kappa"
    runs = build_runs(text, [("25", 0, 4), ("25", 9, 13)], registry=None)
    assert runs == (
        EmojiRun(
            shortcut="Kappa",
            image_url="https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/2.0",
            is_custom=True,
        ),
        TextRun(text=" hi "),
        EmojiRun(
            shortcut="Kappa",
            image_url="https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/2.0",
            is_custom=True,
        ),
    )


def test_build_runs_native_with_text_around():
    text = "say Kappa now"
    runs = build_runs(text, [("25", 4, 8)], registry=None)
    assert runs == (
        TextRun(text="say "),
        EmojiRun(
            shortcut="Kappa",
            image_url="https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/2.0",
            is_custom=True,
        ),
        TextRun(text=" now"),
    )


def test_build_runs_only_text_no_registry():
    runs = build_runs("hello world", [], registry=None)
    assert runs == (TextRun(text="hello world"),)


def test_build_runs_empty_text():
    assert build_runs("", [], registry=None) == ()


def test_build_runs_text_with_3p_emote_via_registry():
    registry = TwitchEmoteRegistry()
    registry._add(
        EmoteInfo(
            name="monkaW",
            image_url="https://cdn.betterttv.net/emote/abc/2x",
            source="bttv-channel",
        )
    )
    runs = build_runs("oh monkaW", [], registry)
    assert runs == (
        TextRun(text="oh "),
        EmojiRun(
            shortcut="monkaW",
            image_url="https://cdn.betterttv.net/emote/abc/2x",
            is_custom=True,
        ),
    )


def test_build_runs_native_wins_over_3p_collision():
    # Registry has "Kappa" as a BTTV emote; native tag also says position 0-4 is Kappa.
    # The native emote should be emitted, not the 3p one.
    registry = TwitchEmoteRegistry()
    registry._add(
        EmoteInfo(
            name="Kappa",
            image_url="https://cdn.betterttv.net/emote/SHOULD-NOT-APPEAR/2x",
            source="bttv-channel",
        )
    )
    runs = build_runs("Kappa", [("25", 0, 4)], registry)
    assert runs == (
        EmojiRun(
            shortcut="Kappa",
            image_url="https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/2.0",
            is_custom=True,
        ),
    )


# --- tagged PRIVMSG end-to-end ---

def test_parse_privmsg_with_tags_uses_display_name():
    line = (
        "@badge-info=;color=#FF0000;display-name=Alice;emotes=;id=abc-1;room-id=99 "
        ":alice!alice@alice.tmi.twitch.tv PRIVMSG #ch :Hi"
    )
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.author == "Alice"
    assert msg.id == "abc-1"
    assert msg.text == "Hi"


def test_parse_privmsg_with_emotes_produces_runs():
    line = (
        "@emotes=25:0-4;display-name=bob "
        ":bob!bob@bob.tmi.twitch.tv PRIVMSG #ch :Kappa hello"
    )
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.runs == (
        EmojiRun(
            shortcut="Kappa",
            image_url="https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/2.0",
            is_custom=True,
        ),
        TextRun(text=" hello"),
    )


# --- integration test: requires env vars ---

@pytest.mark.skipif(
    not (os.getenv("TWITCH_CHANNEL") and os.getenv("TWITCH_OAUTH")),
    reason="TWITCH_CHANNEL and TWITCH_OAUTH not set",
)
async def test_twitch_integration():
    provider = TwitchProvider.from_env()
    received = []
    async for msg in provider.messages():
        received.append(msg)
        if len(received) >= 1:
            break
    assert len(received) >= 1
    assert received[0].platform == "twitch"
