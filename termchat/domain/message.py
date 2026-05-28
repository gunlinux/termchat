import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class TextRun:
    text: str


@dataclass(frozen=True)
class EmojiRun:
    shortcut: str
    image_url: str | None
    is_custom: bool


type MessageRun = TextRun | EmojiRun


@dataclass(frozen=True)
class Message:
    id: str
    author: str
    text: str
    timestamp: datetime
    platform: str
    runs: tuple[MessageRun, ...] = ()

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(
            id=str(uuid.uuid4()),
            author="system",
            text=text,
            timestamp=datetime.now(UTC),
            platform="system",
        )
