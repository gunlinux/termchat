import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Footer, RichLog

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.ui.emoji_render import EmojiImageCache, detect_image_protocol, render_run

_PLATFORM_COLORS: dict[str, str] = {
    "twitch": "medium_purple",
    "youtube": "red",
    "fake": "green",
    "system": "dark_orange",
}
_AUTHOR_WIDTH = 20


class TermchatApp(App[None]):
    CSS = """
    RichLog {
        height: 1fr;
        border: none;
    }
    """

    def __init__(self, bus: MessageBus, queue: asyncio.Queue[Message]) -> None:
        super().__init__()
        self._bus = bus
        self._queue = queue
        self._protocol = detect_image_protocol()
        self._emoji_cache = EmojiImageCache() if self._protocol != "none" else None

    def compose(self) -> ComposeResult:
        yield RichLog(highlight=True, markup=True, wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        self._bus_task = asyncio.create_task(self._bus.run())
        self.set_interval(0.1, self._drain_queue)

    async def _drain_queue(self) -> None:
        log = self.query_one(RichLog)
        while not self._queue.empty():
            msg = self._queue.get_nowait()
            self._queue.task_done()
            color = _PLATFORM_COLORS.get(msg.platform, "white")
            platform_tag = f"[bold {color}][{msg.platform}][/bold {color}]"
            author = msg.author.ljust(_AUTHOR_WIDTH)[:_AUTHOR_WIDTH]
            body = self._render_body(msg)
            log.write(f"{platform_tag} [cyan]{author}[/cyan] {body}")

    def _render_body(self, msg: Message) -> str:
        if not msg.runs or self._emoji_cache is None:
            return msg.text
        return "".join(render_run(r, self._protocol, self._emoji_cache) for r in msg.runs)

    async def on_unmount(self) -> None:
        self._bus_task.cancel()
        if self._emoji_cache is not None:
            await self._emoji_cache.aclose()
