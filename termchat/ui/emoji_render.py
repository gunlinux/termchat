"""Inline emoji rendering for the Textual UI.

Detects whether the host terminal supports inline images (Kitty graphics
protocol or iTerm2 inline images) and renders `EmojiRun` either as a raw
image escape sequence or as the `:shortcut:` fallback text.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Iterable, Literal

import httpx

from termchat.domain.message import EmojiRun, MessageRun, TextRun

Protocol = Literal["kitty", "iterm2", "none"]


def detect_image_protocol(
    env: os._Environ[str] | dict[str, str] | None = None,
) -> Protocol:
    env = os.environ if env is None else env
    if env.get("KITTY_WINDOW_ID") or env.get("TERM") == "xterm-kitty":
        return "kitty"
    term_program = env.get("TERM_PROGRAM", "")
    if term_program in ("iTerm.app", "WezTerm"):
        return "iterm2"
    return "none"


_DEFAULT_TTL_SECONDS = 30 * 86400  # 30 days


def default_disk_cache_dir() -> Path:
    """Per-user emote cache location, following XDG basedir conventions."""
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "termchat" / "emotes"
    return Path.home() / ".cache" / "termchat" / "emotes"


class EmojiImageCache:
    """Async two-tier cache for emoji image bytes.

    Tier 1: in-memory LRU (`_data`), bounded by `capacity`.
    Tier 2: on-disk PNG cache at `cache_dir`, keyed by sha1(url), with an
    mtime-based TTL so eventually-changed emotes still refresh.

    First call to `get` for an unseen URL returns `None` and schedules a
    background fetch. The fetch checks disk first; on disk miss/expiry it
    pulls from the network and writes the PNG to disk for next session.
    Subsequent in-memory calls return the cached bytes immediately. Concurrent
    calls for the same URL deduplicate to a single fetch.

    Pass `cache_dir=None` (the default) to disable the disk tier entirely —
    useful for tests so they don't pollute the user's cache directory.
    """

    def __init__(
        self,
        capacity: int = 256,
        client: httpx.AsyncClient | None = None,
        cache_dir: Path | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._capacity = capacity
        self._data: OrderedDict[str, bytes] = OrderedDict()
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._client = client
        self._owns_client = client is None
        self._cache_dir = cache_dir
        self._ttl_seconds = ttl_seconds
        if cache_dir is not None:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Read-only filesystem, permission denied — silently degrade
                # to in-memory only rather than crashing the UI.
                self._cache_dir = None

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
            # Disk tier: cheap hit avoids a network round-trip every restart.
            if self._cache_dir is not None:
                cached = await asyncio.to_thread(self._read_disk, url)
                if cached is not None:
                    self._store(url, cached)
                    return
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=10.0)
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = _to_png_first_frame(resp.content)
            self._store(url, data)
            if self._cache_dir is not None:
                await asyncio.to_thread(self._write_disk, url, data)
        except Exception:
            pass
        finally:
            self._in_flight.pop(url, None)

    def _store(self, url: str, data: bytes) -> None:
        self._data[url] = data
        self._data.move_to_end(url)
        while len(self._data) > self._capacity:
            self._data.popitem(last=False)

    def _disk_path(self, url: str) -> Path:
        assert self._cache_dir is not None
        return self._cache_dir / hashlib.sha1(url.encode("utf-8")).hexdigest()

    def _read_disk(self, url: str) -> bytes | None:
        try:
            path = self._disk_path(url)
            stat = path.stat()
        except OSError:
            return None
        if time.time() - stat.st_mtime > self._ttl_seconds:
            return None  # entry expired — treat as miss, let network refresh
        try:
            return path.read_bytes()
        except OSError:
            return None

    def _write_disk(self, url: str, data: bytes) -> None:
        path = self._disk_path(url)
        # tempfile + rename gives us an atomic replace; concurrent readers
        # never observe a half-written file.
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def prefetch(self, urls: Iterable[str]) -> None:
        """Block until every URL is either cached or its fetch has failed.

        Schedules a fetch for any URL not already in the cache (sharing the
        task with concurrent callers via `_in_flight`), then awaits all
        relevant tasks. Failed fetches drop silently — `get(url)` will still
        return None after this returns, and the caller falls back to the
        shortcode just as it would have without prefetching.
        """
        tasks: list[asyncio.Task[None]] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen or url in self._data:
                continue
            seen.add(url)
            self.get(url)  # schedules the fetch if not already running
            task = self._in_flight.get(url)
            if task is not None:
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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


_KITTY_CHUNK = 4096


def _kitty_escape(data: bytes) -> str:
    # q=2 suppresses Kitty's per-command response acks. Without it Kitty replies
    # via the TTY input channel and the shell echoes the response bytes back to
    # the screen as visible garbage.
    payload = base64.standard_b64encode(data).decode("ascii")
    chunks = [
        payload[i : i + _KITTY_CHUNK] for i in range(0, len(payload), _KITTY_CHUNK)
    ]
    if not chunks:
        chunks = [""]
    if len(chunks) == 1:
        return f"\x1b_Gf=100,a=T,t=d,c=2,r=1,q=2;{chunks[0]}\x1b\\"
    parts = [f"\x1b_Gf=100,a=T,t=d,c=2,r=1,q=2,m=1;{chunks[0]}\x1b\\"]
    for chunk in chunks[1:-1]:
        parts.append(f"\x1b_Gm=1;{chunk}\x1b\\")
    parts.append(f"\x1b_Gm=0;{chunks[-1]}\x1b\\")
    return "".join(parts)


def _iterm2_escape(data: bytes) -> str:
    payload = base64.standard_b64encode(data).decode("ascii")
    return (
        f"\x1b]1337;File=inline=1;width=2;height=1;preserveAspectRatio=1:{payload}\x07"
    )
