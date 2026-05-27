import asyncio
from typing import AsyncIterator

from termchat.domain.message import Message


class FakeProvider:
    def __init__(self, messages_to_yield: list[Message], delay: float = 0.0) -> None:
        self._messages = messages_to_yield
        self._delay = delay

    async def messages(self) -> AsyncIterator[Message]:
        for msg in self._messages:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield msg
