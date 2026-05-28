import asyncio

from termchat.domain.message import Message
from termchat.domain.provider import Provider


class MessageBus:
    def __init__(self, providers: list[Provider], queue: asyncio.Queue[Message]) -> None:
        self._providers = providers
        self._queue = queue
        # Mutated from concurrent _drain tasks but safe: asyncio is single-threaded
        # and there is no await between the read and write, so no interleaving.
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
            sys_msg = Message.system(f"[{type(provider).__name__}] error: {e}")
            self._counts[sys_msg.platform] = self._counts.get(sys_msg.platform, 0) + 1
            await self._queue.put(sys_msg)
        finally:
            await gen.aclose()
