import asyncio
import os

import httpx

from termchat.domain.message import EmojiRun, TextRun
from termchat.ui.emoji_render import (
    EmojiImageCache,
    detect_image_protocol,
    render_run,
)


# --- detect_image_protocol ---

def test_detect_kitty_via_window_id():
    assert detect_image_protocol({"KITTY_WINDOW_ID": "1"}) == "kitty"


def test_detect_kitty_via_term():
    assert detect_image_protocol({"TERM": "xterm-kitty"}) == "kitty"


def test_detect_iterm2():
    assert detect_image_protocol({"TERM_PROGRAM": "iTerm.app"}) == "iterm2"


def test_detect_wezterm_uses_iterm2_protocol():
    assert detect_image_protocol({"TERM_PROGRAM": "WezTerm"}) == "iterm2"


def test_detect_none_for_plain_xterm():
    assert detect_image_protocol({"TERM": "xterm-256color"}) == "none"


# --- render_run ---

def test_render_text_run_passes_through():
    assert render_run(TextRun(text="hello"), "kitty", None) == "hello"


def test_render_emoji_falls_back_when_protocol_none():
    run = EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=False)
    assert render_run(run, "none", None) == ":smile:"


def test_render_emoji_falls_back_when_no_cache():
    run = EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=False)
    assert render_run(run, "kitty", None) == ":smile:"


def test_render_emoji_falls_back_when_image_url_missing():
    run = EmojiRun(shortcut=":smile:", image_url=None, is_custom=False)
    cache = EmojiImageCache(client=httpx.AsyncClient())
    assert render_run(run, "kitty", cache) == ":smile:"


async def test_render_emoji_kitty_escape_when_cached():
    cache = EmojiImageCache(client=httpx.AsyncClient())
    cache._data["https://x/a.png"] = b"PNGBYTES"
    run = EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=False)
    out = render_run(run, "kitty", cache)
    assert out.startswith("\x1b_G")
    assert out.endswith("\x1b\\")
    assert "UE5HQllURVM=" in out  # base64 of PNGBYTES
    await cache.aclose()


def test_kitty_escape_chunks_large_payload():
    from termchat.ui.emoji_render import _KITTY_CHUNK, _kitty_escape

    # 4 bytes → 8 base64 chars — fits in one chunk
    small = _kitty_escape(b"X" * 3)
    assert small.count("\x1b_G") == 1
    assert "m=1" not in small

    # enough bytes to force >1 chunk (each chunk ≤ _KITTY_CHUNK base64 chars)
    big_data = b"X" * ((_KITTY_CHUNK * 3 // 4) + 1)
    out = _kitty_escape(big_data)
    assert "\x1b_Gf=100" in out          # first chunk has metadata
    assert "m=1;" in out                  # at least one intermediate chunk
    assert out.endswith("\x1b\\")


def test_kitty_escape_suppresses_response_acks_with_q2():
    """Without q=2 Kitty replies to each graphics command via stdin, and the
    shell echoes the response bytes back to the screen as visible garbage."""
    from termchat.ui.emoji_render import _kitty_escape

    assert "q=2" in _kitty_escape(b"PNG")


async def test_render_emoji_iterm2_escape_when_cached():
    cache = EmojiImageCache(client=httpx.AsyncClient())
    cache._data["https://x/a.png"] = b"PNGBYTES"
    run = EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=False)
    out = render_run(run, "iterm2", cache)
    assert out.startswith("\x1b]1337;File=")
    assert out.endswith("\x07")
    assert "UE5HQllURVM=" in out
    await cache.aclose()


# --- EmojiImageCache ---

class _StubTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.gate = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls[url] = self.calls.get(url, 0) + 1
        await self.gate.wait()
        return httpx.Response(200, content=f"bytes-for-{url}".encode())


async def test_cache_first_get_returns_none_and_warms():
    transport = _StubTransport()
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client)

    url = "https://x/a.png"
    assert cache.get(url) is None
    assert url in cache._in_flight

    transport.gate.set()
    await cache._in_flight[url]

    assert cache.get(url) == b"bytes-for-https://x/a.png"
    await cache.aclose()


async def test_cache_dedupes_concurrent_fetches():
    transport = _StubTransport()
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client)

    url = "https://x/a.png"
    cache.get(url)
    cache.get(url)
    cache.get(url)
    assert len(cache._in_flight) == 1

    transport.gate.set()
    await cache._in_flight[url]
    assert transport.calls[url] == 1
    await cache.aclose()


async def test_cache_evicts_when_over_capacity():
    transport = _StubTransport()
    transport.gate.set()
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(capacity=2, client=client)

    for i in range(3):
        url = f"https://x/{i}.png"
        cache.get(url)
        await cache._in_flight[url]

    assert "https://x/0.png" not in cache._data
    assert "https://x/1.png" in cache._data
    assert "https://x/2.png" in cache._data
    await cache.aclose()


async def test_cache_fetch_failure_does_not_crash():
    class _BoomTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

    client = httpx.AsyncClient(transport=_BoomTransport())
    cache = EmojiImageCache(client=client)
    url = "https://x/a.png"
    cache.get(url)
    await asyncio.sleep(0)
    assert url not in cache._in_flight
    assert url not in cache._data
    await cache.aclose()


# --- Pillow conversion ---

def _make_gif_bytes() -> bytes:
    """Return raw bytes of a 1x1 GIF, encoded by Pillow."""
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    buf = BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


class _BytesTransport(httpx.AsyncBaseTransport):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=self._payload)


async def test_cache_converts_gif_to_png():
    gif = _make_gif_bytes()
    assert gif[:3] == b"GIF"
    client = httpx.AsyncClient(transport=_BytesTransport(gif))
    cache = EmojiImageCache(client=client)
    url = "https://x/a.gif"
    cache.get(url)
    await cache._in_flight[url]
    stored = cache.get(url)
    assert stored is not None
    assert stored.startswith(b"\x89PNG")
    await cache.aclose()


async def test_cache_undecodable_bytes_fall_through_unchanged():
    payload = b"not a real image"
    client = httpx.AsyncClient(transport=_BytesTransport(payload))
    cache = EmojiImageCache(client=client)
    url = "https://x/bad"
    cache.get(url)
    await cache._in_flight[url]
    assert cache.get(url) == payload
    await cache.aclose()


# --- prefetch ---

async def test_prefetch_blocks_until_all_urls_cached():
    transport = _StubTransport()
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client)

    urls = ["https://x/a.png", "https://x/b.png", "https://x/c.png"]
    # transport.gate is unset → fetches block until we open it
    pending = asyncio.create_task(cache.prefetch(urls))
    await asyncio.sleep(0)  # let prefetch schedule the fetches
    assert not pending.done()
    assert len(cache._in_flight) == 3

    transport.gate.set()
    await pending
    # After prefetch returns, every URL must be in the cache
    for url in urls:
        assert cache.get(url) is not None
    await cache.aclose()


async def test_prefetch_skips_already_cached_urls():
    transport = _StubTransport()
    transport.gate.set()
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client)

    # Warm one URL
    cache._data["https://x/already.png"] = b"cached"
    # Mix cached + new
    await cache.prefetch(["https://x/already.png", "https://x/new.png"])
    # Only the uncached URL hit the network
    assert "https://x/new.png" in transport.calls
    assert "https://x/already.png" not in transport.calls
    await cache.aclose()


async def test_prefetch_shares_in_flight_task_with_concurrent_get():
    transport = _StubTransport()
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client)

    url = "https://x/a.png"
    # Concurrent: a normal get() and a prefetch() both want the same URL
    cache.get(url)
    pending = asyncio.create_task(cache.prefetch([url]))
    await asyncio.sleep(0)
    # The fetch is deduplicated to a single in-flight task
    assert len(cache._in_flight) == 1

    transport.gate.set()
    await pending
    assert transport.calls[url] == 1
    await cache.aclose()


async def test_prefetch_returns_even_when_fetch_fails():
    class _BoomTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

    client = httpx.AsyncClient(transport=_BoomTransport())
    cache = EmojiImageCache(client=client)
    # Should not raise — failures drop silently and the caller falls back.
    await cache.prefetch(["https://x/dead.png"])
    assert cache.get("https://x/dead.png") is None
    await cache.aclose()


# --- disk cache ---

class _CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, content=self._payload)


async def test_disk_cache_writes_png_on_first_fetch(tmp_path):
    payload = _make_gif_bytes()
    transport = _CountingTransport(payload)
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client, cache_dir=tmp_path)

    url = "https://x/a.gif"
    cache.get(url)
    await cache._in_flight[url]

    # Exactly one file written, name = sha1(url), contents = PNG-normalized
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    import hashlib
    assert files[0].name == hashlib.sha1(url.encode()).hexdigest()
    assert files[0].read_bytes().startswith(b"\x89PNG")
    await cache.aclose()


async def test_disk_cache_hit_avoids_network(tmp_path):
    """Pre-populate the disk cache, then assert the network is never touched."""
    import hashlib

    url = "https://x/already.png"
    path = tmp_path / hashlib.sha1(url.encode()).hexdigest()
    path.write_bytes(b"\x89PNG-from-disk")

    transport = _CountingTransport(b"should-not-be-called")
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client, cache_dir=tmp_path)

    cache.get(url)
    await cache._in_flight[url]

    assert transport.calls == 0
    assert cache.get(url) == b"\x89PNG-from-disk"
    await cache.aclose()


async def test_disk_cache_expired_entry_refetched_from_network(tmp_path):
    """An on-disk file older than TTL should be treated as a miss."""
    import hashlib
    import time as time_mod

    url = "https://x/stale.png"
    path = tmp_path / hashlib.sha1(url.encode()).hexdigest()
    path.write_bytes(b"old-bytes")
    # Backdate mtime well past TTL (use a small TTL so we don't need to wait)
    old = time_mod.time() - 10_000
    os.utime(path, (old, old))

    payload = _make_gif_bytes()
    transport = _CountingTransport(payload)
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client, cache_dir=tmp_path, ttl_seconds=60)

    cache.get(url)
    await cache._in_flight[url]

    assert transport.calls == 1                       # forced network refresh
    assert cache.get(url).startswith(b"\x89PNG")      # fresh PNG cached
    assert path.read_bytes().startswith(b"\x89PNG")   # disk file was replaced
    await cache.aclose()


async def test_disk_cache_disabled_when_cache_dir_is_none(tmp_path):
    """cache_dir=None must not touch the disk."""
    payload = _make_gif_bytes()
    transport = _CountingTransport(payload)
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client, cache_dir=None)

    cache.get("https://x/a.gif")
    await cache._in_flight["https://x/a.gif"]

    # No files created in tmp_path (we never told the cache about it)
    assert list(tmp_path.iterdir()) == []
    await cache.aclose()


async def test_disk_cache_mkdir_failure_degrades_to_memory_only(tmp_path):
    """If the cache dir can't be created (perm denied / readonly), don't crash."""
    # Point cache_dir at a path whose parent is a file → mkdir fails
    blocker = tmp_path / "blocker"
    blocker.write_text("im a file not a dir")
    bad_dir = blocker / "emotes"  # mkdir parents=True would fail

    payload = _make_gif_bytes()
    transport = _CountingTransport(payload)
    client = httpx.AsyncClient(transport=transport)
    cache = EmojiImageCache(client=client, cache_dir=bad_dir)
    # Construction should not raise; cache_dir silently disabled
    assert cache._cache_dir is None

    cache.get("https://x/a.gif")
    await cache._in_flight["https://x/a.gif"]
    assert cache.get("https://x/a.gif").startswith(b"\x89PNG")
    await cache.aclose()


def test_default_disk_cache_dir_honors_xdg(monkeypatch, tmp_path):
    from termchat.ui.emoji_render import default_disk_cache_dir

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_disk_cache_dir() == tmp_path / "termchat" / "emotes"

    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    path = default_disk_cache_dir()
    assert path.parts[-3:] == (".cache", "termchat", "emotes")
