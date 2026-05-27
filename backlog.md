# Backlog

Tasks are ordered so each one extends the working system without modifying or blocking what came before.

---

## T1 — Project scaffold ✅
Set up `uv` project: `pyproject.toml`, empty package skeleton (`termchat/__init__.py`, `termchat/domain/`, `termchat/providers/`, `termchat/ui/`), `pytest` configured, CI-ready `uv.lock`.

**Done when:** `uv run pytest` exits 0 (no tests yet, just no import errors).

---

## T2 — Domain model ✅
Define `Message` dataclass (id, author, text, timestamp, platform) and `Provider` Protocol with `async def messages() -> AsyncIterator[Message]`. No I/O, pure data.

**Done when:** unit tests cover `Message` construction and validate the `Provider` Protocol signature.

---

## T3 — In-memory fake provider ✅
Implement `FakeProvider(Provider)` in `termchat/providers/fake.py` that yields a configurable list of `Message` objects with a configurable delay. Used as the test double for all UI and orchestration tests.

**Done when:** tests drive `FakeProvider` and assert messages arrive in order.

---

## T4 — Async message bus ✅
Implement `MessageBus` in `termchat/app.py`: accepts a list of `Provider` instances, fans them out with `asyncio.TaskGroup`, pushes every `Message` into a single `asyncio.Queue[Message]`.

**Done when:** tests use two `FakeProvider` instances and assert all messages land in the queue regardless of interleaving.

---

## T5 — Minimal terminal UI (static render) ✅
Implement `termchat/ui/terminal.py` with a `TerminalUI` that reads from `asyncio.Queue[Message]` and prints each message to stdout as `[platform] author: text`. No fancy TUI yet.

**Done when:** running `uv run python -m termchat` with `FakeProvider` prints messages to the terminal.

---

## T6 — Twitch IRC provider ✅
Implement `termchat/providers/twitch.py`: async IRC reader (plain `asyncio` streams, no third-party IRC lib required). Connects to `irc.chat.twitch.tv:6667`, joins a channel, parses `PRIVMSG` into `Message`. Channel and oauth token read from env vars `TWITCH_CHANNEL` / `TWITCH_OAUTH`.

**Done when:** integration test (skipped unless env vars present) receives at least one real message; unit tests cover IRC line parsing with raw string fixtures.

---

## T7 — YouTube chat provider ✅
Implement `termchat/providers/youtube.py` using `yt-dlp` as a library (not subprocess) to poll live chat for a given video/stream URL. Video URL read from env var `YOUTUBE_URL`.

**Done when:** integration test (skipped unless env var present) receives at least one real message; unit tests cover mapping yt-dlp chat entries to `Message`.

---

## T8 — CLI entry point ✅
Add `__main__.py` with argument parsing (`argparse` or `click`): `--twitch <channel>`, `--youtube <url>`, at least one required. Instantiates the matching providers and wires them to `MessageBus` + `TerminalUI`.

**Done when:** `uv run python -m termchat --twitch <channel>` works end-to-end; `--help` is informative.

---

## T9 — Rich TUI (scrollable message log) ✅
Replace the stdout printer in `TerminalUI` with a `textual` or `rich` live display: scrollable message list, color-coded by platform, author column aligned.

**Done when:** visual smoke-test shows new messages appending without flicker; existing unit tests still pass unchanged (UI is behind the same interface).

---

## T10 — Graceful shutdown ✅
Handle `SIGINT`/`SIGTERM`: cancel the `TaskGroup`, drain remaining queued messages, print a short summary (messages received per provider), exit cleanly.

**Done when:** `Ctrl-C` during a live session exits with code 0 and no traceback.

---

## T11 — Configuration file support ✅
Load provider settings from a `~/.config/termchat/config.toml` (channels, credentials) as defaults, overridable by CLI flags. Use `tomllib` (stdlib 3.11+).

**Done when:** starting without CLI flags but with a valid config file works identically to passing all flags explicitly.
