from datetime import datetime, timezone
from typing import AsyncIterator

from termchat.domain.message import Message
from termchat.domain.provider import Provider


class _ConcreteProvider:
    async def messages(self) -> AsyncIterator[Message]:
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        yield Message(id="1", author="a", text="hi", timestamp=ts, platform="test")


def test_provider_protocol_satisfied():
    provider: Provider = _ConcreteProvider()
    assert provider is not None


async def test_provider_yields_messages():
    provider = _ConcreteProvider()
    collected: list[Message] = []
    async for msg in provider.messages():
        collected.append(msg)
    assert len(collected) == 1
    assert collected[0].text == "hi"
