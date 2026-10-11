## Context

termchat's provider contract is the `Provider` Protocol: a single `messages() ->
AsyncGenerator[Message, None]`. Existing providers (`TwitchProvider`, `YouTubeProvider`) already
demonstrate the required shape — a never-ending generator wrapping a reconnect loop that emits a
`system` `Message` on failure. The AMQP provider is a third implementation of the same contract,
wired into `MessageBus` in `__main__.py`.

The upstream pipeline (see `schema.md`) publishes a field-identical `MessageCreate` JSON payload to
a durable **fanout** exchange. A fanout exchange copies each message to every bound queue but
load-balances among multiple consumers of the *same* queue — the key constraint driving the queue
design. The broker DSN is provided by the operator (`amqp://user:pass@host:port/vhost`).

## Goals / Non-Goals

**Goals:**
- A `Provider` implementation that connects to the fanout exchange and yields mapped `Message`s.
- Per-instance isolation via a temporary exclusive queue, so two termchat processes on one host
  both receive all traffic.
- Failures are recoverable (reconnect) and never crash the bus.
- Wiring through CLI/config consistent with the existing providers.

**Non-Goals:**
- Message backlog/replay after a restart (a temporary queue has no durability).
- Publishing, or changing the upstream producers' topology.
- Rendering new platforms or changing the UI.

## Decisions

### Async AMQP client: `aio-pika`

Choose `aio-pika` (async AMQP 0-9-1 client on top of `aiormq`). Rationale: the provider contract is
an `AsyncGenerator` running inside `asyncio.TaskGroup`; aio-pika gives a native `async for` over a
`Queue` with `await`-based ack. Alternative considered: `pika`, which is synchronous and would need
a thread/`run_in_executor` bridge (as `YouTubeProvider` does with `httpx`) for no benefit here.

### Temporary per-instance queue

Declare the queue with an empty name (broker-assigned unique name), `exclusive=True`,
`auto_delete=True`, then bind it to the exchange with routing key `""` (fanout ignores the key).
`exclusive` guarantees only the declaring connection can use it, and it is deleted when that
connection closes. This is the mechanism that lets two instances coexist: each has a distinct
queue, so the fanout delivers a full copy to each. Alternative considered: a single durable named
queue (the Twitch publisher's approach) — rejected because concurrent consumers would round-robin
and each instance would see only part of the stream.

### Exchange declaration

Actively declare the exchange as a durable fanout (`exchange_declare(name, type="fanout",
durable=True)`) rather than a passive declare. This is idempotent when the exchange already exists
with matching parameters (the publishers declare a durable fanout), and it makes the provider
usable when it starts before any publisher has run. Alternative considered: passive declare —
rejected because it raises if the exchange is absent.

### Acknowledge-and-drop malformed payloads

Decode each `IncomingMessage.body` as JSON, map it, then `ack`. On decode/mapping failure, ack and
continue (optionally surfacing a `system` message). Because the queue is live-only and non-durable,
requeueing a malformed message has no value and risks a poison-message loop. Malformed input is a
producer bug, not a transport failure.

### Payload mapping and timestamps

`source` is an `IntEnum` serialized as an int: `0 → "twitch"`, `1 → "youtube"`. Unknown values are
treated as malformed (ack-and-drop). The wire payload carries no timestamp, so `Message.timestamp`
is the receive time (`datetime.now(UTC)`). `runs` is empty; the plain `text` (from `body`) is
sufficient for both UIs' fallback rendering.

### Reconnect loop

Mirror the established pattern: an outer loop wraps connect → declare → consume; any
connection/channel/consume exception emits `Message.system(...)` and triggers bounded exponential
backoff (start ~1s, cap ~60s), resetting on a fresh successful consumption. `asyncio.CancelledError`
propagates so shutdown is clean.

## Risks / Trade-offs

- **No backlog while termchat is down** → accepted; live consumption is the goal, and durability
  would reintroduce competing consumers. Documented as a non-goal.
- **Exchange parameter mismatch** (e.g. a non-durable exchange with the same name) → `Promise`
  `channel error`; mitigated by declaring durable fanout to match `schema.md`, and by the reconnect
  loop emitting a `system` message rather than dying silently.
- **Unbounded prefetch buffering** in a fast chat → can be bounded later with `channel.set_qos`;
  left at broker defaults initially to keep the design minimal.
- **DSN handling** → the DSN is used verbatim (URL-encoded credentials as provided); it may contain
  credentials, so it is never logged. Config/CLI only, per the operator's choice.

## Migration Plan

Additive: a new provider module plus new optional CLI flags/config keys. No existing behavior
changes. Rollback is removing the provider registration and dependency; no data migration. Deploy
by adding `[amqp]` to `~/.config/termchat/config.toml` (or passing `--amqp`/`--amqp-exchange`) and
running termchat against a broker with the fanout exchange present.
