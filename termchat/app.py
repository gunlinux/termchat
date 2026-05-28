import asyncio
import uuid
from datetime import datetime, timezone

from termchat.domain.message import Message
from termchat.domain.provider import Provider


class MessageBus:
    def __init__(self, providers: list[Provider], queue: asyncio.Queue[Message]) -> None:
        self._providers = providers
        self._queue = queue
        self._counts: dict[str, int] = {}

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for provider in self._providers:
                tg.create_task(self._drain(provider))

    async def _drain(self, provider: Provider) -> None:
        gen = provider.messages()
        try:
            async for msg in gen:
                self._counts[msg.platform] = self._counts.get(msg.platform, 0) + 1
                await self._queue.put(msg)
        except Exception as e:
            await self._queue.put(Message(
                id=str(uuid.uuid4()),
                author="system",
                text=f"[{type(provider).__name__}] error: {e}",
                timestamp=datetime.now(timezone.utc),
                platform="system",
            ))
        finally:
            await gen.aclose()
