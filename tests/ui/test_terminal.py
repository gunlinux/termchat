import asyncio
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

from termchat.domain.message import EmojiRun, Message, TextRun
from termchat.ui.terminal import _HIDE_CURSOR, TerminalUI


def _make_ui() -> TerminalUI:
    return TerminalUI(asyncio.Queue())


def _msg(i: int, platform: str = "twitch") -> Message:
    return Message(
        id=str(i),
        author="streamer",
        text=f"hello {i}",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
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

    # The leading hide-cursor escape is written once before any message; the
    # three messages each occupy their own line.
    lines = [line for line in output.getvalue().splitlines() if "streamer" in line]
    assert len(lines) == 3
    assert "streamer\x1b[0m: hello 0" in lines[0]
    assert "streamer\x1b[0m: hello 2" in lines[2]


async def test_terminal_ui_highlights_mention_in_blue():
    """A reply starting with @handle gets the blue mention ANSI color."""
    from termchat.ui.terminal import _MENTION_ANSI

    msg = Message(
        id="1",
        author="bob",
        text="@alice nice play",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        platform="twitch",
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True),
        patch("sys.stdout", output),
    ):
        ui = TerminalUI(queue)
        await _drain(ui, queue)

    out = output.getvalue()
    assert f"{_MENTION_ANSI}@alice\x1b[0m nice play" in out


async def test_terminal_ui_does_not_highlight_non_mention_at():
    """An @ embedded mid-word (e.g. an email) is left untouched."""
    from termchat.ui.terminal import _MENTION_ANSI

    msg = Message(
        id="1",
        author="bob",
        text="mail me at a@b.com",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        platform="twitch",
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True),
        patch("sys.stdout", output),
    ):
        ui = TerminalUI(queue)
        await _drain(ui, queue)

    out = output.getvalue()
    assert _MENTION_ANSI not in out
    assert "a@b.com" in out


async def test_terminal_ui_falls_back_to_shortcuts_when_no_image_protocol():
    """Unsupported terminal (no Kitty/iTerm2/WezTerm env) → :shortcut: text."""
    msg = Message(
        id="1",
        author="alice",
        text="hi :smile:",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        platform="twitch",
        runs=(
            TextRun(text="hi "),
            EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=True),
        ),
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True),
        patch("sys.stdout", output),
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
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        platform="twitch",
        runs=(
            TextRun(text="hi "),
            EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=True),
        ),
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-kitty"}, clear=True),
        patch("sys.stdout", output),
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


async def test_terminal_ui_youtube_icon_renders_as_image_in_kitty():
    """In Kitty, the YouTube icon is a bundled PNG image escape, not the glyph."""
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(_msg(0, platform="youtube"))

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-kitty"}, clear=True),
        patch("sys.stdout", output),
    ):
        ui = TerminalUI(queue)
        assert ui._youtube_icon is not None  # asset loaded + escape precomputed
        await _drain(ui, queue)
        if ui._cache is not None:
            await ui._cache.aclose()

    rendered = output.getvalue()
    assert "\x1b_G" in rendered  # Kitty graphics escape for the icon
    assert "" not in rendered  # the missing-glyph fallback was NOT used
    assert "streamer\x1b[0m: hello 0" in rendered


async def test_terminal_ui_twitch_keeps_glyph_in_kitty():
    """Twitch still uses its (working) Nerd Font glyph, not an image."""
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(_msg(0, platform="twitch"))

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-kitty"}, clear=True),
        patch("sys.stdout", output),
    ):
        ui = TerminalUI(queue)
        await _drain(ui, queue)
        if ui._cache is not None:
            await ui._cache.aclose()

    rendered = output.getvalue()
    assert "" in rendered  # Twitch glyph present
    assert "\x1b_G" not in rendered  # no inline image for the icon


async def test_terminal_ui_youtube_icon_falls_back_to_glyph_without_protocol():
    """Unsupported terminal → no asset loaded, YouTube glyph used."""
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(_msg(0, platform="youtube"))

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True),
        patch("sys.stdout", output),
    ):
        ui = TerminalUI(queue)
        assert ui._youtube_icon is None
        await _drain(ui, queue)

    rendered = output.getvalue()
    assert "" in rendered  # glyph fallback
    assert "\x1b_G" not in rendered


async def test_terminal_ui_waits_for_emote_fetch_before_printing():
    """Message must not appear on stdout until every emote URL is cached.

    Drives a Twitch message whose only emote isn't cached. While the HTTP
    fetch is blocked behind a gate, stdout must stay empty — confirming the
    UI awaited the prefetch instead of rendering with a `:shortcode:` flash.
    """
    import httpx

    from termchat.infra.emote_cache import EmojiImageCache

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
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        platform="twitch",
        runs=(
            TextRun(text="hi "),
            EmojiRun(shortcut=":smile:", image_url="https://x/a.png", is_custom=True),
        ),
    )
    queue: asyncio.Queue[Message] = asyncio.Queue()
    await queue.put(msg)

    output = StringIO()
    with (
        patch.dict("os.environ", {"TERM": "xterm-kitty"}, clear=True),
        patch("sys.stdout", output),
    ):
        ui = TerminalUI(queue)
        ui._cache = cache  # inject our gated cache

        task = asyncio.create_task(ui.run())
        # Give the loop a few ticks to dequeue + kick off the fetch
        for _ in range(5):
            await asyncio.sleep(0)
        # Fetch is blocked → no message printed yet (only the hide-cursor escape)
        assert transport.calls == 1
        assert output.getvalue() == _HIDE_CURSOR

        # Release the gate; UI should now finish rendering
        transport.gate.set()
        await queue.join()
        task.cancel()
        await cache.aclose()

    rendered = output.getvalue()
    assert "alice\x1b[0m: hi " in rendered
    assert "\x1b_G" in rendered  # image escape, not shortcode


# ── _wrap_body unit tests ──────────────────────────────────────────────────────


def test_wrap_body_plain_text():
    ui = _make_ui()
    body = "word1 word2 word3 word4 word5"
    wrapped = ui._wrap_body(body, indent_width=0, available_width=14)
    lines = wrapped.split("\n")
    # "word1 word2" fits (11 ≤ 14), "word3" triggers wrap with no indent;
    # continuation line uses full terminal width so all remaining words fit on one line.
    assert lines[0] == "word1 word2"
    assert lines[1] == "word3 word4 word5"


def test_wrap_body_continuation_uses_full_available_width():
    """After wrapping, the continuation line body uses the full available_width."""
    ui = _make_ui()
    # available_width=10, indent_width=0 (no gap on left)
    # "longword"=8 fits; space+"aaa"=12>10 → wrap; on continuation "aaa bbb"=7 ≤ 10 fits
    body = "longword aaa bbb"
    wrapped = ui._wrap_body(body, indent_width=0, available_width=10)
    lines = wrapped.split("\n")
    assert lines[0] == "longword"
    assert lines[1] == "aaa bbb"


def test_wrap_body_single_chunk_kitty_counted_as_2_cells():
    """A single-chunk Kitty escape counts as exactly 2 display cells."""
    ui = _make_ui()
    img = "\x1b_Gf=100,a=T,t=d,c=2,r=1,q=2;AAAA\x1b\\"
    # 5 emojis + 4 spaces = 14 cells; with available_width=20 they all fit
    body = " ".join([img] * 5)
    wrapped = ui._wrap_body(body, indent_width=5, available_width=20)
    assert "\n" not in wrapped


def test_wrap_body_multi_chunk_kitty_counted_as_2_cells():
    """Kitty continuation chunks (m=1/m=0) count as 0 cells — only the transmit chunk is 2."""
    ui = _make_ui()
    multi = "\x1b_Gf=100,a=T,t=d,c=2,r=1,q=2,m=1;AAAA\x1b\\\x1b_Gm=1;BBBB\x1b\\\x1b_Gm=0;CCCC\x1b\\"
    # 5 multi-chunk emojis + 4 spaces = 14 cells; should all fit in available_width=20
    body = " ".join([multi] * 5)
    wrapped = ui._wrap_body(body, indent_width=5, available_width=20)
    assert "\n" not in wrapped
    # Continuation chunks must stay glued to their transmit chunk (no newline between them)
    assert "\x1b_Gm=1;BBBB\x1b\\\n" not in wrapped
