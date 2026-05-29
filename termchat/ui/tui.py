import asyncio
import re

from rich.markup import escape as markup_escape
from textual.app import App, ComposeResult
from textual.widgets import Footer, RichLog

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.infra.emote_cache import EmojiImageCache
from termchat.ui._theme import PLATFORM_ICONS
from termchat.ui.emoji_render import (
    Protocol,
    detect_image_protocol,
    render_run,
)

_PLATFORM_COLORS: dict[str, str] = {
    "twitch": "rgb(145,70,255)",
    "youtube": "red",
    "fake": "green",
    "system": "dark_orange",
}

_AUTHOR_COLORS: dict[str, str] = {
    "twitch": "#b04fdd",  # rgb(176, 79, 221)
    "youtube": "#ff69b4",  # pink
    "fake": "green",
    "system": "dark_orange",
}

_AUTHOR_WIDTH = 20

# Match an @handle at the start of the body or after whitespace so a reply that
# "starts with @" — and any mid-message ping — is highlighted in blue. Applied
# after markup_escape, so the inserted [blue] tags are the only live markup.
_MENTION_RE = re.compile(r"(?:^|(?<=\s))(@\w+)")


def _highlight_mentions(escaped: str) -> str:
    return _MENTION_RE.sub(lambda m: f"[blue]{m.group(1)}[/blue]", escaped)


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
        self._protocol: Protocol = detect_image_protocol()
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
            author_color = _AUTHOR_COLORS.get(msg.platform, "cyan")
            icon = PLATFORM_ICONS.get(msg.platform, f"[{msg.platform}]")
            platform_tag = f"[bold {color}]{icon}[/bold {color}]"
            author = markup_escape(msg.author.ljust(_AUTHOR_WIDTH)[:_AUTHOR_WIDTH])
            body = _highlight_mentions(markup_escape(self._render_body(msg)))
            log.write(f"{platform_tag} [bold {author_color}]{author}[/bold {author_color}] {body}")

    def _render_body(self, msg: Message) -> str:
        if not msg.runs or self._emoji_cache is None:
            return msg.text
        return "".join(render_run(r, self._protocol, self._emoji_cache) for r in msg.runs)

    async def on_unmount(self) -> None:
        self._bus_task.cancel()
        if self._emoji_cache is not None:
            await self._emoji_cache.aclose()
