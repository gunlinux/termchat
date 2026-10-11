import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import aio_pika

from termchat.domain.message import Message

# `MessageCreate.source` is an IntEnum serialized as an int by the upstream
# consumers; a fanout exchange copies each message to every bound queue.
_SOURCE_PLATFORMS: dict[int, str] = {0: "twitch", 1: "youtube"}

_RECONNECT_BACKOFF_INITIAL = 1.0
_RECONNECT_BACKOFF_MAX = 60.0


class _IncomingMessage(Protocol):
    @property
    def body(self) -> bytes: ...

    async def ack(self) -> None: ...


class _AmqpQueue(Protocol):
    async def bind(self, exchange: Any, routing_key: str = ...) -> Any: ...

    def iterator(self) -> AsyncIterator[_IncomingMessage]: ...


class _AmqpChannel(Protocol):
    async def declare_exchange(self, name: str, type: Any = ..., *, durable: bool = ...) -> Any: ...

    async def declare_queue(
        self, name: str = ..., *, exclusive: bool = ..., auto_delete: bool = ...
    ) -> _AmqpQueue: ...


class _AmqpConnection(Protocol):
    async def channel(self) -> _AmqpChannel: ...

    async def close(self) -> None: ...


type _Connector = Callable[[str], Awaitable[Any]]
type _Sleeper = Callable[[float], Awaitable[None]]


def _map_payload(payload: Any) -> Message | None:
    """Map a decoded ``MessageCreate`` payload to a domain ``Message``.

    The payload's ``author_id`` is required; an optional ``author`` display name
    (once the consumers publish it) is preferred for rendering and falls back to
    ``author_id`` when absent or empty. Returns ``None`` when the payload is
    malformed (not a dict, missing/invalid fields, or an unknown ``source``) so
    the caller can ack-and-drop it.
    """
    if not isinstance(payload, dict):
        return None
    body = payload.get("body")
    source = payload.get("source")
    message_id = payload.get("message_id")
    author_id = payload.get("author_id")
    if (
        not isinstance(body, str)
        or not isinstance(message_id, str)
        or not isinstance(author_id, str)
    ):
        return None
    if not isinstance(source, int) or isinstance(source, bool) or source not in _SOURCE_PLATFORMS:
        return None
    display = payload.get("author")
    author = display if isinstance(display, str) and display else author_id
    return Message(
        id=message_id,
        author=author,
        text=body,
        timestamp=datetime.now(UTC),
        platform=_SOURCE_PLATFORMS[source],
    )


async def _default_connect(dsn: str) -> Any:
    return await aio_pika.connect_robust(dsn)


class AmqpProvider:
    """Consume normalized chat from an AMQP fanout exchange.

    Each instance declares its own server-named ``exclusive``/``auto_delete``
    queue and binds it to the exchange, so the fanout delivers a full copy to
    every concurrently running termchat process instead of load-balancing
    between them.
    """

    def __init__(
        self,
        dsn: str,
        exchange: str,
        *,
        connect: _Connector | None = None,
        sleep: _Sleeper | None = None,
    ) -> None:
        self._dsn = dsn
        self._exchange = exchange
        self._connect = connect if connect is not None else _default_connect
        self._sleep = sleep if sleep is not None else asyncio.sleep

    async def messages(self) -> AsyncGenerator[Message, None]:
        """Yield messages forever, reconnecting on any connection failure."""
        backoff = _RECONNECT_BACKOFF_INITIAL
        while True:
            session = self._consume_session()
            try:
                async for msg in session:
                    backoff = _RECONNECT_BACKOFF_INITIAL
                    yield msg
            except asyncio.CancelledError:
                raise
            except Exception as e:
                yield Message.system(f"[amqp] {e}")
            finally:
                await session.aclose()
            await self._sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)

    async def _consume_session(self) -> AsyncGenerator[Message, None]:
        """One broker connection's lifetime; returns when the connection drops."""
        connection: _AmqpConnection = await self._connect(self._dsn)
        try:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                self._exchange, aio_pika.ExchangeType.FANOUT, durable=True
            )
            queue = await channel.declare_queue("", exclusive=True, auto_delete=True)
            await queue.bind(exchange, routing_key="")
            async for raw in queue.iterator():
                msg = self._decode(raw.body)
                await raw.ack()
                if msg is not None:
                    yield msg
        finally:
            await connection.close()

    @staticmethod
    def _decode(raw: bytes) -> Message | None:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return _map_payload(payload)
