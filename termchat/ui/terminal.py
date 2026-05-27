import asyncio

from termchat.domain.message import Message


class TerminalUI:
    def __init__(self, queue: asyncio.Queue[Message]) -> None:
        self._queue = queue

    async def run(self) -> None:
        while True:
            msg = await self._queue.get()
            print(f"[{msg.platform}] {msg.author}: {msg.text}", flush=True)
            self._queue.task_done()
