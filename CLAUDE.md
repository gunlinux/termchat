# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

_Last synced to commit `4a92d7b`._

## Project Overview

**termchat** is an async, read-only terminal multi-chat aggregator written in Python. It reads live chat from multiple streaming platforms simultaneously and displays them in a terminal UI.

Planned integrations:
- **Twitch** — IRC reader
- **YouTube** — `yt-dlp` library

The architecture must support adding more providers later.

## Tooling

- **Package manager**: `uv`
- **Run project**: `uv run python -m termchat`
- **Run tests**: `uv run pytest`
- **Run single test**: `uv run pytest tests/path/to/test_file.py::test_name`
- **Add dependency**: `uv add <package>`
- **Add dev dependency**: `uv add --dev <package>`

## Architecture

The project follows **Clean Architecture** with strict layer separation:

```
termchat/
  providers/      # Message source adapters (Twitch IRC, YouTube via yt-dlp, fake)
  domain/         # Core entities (Message dataclass, Provider Protocol)
  ui/             # Presentation layer (stdout TerminalUI, Textual TermchatApp)
  app.py          # MessageBus: fan-in of providers into a shared asyncio.Queue
  config.py       # tomllib loader for ~/.config/termchat/config.toml
  __main__.py     # CLI entry point: argparse, signal handlers, UI selection, shutdown
```

**Key design rules:**
- All I/O is async (`asyncio`); providers yield messages via `AsyncIterator` or push to a shared `asyncio.Queue`
- Providers implement a common abstract interface (`domain/`) — the UI and orchestrator depend only on that interface, never on concrete provider classes
- The UI layer is swappable; the domain and providers must not import from `ui/`
- Development is test-driven: write tests before implementation

**Orchestration flow (split across two files — not all in `app.py`):**
- `app.py` holds `MessageBus`: takes a `list[Provider]`, fans them out with `asyncio.TaskGroup`, pushes every `Message` into a single `asyncio.Queue[Message]`, and tracks per-platform counts via `bus.counts`.
- `__main__.py` does the real wiring: parses CLI args, applies config defaults (CLI overrides config), installs `SIGINT`/`SIGTERM` handlers on the loop, picks the UI, drains the queue on shutdown, and prints a per-platform summary.

## UI backends

Two implementations share the same `asyncio.Queue[Message]` contract:
- Default — `ui/terminal.py::TerminalUI`: plain stdout, `[platform] author: text`.
- `--tui` — `ui/tui.py::TermchatApp`: Textual `RichLog`, color-coded per platform (`_PLATFORM_COLORS`), drains the queue on a 0.1s interval. The bus runs as a task owned by the Textual app in this mode.

## Providers — gotchas

- **`TwitchProvider`**: raw `asyncio.open_connection` to `irc.chat.twitch.tv:6667`. When `TWITCH_OAUTH` is empty it logs in anonymously as `justinfan<rand>` — no creds required to read public channels. Handles `PING`/`PONG`. `parse_privmsg()` is exposed separately so it can be unit-tested with raw IRC fixtures.
- **`YouTubeProvider`**: takes a channel handle (e.g. `somechannel` or `@somechannel`) and resolves it to `https://www.youtube.com/@<channel>/live` — i.e. the channel's currently-active live broadcast. yt-dlp is then called with `getcomments=True` on that resolved URL; this is **not** a true live-chat tail and will yield nothing if no broadcast is live. `live_url` is exposed as a property for testing.
- Both providers expose a `from_env()` classmethod (`TWITCH_CHANNEL`/`TWITCH_OAUTH`, `YOUTUBE_CHANNEL`) used by integration tests.
- `FakeProvider` is the standard test double for orchestration/UI tests.

## CLI flags

- `--twitch <channel>` — Twitch channel (optionally uses `TWITCH_OAUTH` env var)
- `--youtube <channel>` — YouTube channel handle (resolves to its active live stream)
- `--demo` — runs `FakeProvider` so the pipeline works without creds
- `--tui` — switch to the Textual UI
- At least one of `--twitch`, `--youtube`, or `--demo` is required (or supplied via config).

Config file (`~/.config/termchat/config.toml`) keys: `[twitch].channel`, `[youtube].channel`. CLI flags override config.

## Testing conventions

- `pytest-asyncio` is in `auto` mode (configured in `pyproject.toml`) — async tests do **not** need `@pytest.mark.asyncio`.
- Integration tests are `pytest.skipif`-gated on env vars and hit real services; unit tests cover parsing and mapping with fixtures.

## Development Approach

- Modern Python (3.12+): use `match`, `dataclass`, `TypeAlias`, `Protocol`, `asyncio.TaskGroup`, etc.
- Each provider lives in its own module under `providers/` and is registered in a central provider registry or passed explicitly at startup
- Keep providers stateless where possible; connection/session state belongs inside the provider class, not in global scope
