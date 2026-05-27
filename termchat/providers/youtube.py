import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator

from termchat.domain.message import EmojiRun, Message, MessageRun, TextRun


def _largest_image_url(images: list[dict[str, Any]] | None) -> str | None:
    if not images:
        return None
    sized = [img for img in images if img.get("url")]
    if not sized:
        return None
    sized.sort(key=lambda img: (img.get("width") or 0) * (img.get("height") or 0), reverse=True)
    return sized[0]["url"]


def _tokenize(message: str, emotes: list[dict[str, Any]]) -> tuple[MessageRun, ...]:
    if not emotes:
        return (TextRun(text=message),) if message else ()

    by_name: dict[str, dict[str, Any]] = {}
    for emote in emotes:
        name = emote.get("name")
        if isinstance(name, str) and name:
            by_name[name] = emote

    if not by_name:
        return (TextRun(text=message),) if message else ()

    names_sorted = sorted(by_name.keys(), key=len, reverse=True)
    pattern = re.compile("(" + "|".join(re.escape(n) for n in names_sorted) + ")")

    runs: list[MessageRun] = []
    for token in pattern.split(message):
        if not token:
            continue
        emote = by_name.get(token)
        if emote is None:
            runs.append(TextRun(text=token))
        else:
            runs.append(
                EmojiRun(
                    shortcut=token,
                    image_url=_largest_image_url(emote.get("images")),
                    is_custom=bool(emote.get("is_custom_emoji", False)),
                )
            )
    return tuple(runs)


def _map_entry(entry: dict[str, Any]) -> Message | None:
    text = entry.get("message") or ""
    if not text:
        return None

    author: Any = entry.get("author") or ""
    if isinstance(author, dict):
        author = author.get("name") or "unknown"

    ts_usec = entry.get("timestamp")
    ts = (
        datetime.fromtimestamp(ts_usec / 1_000_000, tz=timezone.utc)
        if ts_usec
        else datetime.now(timezone.utc)
    )

    emotes = entry.get("emotes") or []
    runs = _tokenize(str(text), emotes)

    return Message(
        id=entry.get("message_id") or str(uuid.uuid4()),
        author=str(author) or "unknown",
        text=str(text),
        timestamp=ts,
        platform="youtube",
        runs=runs,
    )


def _next_or_none(it: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(it)
    except StopIteration:
        return None


class YouTubeProvider:
    def __init__(self, channel: str) -> None:
        self._channel = channel.lstrip("@")

    @classmethod
    def from_env(cls) -> "YouTubeProvider":
        return cls(os.environ["YOUTUBE_CHANNEL"])

    @property
    def live_url(self) -> str:
        return f"https://www.youtube.com/@{self._channel}/live"

    async def messages(self) -> AsyncIterator[Message]:
        loop = asyncio.get_running_loop()
        chat = await loop.run_in_executor(None, self._open_chat)
        while True:
            entry = await loop.run_in_executor(None, _next_or_none, chat)
            if entry is None:
                return
            msg = _map_entry(entry)
            if msg:
                yield msg

    def _open_chat(self) -> Iterator[dict[str, Any]]:
        from chat_downloader import ChatDownloader  # local import keeps it mockable

        return iter(ChatDownloader().get_chat(self.live_url))
