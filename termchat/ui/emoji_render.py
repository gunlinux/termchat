"""Inline emoji rendering for the terminal and Textual UIs.

Detects whether the host terminal supports inline images (Kitty graphics
protocol or iTerm2 inline images) and renders `EmojiRun` either as a raw
image escape sequence or as the `:shortcut:` fallback text.

`EmojiImageCache` (fetch + disk caching) lives in `termchat.infra.emote_cache`
so the infrastructure concern is separate from this presentation module.
"""

import base64
import logging
import os
from collections.abc import Iterable
from typing import Literal
from typing import Protocol as _TypingProtocol

from termchat.domain.message import EmojiRun, MessageRun, TextRun

logger = logging.getLogger(__name__)

Protocol = Literal["kitty", "iterm2", "none"]


class ImageSource(_TypingProtocol):
    """Minimal interface for a bytes-by-URL image store.

    Decouples `render_run` from the concrete `EmojiImageCache` implementation
    so the presentation layer doesn't depend on network or disk I/O directly.
    """

    def get(self, url: str) -> bytes | None: ...
    async def prefetch(self, urls: Iterable[str]) -> None: ...
    async def aclose(self) -> None: ...


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


_KITTY_CHUNK = 4096
# Terminal cells an emote occupies, kept in sync between the Kitty (c=/r=) and
# iTerm2 (width=/height=) escapes so emotes are the same size on both.
_EMOJI_CELLS_W = 2
_EMOJI_CELLS_H = 1


def render_run(
    run: MessageRun,
    protocol: Protocol,
    cache: ImageSource | None,
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


def render_image(data: bytes, protocol: Protocol) -> str:
    """Build an inline-image escape from raw PNG bytes for the given protocol.

    Used for non-emote images (e.g. the YouTube platform icon). Sized to the
    same 2x1 cells as emotes. Returns "" when the terminal has no image support.
    """
    if protocol == "kitty":
        return _kitty_escape(data)
    if protocol == "iterm2":
        return _iterm2_escape(data)
    return ""


def _kitty_escape(data: bytes) -> str:
    # q=2 suppresses Kitty's per-command response acks. Without it Kitty replies
    # via the TTY input channel and the shell echoes the response bytes back to
    # the screen as visible garbage.
    payload = base64.standard_b64encode(data).decode("ascii")
    chunks = [payload[i : i + _KITTY_CHUNK] for i in range(0, len(payload), _KITTY_CHUNK)]
    if not chunks:
        chunks = [""]
    head = f"f=100,a=T,t=d,c={_EMOJI_CELLS_W},r={_EMOJI_CELLS_H},q=2"
    if len(chunks) == 1:
        return f"\x1b_G{head};{chunks[0]}\x1b\\"
    parts = [f"\x1b_G{head},m=1;{chunks[0]}\x1b\\"]
    for chunk in chunks[1:-1]:
        parts.append(f"\x1b_Gm=1;{chunk}\x1b\\")
    parts.append(f"\x1b_Gm=0;{chunks[-1]}\x1b\\")
    return "".join(parts)


def _iterm2_escape(data: bytes) -> str:
    payload = base64.standard_b64encode(data).decode("ascii")
    return (
        f"\x1b]1337;File=inline=1;width={_EMOJI_CELLS_W};height={_EMOJI_CELLS_H};"
        f"preserveAspectRatio=1:{payload}\x07"
    )
