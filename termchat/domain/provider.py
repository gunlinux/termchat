from typing import AsyncIterator, Protocol

from termchat.domain.message import Message


class Provider(Protocol):
    async def messages(self) -> AsyncIterator[Message]:
        ...
