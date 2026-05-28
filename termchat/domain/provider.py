from typing import AsyncGenerator, Protocol

from termchat.domain.message import Message


class Provider(Protocol):
    def messages(self) -> AsyncGenerator[Message, None]:
        ...
