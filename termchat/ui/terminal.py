import asyncio
import sys

from termchat.domain.message import EmojiRun, Message
from termchat.ui.emoji_render import (
    EmojiImageCache,
    default_disk_cache_dir,
    detect_image_protocol,
    render_run,
)


class TerminalUI:
    def __init__(self, queue: asyncio.Queue[Message]) -> None:
        self._queue = queue
        # detect_image_protocol checks env vars for Kitty / iTerm2 / WezTerm.
        # In an unsupported terminal `_protocol == "none"` and runs collapse
        # back to their `:shortcut:` text — same output as before.
        self._protocol = detect_image_protocol()
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
                sys.stdout.write(f"[{msg.platform}] {msg.author}: {body}\n")
                sys.stdout.flush()
                self._queue.task_done()
        finally:
            if self._cache is not None:
                await self._cache.aclose()

    async def _prefetch_emote_images(self, msg: Message) -> None:
        if not msg.runs or self._cache is None:
            return
        urls = {
            run.image_url
            for run in msg.runs
            if isinstance(run, EmojiRun) and run.image_url
        }
        if urls:
            await self._cache.prefetch(urls)

    def _render_body(self, msg: Message) -> str:
        if not msg.runs or self._cache is None:
            return msg.text
        return "".join(render_run(r, self._protocol, self._cache) for r in msg.runs)
