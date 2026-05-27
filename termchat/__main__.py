import asyncio
from datetime import datetime, timezone

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.providers.fake import FakeProvider
from termchat.ui.terminal import TerminalUI


async def _demo() -> None:
    msgs = [
        Message(id=str(i), author="demo_user", text=f"Demo message {i}",
                timestamp=datetime.now(timezone.utc), platform="fake")
        for i in range(5)
    ]
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus([FakeProvider(msgs, delay=0.1)], queue)
    ui = TerminalUI(queue)

    bus_task = asyncio.create_task(bus.run())
    ui_task = asyncio.create_task(ui.run())

    await bus_task
    await queue.join()
    ui_task.cancel()


if __name__ == "__main__":
    asyncio.run(_demo())
