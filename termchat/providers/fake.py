import asyncio
from typing import AsyncGenerator

from termchat.domain.message import Message


class FakeProvider:
    def __init__(self, messages_to_yield: list[Message], delay: float = 0.0) -> None:
        self._messages = messages_to_yield
        self._delay = delay

    async def messages(self) -> AsyncGenerator[Message, None]:
        for msg in self._messages:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            yield msg
