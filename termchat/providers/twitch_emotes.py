"""Twitch chat emote registry — fetches and indexes BTTV and 7TV emotes.

The registry does not handle Twitch's native emotes; those carry positional
data in the IRC `emotes=` tag and are merged in by `twitch.py` separately.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from termchat.domain.message import EmojiRun, MessageRun, TextRun

logger = logging.getLogger(__name__)

Source = Literal["bttv-global", "bttv-channel", "7tv-channel", "twitch-channel"]

_PRIORITY: dict[Source, int] = {
    "bttv-global": 1,
    "7tv-channel": 2,
    "bttv-channel": 3,
    "twitch-channel": 4,
}

# Public web client ID, sent by the Twitch website on its own GQL calls.
# It is the only way to reach `localEmoteSets` (follower-only / native channel
# emotes) without an OAuth token — Helix requires auth for the equivalent.
_TWITCH_GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
_TWITCH_GQL_QUERY = (
    "query($id: ID!) { user(id: $id) {"
    " subscriptionProducts { emotes { id token } }"
    " channel { localEmoteSets { emotes { id token } } }"
    " } }"
)

_WS_SPLIT = re.compile(r"(\s+)")


@dataclass(frozen=True)
class EmoteInfo:
    name: str
    image_url: str
    source: Source


class TwitchEmoteRegistry:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._emotes: dict[str, EmoteInfo] = {}
        self._priorities: dict[str, int] = {}

    def _add(self, info: EmoteInfo) -> None:
        prio = _PRIORITY[info.source]
        existing = self._priorities.get(info.name, 0)
        if prio >= existing:
            self._emotes[info.name] = info
            self._priorities[info.name] = prio

    def lookup(self, name: str) -> EmoteInfo | None:
        return self._emotes.get(name)

    def tokenize(self, text: str) -> tuple[MessageRun, ...]:
        if not text:
            return ()
        parts = _WS_SPLIT.split(text)
        runs: list[MessageRun] = []
        buf = ""
        for part in parts:
            info = self._emotes.get(part) if part else None
            if info is not None:
                if buf:
                    runs.append(TextRun(text=buf))
                    buf = ""
                runs.append(EmojiRun(shortcut=info.name, image_url=info.image_url, is_custom=True))
            else:
                buf += part
        if buf:
            runs.append(TextRun(text=buf))
        return tuple(runs)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def load_global(self) -> None:
        client = await self._get_client()
        try:
            resp = await client.get("https://api.betterttv.net/3/cached/emotes/global")
            resp.raise_for_status()
            for entry in resp.json() or []:
                self._ingest_bttv(entry, source="bttv-global")
        except Exception as e:  # best-effort: missing emotes just fall back to text
            logger.debug("BTTV global emote load failed: %s", e)

    async def load_channel(self, room_id: str) -> None:
        await asyncio.gather(
            self._load_bttv_channel(room_id),
            self._load_7tv_channel(room_id),
            self._load_twitch_channel(room_id),
        )

    async def _load_twitch_channel(self, room_id: str) -> None:
        client = await self._get_client()
        try:
            resp = await client.post(
                _TWITCH_GQL_URL,
                json={"query": _TWITCH_GQL_QUERY, "variables": {"id": room_id}},
                headers={"Client-Id": _TWITCH_GQL_CLIENT_ID},
            )
            resp.raise_for_status()
            user = (resp.json().get("data") or {}).get("user") or {}
            entries: list[dict[str, Any]] = []
            for product in user.get("subscriptionProducts") or []:
                entries.extend(product.get("emotes") or [])
            channel = user.get("channel") or {}
            for emote_set in channel.get("localEmoteSets") or []:
                entries.extend(emote_set.get("emotes") or [])
            for entry in entries:
                name = entry.get("token")
                eid = entry.get("id")
                if not name or not eid:
                    continue
                self._add(
                    EmoteInfo(
                        name=name,
                        image_url=(
                            f"https://static-cdn.jtvnw.net/emoticons/v2/{eid}/static/dark/2.0"
                        ),
                        source="twitch-channel",
                    )
                )
        except Exception as e:  # best-effort: missing emotes just fall back to text
            logger.debug("Twitch channel emote load failed for room %s: %s", room_id, e)

    async def _load_bttv_channel(self, room_id: str) -> None:
        client = await self._get_client()
        url = f"https://api.betterttv.net/3/cached/users/twitch/{room_id}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json() or {}
            for entry in (data.get("channelEmotes") or []) + (data.get("sharedEmotes") or []):
                self._ingest_bttv(entry, source="bttv-channel")
        except Exception as e:  # best-effort: missing emotes just fall back to text
            logger.debug("BTTV channel emote load failed for room %s: %s", room_id, e)

    async def _load_7tv_channel(self, room_id: str) -> None:
        client = await self._get_client()
        url = f"https://7tv.io/v3/users/twitch/{room_id}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json() or {}
            emote_set = data.get("emote_set") or {}
            for entry in emote_set.get("emotes") or []:
                name = entry.get("name")
                eid = entry.get("id")
                if not name or not eid:
                    continue
                # 7TV's CDN serves webp/avif/gif but not png; Pillow normalizes
                # whatever bytes we get to a static-PNG first frame downstream.
                self._add(
                    EmoteInfo(
                        name=name,
                        image_url=f"https://cdn.7tv.app/emote/{eid}/2x.webp",
                        source="7tv-channel",
                    )
                )
        except Exception as e:  # best-effort: missing emotes just fall back to text
            logger.debug("7TV channel emote load failed for room %s: %s", room_id, e)

    def _ingest_bttv(self, entry: dict[str, Any], source: Source) -> None:
        code = entry.get("code")
        eid = entry.get("id")
        if not code or not eid:
            return
        self._add(
            EmoteInfo(
                name=code,
                image_url=f"https://cdn.betterttv.net/emote/{eid}/2x",
                source=source,
            )
        )

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
