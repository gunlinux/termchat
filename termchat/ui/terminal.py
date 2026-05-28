import asyncio
import sys

from termchat.domain.message import EmojiRun, Message
from termchat.infra.emote_cache import EmojiImageCache, default_disk_cache_dir
from termchat.ui._theme import PLATFORM_ICONS
from termchat.ui.emoji_render import (
    Protocol,
    detect_image_protocol,
    render_run,
)

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


class TerminalUI:
    def __init__(self, queue: asyncio.Queue[Message]) -> None:
        self._queue = queue
        # detect_image_protocol checks env vars for Kitty / iTerm2 / WezTerm.
        # In an unsupported terminal `_protocol == "none"` and runs collapse
        # back to their `:shortcut:` text — same output as before.
        self._protocol: Protocol = detect_image_protocol()
        self._cache: EmojiImageCache | None = (
            EmojiImageCache(cache_dir=default_disk_cache_dir())
            if self._protocol != "none"
            else None
        )

    async def run(self) -> None:
        try:
            while True:
                msg = await self._queue.get()
                # Block on the fetch so the message lands with images already
                # cached — avoids the "first occurrence is :shortcode:" flash.
                await self._prefetch_emote_images(msg)
                body = self._render_body(msg)
                icon = PLATFORM_ICONS.get(msg.platform, f"[{msg.platform}]")
                p_ansi = _PLATFORM_ANSI.get(msg.platform, "")
                a_ansi = _AUTHOR_ANSI.get(msg.platform, "")
                sys.stdout.write(f"\n{p_ansi}{icon}{_RESET} {a_ansi}{msg.author}{_RESET}: {body}")
                sys.stdout.flush()
                self._queue.task_done()
        finally:
            if self._cache is not None:
                await self._cache.aclose()

    async def _prefetch_emote_images(self, msg: Message) -> None:
        if not msg.runs or self._cache is None:
            return
        urls = {run.image_url for run in msg.runs if isinstance(run, EmojiRun) and run.image_url}
        if urls:
            await self._cache.prefetch(urls)

    def _render_body(self, msg: Message) -> str:
        if not msg.runs or self._cache is None:
            return msg.text
        return "".join(render_run(r, self._protocol, self._cache) for r in msg.runs)
