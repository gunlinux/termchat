import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aio_pika
import pytest

from termchat.domain.provider import Provider
from termchat.providers.amqp import AmqpProvider, _map_payload


def _wire(
    body: str = "hello",
    source: int = 0,
    message_id: str = "m1",
    author_id: str = "a1",
    author: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "body": body,
        "source": source,
        "message_id": message_id,
        "author_id": author_id,
        "stream_id": "s1",
        "channel_id": "c1",
    }
    if author is not None:
        payload["author"] = author
    return payload


class _FakeIncoming:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.body = json.dumps(payload).encode()
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


class _FakeQueue:
    def __init__(self, messages: list[_FakeIncoming]) -> None:
        self._messages = messages
        self.bound: tuple[Any, str] | None = None

    def iterator(self) -> AsyncIterator[_FakeIncoming]:
        async def _gen() -> AsyncIterator[_FakeIncoming]:
            for message in self._messages:
                yield message

        return _gen()

    async def bind(self, exchange: Any, routing_key: str = "") -> Any:
        self.bound = (exchange, routing_key)
        return None


class _BlockingQueue:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def bind(self, exchange: Any, routing_key: str = "") -> Any:
        return None

    def iterator(self) -> AsyncIterator[_FakeIncoming]:
        async def _gen() -> AsyncIterator[_FakeIncoming]:
            self.started.set()
            await asyncio.Event().wait()
            yield _FakeIncoming(_wire())  # pragma: no cover

        return _gen()


class _FakeChannel:
    def __init__(self, queue: _FakeQueue | _BlockingQueue) -> None:
        self._queue = queue
        self.exchange_name: str | None = None
        self.exchange_type: Any = None
        self.exchange_durable: bool | None = None
        self.exchange: Any = None
        self.queue_name: str | None = None
        self.exclusive: bool | None = None
        self.auto_delete: bool | None = None

    async def declare_exchange(self, name: str, type: Any = None, *, durable: bool = False) -> Any:
        self.exchange_name = name
        self.exchange_type = type
        self.exchange_durable = durable
        self.exchange = object()
        return self.exchange

    async def declare_queue(
        self, name: str = "", *, exclusive: bool = False, auto_delete: bool = False
    ) -> Any:
        self.queue_name = name
        self.exclusive = exclusive
        self.auto_delete = auto_delete
        return self._queue


class _FakeConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel
        self.closed = False

    async def channel(self) -> _FakeChannel:
        return self._channel

    async def close(self) -> None:
        self.closed = True


async def _no_sleep(_: float) -> None:
    return None


def test_map_payload_twitch():
    msg = _map_payload(_wire(body="hi", source=0, message_id="m", author_id="a"))
    assert msg is not None
    assert msg.platform == "twitch"
    assert msg.text == "hi"
    assert msg.id == "m"
    assert msg.author == "a"
    assert msg.runs == ()
    assert msg.timestamp.tzinfo is not None


def test_map_payload_youtube():
    msg = _map_payload(_wire(source=1))
    assert msg is not None
    assert msg.platform == "youtube"


def test_map_payload_prefers_author_display_name():
    msg = _map_payload(_wire(author_id="29122457", author="gunlinux"))
    assert msg is not None
    assert msg.author == "gunlinux"


@pytest.mark.parametrize("author", ["", None])
def test_map_payload_falls_back_to_author_id(author):
    msg = _map_payload(_wire(author_id="29122457", author=author))
    assert msg is not None
    assert msg.author == "29122457"


def test_map_payload_non_string_author_falls_back_to_author_id():
    payload = _wire(author_id="29122457")
    payload["author"] = 42
    msg = _map_payload(payload)
    assert msg is not None
    assert msg.author == "29122457"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not a dict",
        [],
        {"body": "hi"},
        _wire(source=2),
        _wire(source=True),
        {"body": 1, "source": 0, "message_id": "m", "author_id": "a"},
        {"body": "hi", "source": 0, "message_id": "m"},
        {"body": "hi", "source": 0, "message_id": "m", "author_id": 5},
    ],
)
def test_map_payload_malformed_returns_none(payload):
    assert _map_payload(payload) is None


async def test_consume_declares_exclusive_queue_binds_and_maps():
    incoming = [
        _FakeIncoming(_wire(source=0, message_id="m1")),
        _FakeIncoming(_wire(source=1, message_id="m2")),
    ]
    queue = _FakeQueue(incoming)
    channel = _FakeChannel(queue)
    connection = _FakeConnection(channel)

    async def connect(dsn: str) -> Any:
        assert dsn == "amqp://broker"
        return connection

    provider = AmqpProvider("amqp://broker", "chat", connect=connect, sleep=_no_sleep)
    gen = provider.messages()
    first = await anext(gen)
    second = await anext(gen)
    await gen.aclose()

    assert first.platform == "twitch" and first.id == "m1"
    assert second.platform == "youtube" and second.id == "m2"
    assert all(m.acked for m in incoming)
    assert connection.closed
    assert channel.queue_name == ""
    assert channel.exclusive is True
    assert channel.auto_delete is True
    assert channel.exchange_name == "chat"
    assert channel.exchange_type == aio_pika.ExchangeType.FANOUT
    assert channel.exchange_durable is True
    assert queue.bound == (channel.exchange, "")


async def test_consume_skips_malformed_but_acks():
    good = _FakeIncoming(_wire(message_id="ok"))
    bad = _FakeIncoming({"unexpected": "shape"})
    queue = _FakeQueue([bad, good])
    channel = _FakeChannel(queue)
    connection = _FakeConnection(channel)

    async def connect(dsn: str) -> Any:
        return connection

    provider = AmqpProvider("amqp://broker", "chat", connect=connect, sleep=_no_sleep)
    gen = provider.messages()
    msg = await anext(gen)
    await gen.aclose()

    assert msg.id == "ok"
    assert bad.acked and good.acked


async def test_reconnect_after_connection_error():
    attempts = 0
    queue = _FakeQueue([_FakeIncoming(_wire(message_id="ok"))])
    channel = _FakeChannel(queue)
    connection = _FakeConnection(channel)

    async def connect(dsn: str) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("broker down")
        return connection

    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = AmqpProvider("amqp://broker", "chat", connect=connect, sleep=sleep)
    gen = provider.messages()
    first = await anext(gen)
    assert first.platform == "system"
    assert "broker down" in first.text
    second = await anext(gen)
    assert second.id == "ok"
    await gen.aclose()

    assert sleeps[0] == 1.0


async def test_cancellation_propagates():
    queue = _BlockingQueue()
    channel = _FakeChannel(queue)
    connection = _FakeConnection(channel)

    async def connect(dsn: str) -> Any:
        return connection

    provider = AmqpProvider("amqp://broker", "chat", connect=connect, sleep=_no_sleep)

    async def consume() -> None:
        async for _ in provider.messages():
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(queue.started.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_provider_satisfies_protocol():
    provider: Provider = AmqpProvider("amqp://broker", "chat")
    assert provider is not None
