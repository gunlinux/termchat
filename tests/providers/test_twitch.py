import os
import pytest

from termchat.providers.twitch import parse_privmsg, TwitchProvider


# --- unit tests: IRC line parsing (no network) ---

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
