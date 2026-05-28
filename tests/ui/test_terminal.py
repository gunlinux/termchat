import asyncio
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from termchat.domain.message import EmojiRun, Message, TextRun
from termchat.ui.terminal import TerminalUI


def _msg(i: int, platform: str = "twitch") -> Message:
    return Message(
        id=str(i),
        author="streamer",
        text=f"hello {i}",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform=platform,
    )


async def _drain(ui: TerminalUI, queue: asyncio.Queue[Message]) -> None:
    task = asyncio.create_task(ui.run())
    await queue.join()
    task.cancel()


async def test_terminal_ui_formats_message():
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(_msg(0))
    ui = TerminalUI(queue)

    output = StringIO()
    with patch("sys.stdout", output):
        await _drain(ui, queue)

    out = output.getvalue()
    assert "streamer\x1b[0m: hello 0" in out


async def test_terminal_ui_multiple_messages():
    queue: asyncio.Queue[Message] = asyncio.Queue()
    for i in range(3):
        await queue.put(_msg(i))

    output = StringIO()
    with patch("sys.stdout", output):
        ui = TerminalUI(queue)
        await _drain(ui, queue)

    lines = output.getvalue().strip().splitlines()
    assert len(lines) == 3
    assert "streamer\x1b[0m: hello 0" in lines[0]
    assert "streamer\x1b[0m: hello 2" in lines[2]


async def test_terminal_ui_falls_back_to_shortcuts_when_no_image_protocol():
    """Unsupported terminal (no Kitty/iTerm2/WezTerm env) → :shortcut: text."""
    msg = Message(
        id="1",
        author="alice",
        text="hi :smile:",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform="twitch",
        runs=(
            TextRun(text="hi "),
            EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=True),
        ),
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True), patch(
        "sys.stdout", output
    ):
        ui = TerminalUI(queue)
        await _drain(ui, queue)

    assert "alice\x1b[0m: hi :smile:" in output.getvalue()


async def test_terminal_ui_emits_kitty_escape_when_cache_warm():
    """In Kitty, a warmed cache entry should render as an inline image escape."""
    msg = Message(
        id="1",
        author="alice",
        text="hi :smile:",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform="twitch",
        runs=(
            TextRun(text="hi "),
            EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=True),
        ),
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with patch.dict("os.environ", {"TERM": "xterm-kitty"}, clear=True), patch(
        "sys.stdout", output
    ):
        ui = TerminalUI(queue)
        # Pre-warm cache so the first render emits the image escape directly
        assert ui._cache is not None
        ui._cache._data["https://x/a.png"] = b"PNGBYTES"
        await _drain(ui, queue)
        await ui._cache.aclose()

    rendered = output.getvalue()
    assert "alice\x1b[0m: hi " in rendered
    assert "\x1b_G" in rendered  # Kitty graphics escape made it to stdout


async def test_terminal_ui_waits_for_emote_fetch_before_printing():
    """Message must not appear on stdout until every emote URL is cached.

    Drives a Twitch message whose only emote isn't cached. While the HTTP
    fetch is blocked behind a gate, stdout must stay empty — confirming the
    UI awaited the prefetch instead of rendering with a `:shortcode:` flash.
    """
    import httpx

    from termchat.ui.emoji_render import EmojiImageCache

    class _GatedTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.gate = asyncio.Event()
            self.calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.calls += 1
            await self.gate.wait()
            return httpx.Response(200, content=b"PNGBYTES")

    transport = _GatedTransport()
    cache = EmojiImageCache(client=httpx.AsyncClient(transport=transport))

    msg = Message(
        id="1",
        author="alice",
        text="hi :smile:",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform="twitch",
        runs=(
            TextRun(text="hi "),
            EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=True),
        ),
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with patch.dict("os.environ", {"TERM": "xterm-kitty"}, clear=True), patch(
        "sys.stdout", output
    ):
        ui = TerminalUI(queue)
        ui._cache = cache  # inject our gated cache

        task = asyncio.create_task(ui.run())
        # Give the loop a few ticks to dequeue + kick off the fetch
        for _ in range(5):
            await asyncio.sleep(0)
        # Fetch is blocked → nothing printed yet
        assert transport.calls == 1
        assert output.getvalue() == ""

        # Release the gate; UI should now finish rendering
        transport.gate.set()
        await queue.join()
        task.cancel()
        await cache.aclose()

    rendered = output.getvalue()
    assert "alice\x1b[0m: hi " in rendered
    assert "\x1b_G" in rendered  # image escape, not shortcode
