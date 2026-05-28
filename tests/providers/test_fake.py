import time
from datetime import UTC, datetime

from termchat.domain.message import Message
from termchat.providers.fake import FakeProvider


def _msg(i: int) -> Message:
    return Message(
        id=str(i),
        author="user",
        text=f"message {i}",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        platform="fake",
    )


async def test_fake_provider_yields_in_order():
    msgs = [_msg(i) for i in range(3)]
    provider = FakeProvider(msgs)
    collected: list[Message] = []
    async for msg in provider.messages():
        collected.append(msg)
    assert collected == msgs


async def test_fake_provider_empty():
    provider = FakeProvider([])
    collected = [m async for m in provider.messages()]
    assert collected == []


async def test_fake_provider_delay():
    msgs = [_msg(0), _msg(1)]
    provider = FakeProvider(msgs, delay=0.05)
    start = time.monotonic()
    collected = [m async for m in provider.messages()]
    elapsed = time.monotonic() - start
    assert collected == msgs
    assert elapsed >= 0.09  # two delays of 0.05s
