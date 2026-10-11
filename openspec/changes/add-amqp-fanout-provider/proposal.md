## Why

termchat today only reads chat by talking to each platform directly (Twitch IRC, YouTube
live-chat poller). The `gunlinux` message pipeline already normalizes Twitch and YouTube chat
into a single `MessageCreate` payload and publishes it to a durable **fanout** exchange on
RabbitMQ (see `schema.md`). Consuming that exchange lets termchat aggregate chat without holding
per-platform connections, and reuse the upstream consumers' normalization/emote work.

The catch: a fanout exchange delivers a copy of each message to *every bound queue*, but two
terminchat instances bound to the *same* durable queue would compete (RabbitMQ round-robins
between consumers) and each instance would see only a random half of the messages. The provider
must therefore declare its **own** queue for each running instance.

## What Changes

- Add a new **AMQP fanout provider** that connects to a RabbitMQ broker and consumes normalized
  chat messages from a configured fanout exchange.
- On startup the provider declares a **temporary queue** — server-named, `exclusive`, and
  `auto_delete` — and binds it to the exchange. `exclusive` + a unique name guarantees each
  running termchat instance gets its own copy of every message and that the queue disappears when
  the instance disconnects.
- Add CLI flags `--amqp <dsn>` and `--amqp-exchange <name>`, plus an `[amqp]` config section
  (`dsn`, `exchange`). At least one of `--twitch`, `--youtube`, `--demo`, or `--amqp` is required;
  `--amqp` requires an exchange name (flag or config) and errors clearly if it is missing.
- Map the wire `MessageCreate` payload onto the domain `Message`: `body → text`,
  `source` (`0`/`1`) → `platform` (`twitch`/`youtube`), `message_id → id`, `author_id → author`;
  `timestamp` is receive time (`datetime.now(UTC)`) and `runs` is empty.
- Reuse the existing reconnect-with-backoff pattern: on connection/channel failure, emit a
  `system` message and reconnect rather than terminating the provider.
- Add `aio-pika` as a runtime dependency.

**Non-goals**
- No backlog/replay: a temporary exclusive queue is live-only, so messages published while
  termchat is not running are not received. Durable named queues are intentionally not used
  (they would reintroduce the competing-consumer problem).
- No publishing, no PII/credential changes to the upstream consumers.
- No changes to existing providers, the UI, or the domain model.

## Capabilities

### New Capabilities
- `amqp-provider`: consuming normalized chat messages from an AMQP fanout exchange through a
  per-instance temporary exclusive queue, with reconnect, payload mapping, and CLI/config wiring.

### Modified Capabilities
<!-- None: no existing spec-level behavior changes. -->

## Impact

- **New code**: `termchat/providers/amqp.py` (provider), registration/wiring in
  `termchat/__main__.py`, config keys in `termchat/config.py` usage.
- **CLI**: new `--amqp` and `--amqp-exchange` flags; the "at least one source" guard expands.
- **Dependencies**: adds `aio-pika` (asyncio AMQP 0-9-1 client) to `pyproject.toml`.
- **Config file**: new optional `[amqp]` section in `~/.config/termchat/config.toml`.
- **Docs**: README usage/config sections and CLAUDE.md provider list.
- **External**: requires a reachable RabbitMQ broker and a fanout exchange already declared by the
  upstream publishers (e.g. `twitch_consumer`).
