import asyncio
from datetime import datetime, timezone

from termchat.app import MessageBus
from termchat.domain.message import Message
from termchat.providers.fake import FakeProvider


class _ErrorProvider:
    """Provider that raises immediately on iteration."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def messages(self):  # async generator
        raise self._error
        yield  # noqa: unreachable — makes this an async generator


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


async def test_failing_provider_does_not_kill_other_providers():
    good_msgs = [_msg(i, "good") for i in range(2)]
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus(
        [_ErrorProvider(RuntimeError("boom")), FakeProvider(good_msgs)], queue
    )
    await bus.run()  # must not raise
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    good = [m for m in collected if m.platform == "good"]
    system = [m for m in collected if m.platform == "system"]
    assert len(good) == 2
    assert len(system) == 1
    assert "boom" in system[0].text


async def test_failing_provider_logs_class_name():
    queue: asyncio.Queue[Message] = asyncio.Queue()
    bus = MessageBus([_ErrorProvider(ValueError("bad value"))], queue)
    await bus.run()
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    system = [m for m in collected if m.platform == "system"]
    assert len(system) == 1
    assert "_ErrorProvider" in system[0].text
    assert "bad value" in system[0].text
