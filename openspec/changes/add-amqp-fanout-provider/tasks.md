## 1. Dependency

- [x] 1.1 Add `aio-pika` to `pyproject.toml` dependencies and run `uv sync`; verify `uv run python -c "import aio_pika"` succeeds

## 2. Provider core

- [x] 2.1 Add a pure payload-mapping function (wire `MessageCreate` dict → domain `Message`) in `termchat/providers/amqp.py`; verify with unit tests covering `source=0`→`twitch`, `source=1`→`youtube`, receive-time `timestamp`, and malformed/unknown-source returning a drop signal
- [x] 2.2 Implement `AmqpProvider.messages()`: connect at the DSN, declare a durable fanout exchange, declare a server-named `exclusive=True, auto_delete=True` queue, bind with empty routing key, `async for` over messages, ack each, and skip malformed payloads; verify with an injected fake connection/channel that the same queue is bound and messages map to expected `Message`s
- [x] 2.3 Wrap consumption in a reconnect-with-backoff loop that emits a `system` `Message` on failure and resets backoff after a successful message, and lets `CancelledError` propagate; verify with tests that simulate a channel error then a recovery, and a cancellation
- [x] 2.4 Verify the provider satisfies the `Provider` protocol and that `tests/providers/test_amqp.py` passes via `uv run pytest tests/providers/test_amqp.py`

## 3. CLI and config wiring

- [x] 3.1 Add `--amqp <dsn>` and `--amqp-exchange <name>` flags plus `[amqp]` config keys (`dsn`, `exchange`) in `termchat/config.py` usage / `termchat/__main__.py`; include `--amqp` in the "at least one source" guard and fail fast with a clear error when the exchange name is missing; verify with CLI tests
- [x] 3.2 Register the provider in `_run()` so `--amqp` builds an `AmqpProvider` and adds it to the bus; verify with a CLI test asserting the provider is constructed for `--amqp --amqp-exchange`
- [x] 3.3 Verify config fallback: `[amqp].dsn`/`[amqp].exchange` are used when CLI flags are absent, via `tests/test_config.py` / CLI tests

## 4. Documentation and final verification

- [x] 4.1 Update `README.md` (usage + `[amqp]` config example) and `CLAUDE.md` (provider list, CLI flags, arch description) to include the AMQP provider
- [x] 4.2 Run `make check` and confirm lint, format, types, and the full test suite pass clean
