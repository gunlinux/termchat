from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Message:
    id: str
    author: str
    text: str
    timestamp: datetime
    platform: str
