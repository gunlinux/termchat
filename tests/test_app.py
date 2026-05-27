import asyncio
from datetime import datetime, timezone

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.providers.fake import FakeProvider


def _msg(i: int, platform: str = "fake") -> Message:
    return Message(
        id=str(i),
        author="user",
        text=f"msg {i}",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        platform=platform,
    )


async def test_message_bus_single_provider():
    msgs = [_msg(i) for i in range(3)]
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus([FakeProvider(msgs)], queue)
    await bus.run()
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    assert collected == msgs


async def test_message_bus_two_providers_all_messages_land():
    msgs_a = [_msg(i, "a") for i in range(3)]
    msgs_b = [_msg(i, "b") for i in range(3)]
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus([FakeProvider(msgs_a), FakeProvider(msgs_b)], queue)
    await bus.run()
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    assert len(collected) == 6
    assert set(m.platform for m in collected) == {"a", "b"}


async def test_message_bus_interleaved_delays():
    msgs_a = [_msg(0, "a"), _msg(2, "a")]
    msgs_b = [_msg(1, "b"), _msg(3, "b")]
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus(
        [FakeProvider(msgs_a, delay=0.02), FakeProvider(msgs_b, delay=0.01)],
        queue,
    )
    await bus.run()
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    assert len(collected) == 4
    platforms = [m.platform for m in collected]
    assert platforms.count("a") == 2
    assert platforms.count("b") == 2
