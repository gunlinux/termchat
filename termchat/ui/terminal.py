import asyncio
import logging
import re
import shutil
import signal
import sys

from termchat.domain.message import EmojiRun, Message
from termchat.infra.emote_cache import EmojiImageCache, default_disk_cache_dir
from termchat.ui._theme import PLATFORM_ICONS
from termchat.ui.emoji_render import (
    Protocol,
    detect_image_protocol,
    render_run,
)

logger = logging.getLogger(__name__)

# ANSI color applied to the platform icon
_PLATFORM_ANSI: dict[str, str] = {
    "twitch": "\x1b[38;2;145;70;255m",
    "youtube": "\x1b[38;2;255;0;0m",
    "fake": "\x1b[38;2;100;220;100m",
    "system": "\x1b[38;2;255;165;0m",
}

# ANSI color applied to the author/nickname
_AUTHOR_ANSI: dict[str, str] = {
    "twitch": "\x1b[38;2;176;79;221m",
    "youtube": "\x1b[38;2;255;105;180m",
    "fake": "\x1b[38;2;100;220;100m",
    "system": "\x1b[38;2;255;165;0m",
}

_RESET = "\x1b[0m"

# DECTCEM hide/show cursor — the blinking cursor parked after the last message
# is distracting, so hide it while running and restore it on exit.
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"

# Blue applied to @mentions (replies/pings) anywhere in the message body.
_MENTION_ANSI = "\x1b[38;2;88;166;255m"
# Match an @handle at the start of the body or after whitespace, so a reply
# that "starts with @" — and any mid-message ping — is highlighted, while
# embedded forms like emails (a@b) are left alone.
_MENTION_RE = re.compile(r"(?:^|(?<=\s))(@\w+)")

# Same blue applied to the *entire* body when the message is directed at the
# channel owner (i.e. the first @handle matches a watched channel name).
_CHANNEL_REPLY_ANSI = _MENTION_ANSI
_CHANNEL_REPLY_START_RE = re.compile(r"^@(\w+)")

# Tokenises a rendered body string into display-width segments.
# img: Kitty transmit (a=T in header) or iTerm2 → 2 display cells.
# img_cont: Kitty continuation chunk (m=0/m=1, no a=T) → 0 cells, output immediately.
# ansi/esc: zero-width escape sequences.  space: 1 cell.  word: len(text) cells.
_TOKEN_RE = re.compile(
    r"(?P<img>\x1b_G[^;]*a=T[^;]*;[^\x1b]*\x1b\\)"
    r"|(?P<img_cont>\x1b_G[^;]*;[^\x1b]*\x1b\\)"
    r"|(?P<iterm2>\x1b\]1337;[^\x07\x1b]*(?:\x07|\x1b\\))"
    r"|(?P<ansi>\x1b\[[0-9;]*[a-zA-Z])"
    r"|(?P<esc>\x1b.)"
    r"|(?P<space> )"
    r"|(?P<word>[^ \x1b]+)",
    re.DOTALL,
)


def _highlight_mentions(text: str) -> str:
    return _MENTION_RE.sub(lambda m: f"{_MENTION_ANSI}{m.group(1)}{_RESET}", text)


def _is_channel_reply(text: str, channel_names: set[str]) -> bool:
    """Return True if text starts with @<watched-channel-name>."""
    m = _CHANNEL_REPLY_START_RE.match(text)
    return m is not None and m.group(1).lower() in channel_names


class TerminalUI:
    def __init__(
        self, queue: asyncio.Queue[Message], channel_names: set[str] | None = None
    ) -> None:
        self._queue = queue
        # Lowercase, @-stripped names of watched channels; used to detect replies to the streamer.
        self._channel_names: set[str] = {n.lower().lstrip("@") for n in (channel_names or set())}
        # detect_image_protocol checks env vars for Kitty / iTerm2 / WezTerm.
        # In an unsupported terminal `_protocol == "none"` and runs collapse
        # back to their `:shortcut:` text — same output as before.
        self._protocol: Protocol = detect_image_protocol()
        self._cache: EmojiImageCache | None = (
            EmojiImageCache(cache_dir=default_disk_cache_dir())
            if self._protocol != "none"
            else None
        )
        # Cached terminal width; updated on SIGWINCH, not per-message.
        self._terminal_cols: int = shutil.get_terminal_size(fallback=(80, 24)).columns

    def _on_sigwinch(self, _signum: int, _frame: object) -> None:
        self._terminal_cols = shutil.get_terminal_size(fallback=(80, 24)).columns

    async def run(self) -> None:
        sys.stdout.write(_HIDE_CURSOR)
        sys.stdout.flush()
        _old_sigwinch = None
        if hasattr(signal, "SIGWINCH"):
            _old_sigwinch = signal.signal(signal.SIGWINCH, self._on_sigwinch)
        try:
            while True:
                msg = await self._queue.get()
                # Block on the fetch so the message lands with images already
                # cached — avoids the "first occurrence is :shortcode:" flash.
                await self._prefetch_emote_images(msg)
                body = self._render_body(msg)
                if self._channel_names and _is_channel_reply(msg.text, self._channel_names):
                    # Whole body blue; re-apply after any embedded RESET so inner
                    # mention highlights don't terminate the channel-reply color.
                    body = (
                        _CHANNEL_REPLY_ANSI
                        + body.replace(_RESET, _RESET + _CHANNEL_REPLY_ANSI)
                        + _RESET
                    )
                a_ansi = _AUTHOR_ANSI.get(msg.platform, "")
                icon = PLATFORM_ICONS.get(msg.platform, f"[{msg.platform}]")
                p_ansi = _PLATFORM_ANSI.get(msg.platform, "")
                icon_field = f"{p_ansi}{icon}{_RESET}"
                # First line body width: terminal minus the icon+space+author+": " prefix.
                prefix_width = len(msg.author) + 5
                available = self._terminal_cols - prefix_width
                body = self._wrap_body(body, 0, available)
                sys.stdout.write(f"\n{icon_field} {a_ansi}{msg.author}{_RESET}: {body}")
                sys.stdout.flush()
                self._queue.task_done()
        finally:
            if hasattr(signal, "SIGWINCH") and _old_sigwinch is not None:
                signal.signal(signal.SIGWINCH, _old_sigwinch)
            sys.stdout.write(_SHOW_CURSOR)
            sys.stdout.flush()
            if self._cache is not None:
                await self._cache.aclose()

    async def _prefetch_emote_images(self, msg: Message) -> None:
        if not msg.runs or self._cache is None:
            return
        urls = {run.image_url for run in msg.runs if isinstance(run, EmojiRun) and run.image_url}
        if urls:
            await self._cache.prefetch(urls)

    def _wrap_body(self, body: str, indent_width: int, available_width: int) -> str:
        """Word-wrap body to available_width, indenting continuation lines."""
        if available_width <= 4:
            return body

        indent = " " * indent_width
        continuation_width = self._terminal_cols - indent_width
        result: list[str] = []
        col = 0
        pending_space = 0
        pending_ansi: list[str] = []
        line_width = available_width

        for m in _TOKEN_RE.finditer(body):
            kind = m.lastgroup
            text = m.group(0)

            if kind in ("ansi", "esc"):
                pending_ansi.append(text)
                continue

            # Kitty continuation chunks must appear immediately after the transmit
            # chunk — they form a single image together and cannot be separated.
            if kind == "img_cont":
                result.append(text)
                continue

            if kind == "space":
                result.extend(pending_ansi)
                pending_ansi = []
                pending_space += 1
                continue

            w = 2 if kind in ("img", "iterm2") else len(text)

            if col == 0:
                pending_space = 0
                result.extend(pending_ansi)
                pending_ansi = []
            elif col + pending_space + w > line_width:
                result.extend(pending_ansi)
                pending_ansi = []
                result.append(f"\n{indent}")
                col = 0
                pending_space = 0
                line_width = continuation_width
            else:
                result.append(" " * pending_space)
                col += pending_space
                result.extend(pending_ansi)
                pending_ansi = []
                pending_space = 0

            result.append(text)
            col += w

        result.extend(pending_ansi)
        return "".join(result)

    def _render_body(self, msg: Message) -> str:
        # @mentions never collide with the base64 image escapes (their alphabet
        # and headers contain no '@'), so highlighting the whole body is safe.
        if not msg.runs or self._cache is None:
            return _highlight_mentions(msg.text)
        body = "".join(render_run(r, self._protocol, self._cache) for r in msg.runs)
        return _highlight_mentions(body)
