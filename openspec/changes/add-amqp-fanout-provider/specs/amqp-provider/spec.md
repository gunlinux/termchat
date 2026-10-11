## Purpose

Lets termchat consume normalized Twitch/YouTube chat that upstream consumers publish to a
RabbitMQ fanout exchange, without holding direct per-platform connections.

## ADDED Requirements

### Requirement: Consume from a fanout exchange via a per-instance temporary queue

The provider SHALL connect to the AMQP broker at the configured DSN and consume messages from a
configured fanout exchange. For each running instance it SHALL declare a **unique, temporary
queue** (server-named, `exclusive`, `auto_delete`) and bind that queue to the exchange with an
empty routing key, so that every concurrently running termchat instance receives its own copy of
every message instead of competing for messages on a shared queue.

#### Scenario: Two instances both receive every message
- **WHEN** two termchat instances connect to the same broker and fanout exchange, and one message
  is published to the exchange
- **THEN** each instance receives that message (the message is not load-balanced between them)

#### Scenario: Temporary queue is removed on disconnect
- **WHEN** a termchat instance's AMQP connection closes
- **THEN** its queue is deleted (server-named, `exclusive`, `auto_delete`), leaving no queue behind

#### Scenario: Provider starts before any publisher has declared the exchange
- **WHEN** the configured fanout exchange does not yet exist
- **THEN** the provider declares it as a durable fanout exchange and proceeds to bind and consume

### Requirement: Map the wire payload onto a domain message

The provider SHALL decode each message body as JSON conforming to the shared `MessageCreate`
contract (`body`, `source`, `message_id`, `author_id`, `stream_id`, `channel_id`, plus an optional
`author` display name) and map it onto the domain `Message` as follows: `body` → `text`, `source`
(`0` = twitch, `1` = youtube) → `platform`, `message_id` → `id`, and `author` → `author`, preferring
an optional payload `author` display name when present and non-empty and falling back to
`author_id` otherwise. `timestamp` SHALL be the receive time (`datetime.now(UTC)`); `runs` SHALL be
empty. Each consumed message SHALL be acknowledged after it has been turned into a domain message.

#### Scenario: Twitch message is mapped
- **WHEN** a payload with `source: 0` is consumed
- **THEN** the yielded `Message` has `platform == "twitch"`, `text` equal to `body`, `id` equal to
  `message_id`, and `author` equal to `author_id` when no display name is present, and the broker
  message is acknowledged

#### Scenario: Display name is preferred over the id
- **WHEN** a payload carries both `author_id` and a non-empty `author`
- **THEN** the yielded `Message` has `author` equal to the payload `author` display name

#### Scenario: YouTube message is mapped
- **WHEN** a payload with `source: 1` is consumed
- **THEN** the yielded `Message` has `platform == "youtube"`

#### Scenario: Malformed payload does not stall the stream
- **WHEN** a consumed body is not valid JSON or does not match the contract
- **THEN** the provider does not raise, acknowledges the message, and continues consuming
  subsequent messages

### Requirement: Reconnect on connection failure

The provider's `messages()` SHALL be a never-ending async generator. On an AMQP connection,
channel, or consumption error it SHALL emit a `system` message describing the failure and
reconnect with bounded exponential backoff instead of terminating. Cancellation
(`KeyboardInterrupt`/task cancel) SHALL propagate.

#### Scenario: Broker restarts while consuming
- **WHEN** the broker connection drops and later becomes available again
- **THEN** the provider emits a `system` message about the failure and, after backoff, reconnects
  and resumes consuming on a new temporary queue

#### Scenario: Cancellation stops the provider
- **WHEN** the consuming task is cancelled
- **THEN** the generator closes and no further reconnect attempt is made

### Requirement: CLI and config wiring

The provider SHALL be selectable with a `--amqp <dsn>` flag and an exchange name supplied by
`--amqp-exchange <name>` or the `[amqp].exchange` config key, with the DSN also readable from
`[amqp].dsn`. At least one of `--twitch`, `--youtube`, `--demo`, or `--amqp` MUST be supplied.
When `--amqp` is supplied without an exchange name, the program SHALL fail with a clear error
before starting.

#### Scenario: AMQP selected with DSN and exchange via CLI
- **WHEN** termchat is run with `--amqp <dsn> --amqp-exchange <name>`
- **THEN** the provider connects using that DSN and binds to that exchange

#### Scenario: AMQP selected with values from config
- **WHEN** config contains `[amqp]` `dsn` and `exchange` and termchat is run with `--amqp`
  (or with no AMQP flag but the section present and selected)
- **THEN** the provider uses the config values

#### Scenario: Missing exchange name fails fast
- **WHEN** termchat is run with `--amqp <dsn>` but neither `--amqp-exchange` nor a config
  exchange is provided
- **THEN** the program exits with a clear error and does not attempt to connect
