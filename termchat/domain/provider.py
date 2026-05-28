from collections.abc import AsyncGenerator
from typing import Protocol

from termchat.domain.message import Message


class Provider(Protocol):
    def messages(self) -> AsyncGenerator[Message, None]: ...
