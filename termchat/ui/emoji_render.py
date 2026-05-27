"""Inline emoji rendering for the Textual UI.

Detects whether the host terminal supports inline images (Kitty graphics
protocol or iTerm2 inline images) and renders `EmojiRun` either as a raw
image escape sequence or as the `:shortcut:` fallback text.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections import OrderedDict
from io import BytesIO
from typing import Literal

import httpx

from termchat.domain.message import EmojiRun, MessageRun, TextRun

Protocol = Literal["kitty", "iterm2", "none"]


def detect_image_protocol(env: os._Environ[str] | dict[str, str] | None = None) -> Protocol:
    env = os.environ if env is None else env
    if env.get("KITTY_WINDOW_ID") or env.get("TERM") == "xterm-kitty":
        return "kitty"
    term_program = env.get("TERM_PROGRAM", "")
    if term_program in ("iTerm.app", "WezTerm"):
        return "iterm2"
    return "none"


class EmojiImageCache:
    """Async LRU cache for emoji image bytes.

    First call to `get` for a URL returns `None` and schedules a background
    fetch. Subsequent calls return the cached bytes once the fetch completes.
    Concurrent calls for the same URL deduplicate to a single fetch.
    """

    def __init__(self, capacity: int = 256, client: httpx.AsyncClient | None = None) -> None:
        self._capacity = capacity
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._client = client
        self._owns_client = client is None

    def get(self, url: str) -> bytes | None:
        if url in self._data:
            self._data.move_to_end(url)
            return self._data[url]
        if url not in self._in_flight:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # No running event loop — caller is using cache synchronously;
                # fall back to shortcode rendering this frame.
                return None
            self._in_flight[url] = asyncio.create_task(self._fetch(url))
        return None

    async def _fetch(self, url: str) -> None:
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=10.0)
            resp = await self._client.get(url)
            resp.raise_for_status()
            self._data[url] = _to_png_first_frame(resp.content)
            self._data.move_to_end(url)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)
        except Exception:
            pass
        finally:
            self._in_flight.pop(url, None)

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()


def render_run(
    run: MessageRun,
    protocol: Protocol,
    cache: EmojiImageCache | None,
) -> str:
    if isinstance(run, TextRun):
        return run.text
    if not isinstance(run, EmojiRun):
        return ""
    if protocol == "none" or run.image_url is None or cache is None:
        return run.shortcut
    data = cache.get(run.image_url)
    if data is None:
        return run.shortcut
    if protocol == "kitty":
        return _kitty_escape(data)
    return _iterm2_escape(data)


def _to_png_first_frame(data: bytes) -> bytes:
    """Decode any image (PNG/GIF/WebP/AVIF) and re-encode the first frame as PNG.

    Kitty's `f=100` and iTerm2 inline images both render PNG reliably; animated
    or non-PNG inputs are normalized here so the downstream renderer is uniform.
    Falls through to the original bytes on any decode failure.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(data))
        img.seek(0)
        buf = BytesIO()
        img.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return data


def _kitty_escape(data: bytes) -> str:
    payload = base64.standard_b64encode(data).decode("ascii")
    return f"\x1b_Gf=100,a=T,t=d,c=2,r=1;{payload}\x1b\\"


def _iterm2_escape(data: bytes) -> str:
    payload = base64.standard_b64encode(data).decode("ascii")
    return (
        "\x1b]1337;File=inline=1;width=2;height=1;preserveAspectRatio=1:"
        f"{payload}\x07"
    )
