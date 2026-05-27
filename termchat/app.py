import asyncio

from termchat.domain.message import Message
from termchat.domain.provider import Provider


class MessageBus:
    def __init__(self, providers: list[Provider], queue: asyncio.Queue[Message]) -> None:
        self._providers = providers
        self._queue = queue

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for provider in self._providers:
                tg.create_task(self._drain(provider))

    async def _drain(self, provider: Provider) -> None:
        async for msg in provider.messages():
            await self._queue.put(msg)
