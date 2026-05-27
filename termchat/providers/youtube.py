import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from termchat.domain.message import Message


def _map_entry(entry: dict[str, Any]) -> Message | None:
    text = entry.get("text") or ""
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

    return Message(
        id=entry.get("id") or str(uuid.uuid4()),
        author=str(author) or "unknown",
        text=str(text),
        timestamp=ts,
        platform="youtube",
    )


class YouTubeProvider:
    def __init__(self, url: str) -> None:
        self._url = url

    @classmethod
    def from_env(cls) -> "YouTubeProvider":
        return cls(os.environ["YOUTUBE_URL"])

    async def messages(self) -> AsyncIterator[Message]:
        loop = asyncio.get_running_loop()
        entries = await loop.run_in_executor(None, self._extract_comments)
        for entry in entries:
            msg = _map_entry(entry)
            if msg:
                yield msg

    def _extract_comments(self) -> list[dict[str, Any]]:
        from yt_dlp import YoutubeDL  # local import keeps it mockable

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "getcomments": True,
            "skip_download": True,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(self._url, download=False) or {}
        return info.get("comments") or []
