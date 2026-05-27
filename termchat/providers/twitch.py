import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from termchat.domain.message import Message

_PRIVMSG_RE = re.compile(
    r"^:(?P<nick>[^!]+)![^ ]+ PRIVMSG #[^ ]+ :(?P<text>.+)$"
)
_HOST = "irc.chat.twitch.tv"
_PORT = 6667


def parse_privmsg(line: str, channel: str) -> Message | None:
    m = _PRIVMSG_RE.match(line)
    if not m:
        return None
    return Message(
        id=str(uuid.uuid4()),
        author=m.group("nick"),
        text=m.group("text").rstrip("\r\n"),
        timestamp=datetime.now(timezone.utc),
        platform="twitch",
    )


class TwitchProvider:
    def __init__(self, channel: str, oauth_token: str) -> None:
        self._channel = channel.lstrip("#")
        self._oauth = oauth_token

    @classmethod
    def from_env(cls) -> "TwitchProvider":
        channel = os.environ["TWITCH_CHANNEL"]
        oauth = os.environ["TWITCH_OAUTH"]
        return cls(channel, oauth)

    async def messages(self) -> AsyncIterator[Message]:
        reader, writer = await asyncio.open_connection(_HOST, _PORT)
        try:
            writer.write(f"PASS oauth:{self._oauth}\r\n".encode())
            writer.write(f"NICK {self._channel}\r\n".encode())
            writer.write(f"JOIN #{self._channel}\r\n".encode())
            await writer.drain()

            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace")

                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue

                msg = parse_privmsg(line, self._channel)
                if msg:
                    yield msg
        finally:
            writer.close()
            await writer.wait_closed()
