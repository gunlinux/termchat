import asyncio
import os
import random
import re
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from termchat.domain.message import EmojiRun, Message, MessageRun, TextRun
from termchat.providers.twitch_emotes import TwitchEmoteRegistry

_PRIVMSG_RE = re.compile(
    r"^(?:@(?P<tags>[^ ]*) )?:(?P<nick>[^!]+)![^ ]+ PRIVMSG #[^ ]+ :(?P<text>.+)$"
)
_ROOMSTATE_RE = re.compile(
    r"^@(?P<tags>[^ ]*) :tmi\.twitch\.tv ROOMSTATE #[^ ]+\s*$"
)
_TAG_ESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}
_HOST = "irc.chat.twitch.tv"
_PORT = 6667
# Twitch server PINGs every ~5 minutes. If we go this long without ANY traffic
# (PING, chat, or otherwise) the connection is almost certainly dead — NAT
# rebinding, route flap, dropped wifi — and TCP won't tell us for many minutes.
_READ_TIMEOUT = 360.0
# Reconnect backoff: 1s → 2s → 4s → … → 60s cap. Reset to 1s on the first
# successful message of a fresh session.
_RECONNECT_BACKOFF_INITIAL = 1.0
_RECONNECT_BACKOFF_MAX = 60.0


def _unescape_tag_value(v: str) -> str:
    if "\\" not in v:
        return v
    out: list[str] = []
    i = 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            out.append(_TAG_ESCAPES.get(v[i + 1], v[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_tags(tagstr: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    if not tagstr:
        return tags
    for kv in tagstr.split(";"):
        if not kv:
            continue
        k, _, v = kv.partition("=")
        tags[k] = _unescape_tag_value(v)
    return tags


def parse_emotes_tag(value: str) -> list[tuple[str, int, int]]:
    """Returns list of (emote_id, start, end) intervals — end inclusive."""
    if not value:
        return []
    out: list[tuple[str, int, int]] = []
    for entry in value.split("/"):
        eid, _, positions = entry.partition(":")
        if not eid or not positions:
            continue
        for span in positions.split(","):
            a, _, b = span.partition("-")
            try:
                out.append((eid, int(a), int(b)))
            except ValueError:
                continue
    return out


def _native_emote_url(emote_id: str) -> str:
    return f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/static/dark/2.0"


def build_runs(
    text: str,
    native: list[tuple[str, int, int]],
    registry: TwitchEmoteRegistry | None,
) -> tuple[MessageRun, ...]:
    intervals = sorted(native, key=lambda t: t[1])
    runs: list[MessageRun] = []
    cursor = 0
    for eid, start, end in intervals:
        if start < cursor or start > len(text) or end >= len(text):
            continue
        if start > cursor:
            gap = text[cursor:start]
            if registry is not None:
                runs.extend(registry.tokenize(gap))
            else:
                runs.append(TextRun(text=gap))
        runs.append(
            EmojiRun(
                shortcut=text[start : end + 1],
                image_url=_native_emote_url(eid),
                is_custom=True,
            )
        )
        cursor = end + 1
    if cursor < len(text):
        tail = text[cursor:]
        if registry is not None:
            runs.extend(registry.tokenize(tail))
        else:
            runs.append(TextRun(text=tail))
    return tuple(runs)


def parse_privmsg(
    line: str, registry: TwitchEmoteRegistry | None = None
) -> Message | None:
    m = _PRIVMSG_RE.match(line)
    if not m:
        return None
    tags = parse_tags(m.group("tags") or "")
    text = m.group("text").rstrip("\r\n")
    native = parse_emotes_tag(tags.get("emotes", ""))
    runs = build_runs(text, native, registry)
    return Message(
        id=tags.get("id") or str(uuid.uuid4()),
        author=tags.get("display-name") or m.group("nick"),
        text=text,
        timestamp=datetime.now(timezone.utc),
        platform="twitch",
        runs=runs,
    )


def parse_roomstate(line: str) -> str | None:
    m = _ROOMSTATE_RE.match(line)
    if not m:
        return None
    return parse_tags(m.group("tags") or "").get("room-id") or None


class TwitchProvider:
    def __init__(self, channel: str, oauth_token: str = "") -> None:
        self._channel = channel.lstrip("#")
        self._oauth = oauth_token

    @classmethod
    def from_env(cls) -> "TwitchProvider":
        channel = os.environ["TWITCH_CHANNEL"]
        oauth = os.environ.get("TWITCH_OAUTH", "")
        return cls(channel, oauth)

    async def messages(self) -> AsyncIterator[Message]:
        """Yield messages forever, reconnecting on disconnect or stale link.

        Each call to `_read_session()` is one TCP connection's lifetime — it
        ends on graceful server close, network error, or read timeout (no
        traffic in `_READ_TIMEOUT` seconds). Any of those falls through to
        the reconnect loop with exponential backoff.

        Backoff resets to the initial value as soon as a fresh session
        delivers its first message — so brief drops don't ratchet the wait up
        forever, but a server-side ban-loop will throttle politely.
        """
        backoff = _RECONNECT_BACKOFF_INITIAL
        while True:
            try:
                async for msg in self._read_session():
                    backoff = _RECONNECT_BACKOFF_INITIAL
                    yield msg
                # Generator returned cleanly → server closed; reconnect.
            except asyncio.CancelledError:
                raise
            except Exception:
                # Network failure, TimeoutError, ConnectionResetError,
                # OSError from open_connection — all retryable.
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)

    async def _read_session(self) -> AsyncIterator[Message]:
        """One IRC connection's lifetime. Returns when the connection drops."""
        reader, writer = await asyncio.open_connection(_HOST, _PORT)
        registry = TwitchEmoteRegistry()
        global_task: asyncio.Task[None] = asyncio.create_task(registry.load_global())
        channel_task: asyncio.Task[None] | None = None
        try:
            if self._oauth:
                nick = self._channel
                password = f"oauth:{self._oauth}"
            else:
                nick = f"justinfan{random.randint(10000, 99999)}"
                password = "SCHMOOZE"
            writer.write(f"PASS {password}\r\n".encode())
            writer.write(f"NICK {nick}\r\n".encode())
            writer.write(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
            writer.write(f"JOIN #{self._channel}\r\n".encode())
            await writer.drain()

            while True:
                # wait_for raises TimeoutError if the connection goes silent;
                # the outer reconnect loop catches that and re-establishes.
                raw = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT)
                if not raw:
                    return  # peer closed the connection cleanly
                line = raw.decode(errors="replace")

                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue

                if channel_task is None:
                    room_id = parse_roomstate(line)
                    if room_id:
                        channel_task = asyncio.create_task(
                            registry.load_channel(room_id)
                        )
                        continue

                msg = parse_privmsg(line, registry)
                if msg:
                    yield msg
        finally:
            global_task.cancel()
            if channel_task is not None:
                channel_task.cancel()
            await registry.aclose()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                # Already-broken socket may raise on close; we're tearing
                # down anyway, the reconnect loop will open a fresh one.
                pass
