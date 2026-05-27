import asyncio

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
