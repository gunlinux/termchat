from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TextRun:
    text: str


@dataclass(frozen=True)
class EmojiRun:
    shortcut: str
    image_url: str | None
    is_custom: bool


MessageRun = TextRun | EmojiRun


@dataclass(frozen=True)
class Message:
    id: str
    author: str
    text: str
    timestamp: datetime
    platform: str
    runs: tuple[MessageRun, ...] = field(default_factory=tuple)
