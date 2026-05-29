# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

_Last synced to commit `eab3ed3`._

## Project Overview

**termchat** is an async, read-only terminal multi-chat aggregator written in Python. It reads live chat from multiple streaming platforms simultaneously and displays them in a terminal UI.

Integrations:
- **Twitch** — IRC reader
- **YouTube** — native live-chat poller via `httpx` (bootstraps from watch page, polls `get_live_chat` API)

The architecture must support adding more providers later.

## Tooling

- **Package manager**: `uv`
- **Run project**: `uv run python -m termchat`
- **Run tests**: `uv run pytest` (or `make test`)
- **Run single test**: `uv run pytest tests/path/to/test_file.py::test_name`
- **Add dependency**: `uv add <package>`
- **Add dev dependency**: `uv add --dev <package>`
- **Lint / format**: `make lint` / `make fix` (Ruff, configured in `pyproject.toml`: `E,F,I,UP,B`, line-length 100, target py312)
- **Type-check**: `make types` (`uv run pyright`)
- **Everything**: `make check` (lint + fix + types + test)

**Definition of Done:** a change is not done until `make check` passes clean (lint + fix + types + test, all green). Run it before considering any task complete.

## Architecture

The project follows **Clean Architecture** with strict layer separation:

```
termchat/
  providers/      # Twitch IRC + emote registry, YouTube native poller, fake
  domain/         # Core entities (Message dataclass with structured runs, Provider Protocol)
  infra/          # Infrastructure ring: EmojiImageCache (HTTP fetch + in-memory LRU + disk cache)
  ui/             # Presentation layer (stdout TerminalUI, Textual TermchatApp, emoji_render, _theme)
  app.py          # MessageBus: fan-in of providers into a shared asyncio.Queue
  config.py       # tomllib loader for ~/.config/termchat/config.toml
  __main__.py     # CLI entry point: argparse, signal handlers, UI selection, shutdown
```

**Key design rules:**
- All I/O is async (`asyncio`); providers yield messages via `AsyncIterator` or push to a shared `asyncio.Queue`
- Providers implement a common abstract interface (`domain/`) — the UI and orchestrator depend only on that interface, never on concrete provider classes
- The UI layer is swappable; the domain and providers must not import from `ui/`
- `infra/` is the infrastructure ring (network/disk/Pillow I/O). It holds `EmojiImageCache`; the presentation layer (`ui/emoji_render.py`) depends only on the narrow `ImageSource` protocol it defines ("give me bytes for this URL"), not on the concrete cache
- Backend-neutral UI constants shared by both UIs live in `ui/_theme.py` (currently `PLATFORM_ICONS`); per-platform colors stay per-backend because the encodings differ (raw ANSI in `terminal.py` vs. Rich color names in `tui.py`)
- Development is test-driven: write tests before implementation

**Orchestration flow (split across two files — not all in `app.py`):**
- `app.py` holds `MessageBus`: takes a `list[Provider]`, fans them out with `asyncio.TaskGroup`, pushes every `Message` into a single `asyncio.Queue[Message]`, and tracks per-platform counts via `bus.counts`. `_drain` catches any unhandled exception from a provider and emits a `platform="system"` message to the queue so the UI surfaces the error rather than silently dropping the provider.
- `__main__.py` does the real wiring: parses CLI args, applies config defaults (CLI overrides config), clears the terminal once on startup (`\033[2J\033[H` before `asyncio.run`), installs `SIGINT`/`SIGTERM` handlers on the loop, picks the UI, drains the queue on shutdown, and prints a per-platform summary.

## UI backends

Two implementations share the same `asyncio.Queue[Message]` contract:
- Default — `ui/terminal.py::TerminalUI`: plain stdout, each line emitted as `\n{icon} {author}: text` (the newline *precedes* the message so the cursor stays parked after the last line rather than on a fresh empty row). Icons come from `ui/_theme.py::PLATFORM_ICONS` (Twitch / YouTube Nerd Font glyphs, `"◉"` fake, `"⚙"` system), colored with a platform-specific ANSI 24-bit color (`_PLATFORM_ANSI`); author names are also ANSI-colored per platform (`_AUTHOR_ANSI`). **YouTube is a special case**: the YouTube Nerd Font glyph (`U+F167`) is missing in many fonts, so in image-capable terminals the icon is rendered from a bundled PNG (`ui/assets/youtube.png`) instead of the glyph. `_load_youtube_icon` reads the asset once at construction via `importlib.resources` and pre-builds its inline-image escape through `emoji_render.render_image`; the result (`self._youtube_icon`) is reused for every YouTube message, with no ANSI color wrapper (the PNG is already full-color). It is `None` (and the code falls back to the glyph + ANSI path) when the terminal has no image support or the asset can't be read. Writes via `sys.stdout.write` so raw escape sequences pass through unmolested. Uses `ui/emoji_render.py` to render `EmojiRun`s inline: in Kitty (`KITTY_WINDOW_ID` / `TERM=xterm-kitty`) or iTerm2/WezTerm (`TERM_PROGRAM`), emoji images are emitted as inline-image escape sequences after their bytes are fetched and cached; elsewhere they fall back to `:shortcut:` text. `emoji_render.py` is purely presentation: `detect_image_protocol` and `render_run`/`render_image`/`_kitty_escape`/`_iterm2_escape` build the escapes given already-fetched bytes (`render_run` pulls emote bytes from an `ImageSource`; `render_image` takes raw bytes directly, used for the non-emote YouTube icon). Emote cell size is `_EMOJI_CELLS_W=2 × _EMOJI_CELLS_H=1`, kept in sync between the Kitty (`c=`/`r=`) and iTerm2 (`width=`/`height=`) escapes. The Kitty escape always carries `q=2` (suppress per-command response acks — without it Kitty replies on stdin and the shell echoes the response bytes back as visible garbage) and is chunked at 4096 base64 chars (the protocol's per-escape limit): single-chunk emits use `\x1b_Gf=100,a=T,t=d,c=2,r=1,q=2;…\x1b\\` and multi-chunk runs continue with `m=1` chained chunks terminated by `m=0`.
- The fetch/cache concern lives in `infra/emote_cache.py::EmojiImageCache` (imported by both UIs). `EmojiImageCache._fetch` pipes successful responses through `_to_png_first_frame` — Pillow `Image.open → seek(0) → convert("RGBA") → save(PNG)` — so animated GIF/WebP/AVIF emotes render as a static first frame. Kitty's `f=100` only accepts PNG, and animation rendering through this layered renderer was tried and reverted (Kitty animation worked but Twitch's per-channel rate-limited GIF endpoint produced visibly worse end-to-end behavior than the static fallback). `TerminalUI` blocks each message on `EmojiImageCache.prefetch(urls)` before printing, awaiting every uncached emote URL so the message lands with images already cached — no `:shortcut:` flash on the first occurrence. Failed fetches still drop silently and the message proceeds with the shortcode fallback for that emote. `get(url)` is a query-with-side-effect: it returns cached bytes or, on a miss, schedules a background `_fetch` task (returning `None` that frame); concurrent prefetch + get calls for the same URL share one in-flight task. The cache is two-tier: an in-memory LRU (256 entries, lost on exit) backed by a per-user disk cache at `default_disk_cache_dir()` (`$XDG_CACHE_HOME/termchat/emotes` or `~/.cache/termchat/emotes`) keyed by `sha1(url)`, with a 30-day mtime TTL. `_fetch` checks disk first, falls through to network on miss/expiry, and writes successful PNG bytes back atomically (tempfile + `os.replace`). Tests construct `EmojiImageCache` with `cache_dir=None` (the default) to keep the disk tier disabled and out of the user's real cache dir; `TerminalUI` opts in explicitly by passing `default_disk_cache_dir()`. If `mkdir` fails (read-only FS, perm denied) the cache silently degrades to in-memory only.
- `--tui` — `ui/tui.py::TermchatApp`: Textual `RichLog`, color-coded per platform (`_PLATFORM_COLORS` for icons, `_AUTHOR_COLORS` for author names), using the same Nerd Font icons as TerminalUI; drains the queue on a 0.1s interval. The bus runs as a task owned by the Textual app in this mode. **Inline images are NOT rendered in TUI mode**: Textual's `Strip.crop` ignores Rich's `is_control` flag and measures segments by raw text length, which truncates the multi-KB base64 escape to the terminal width and breaks the Kitty/iTerm2 graphics command. TUI mode falls back to `:shortcut:` text for emotes regardless of terminal. Use the default (plain stdout) UI for inline image rendering.

## Message structure

`Message` carries both a flat `text` (the canonical plain form, with custom emoji rendered as `:shortcut:` and Unicode emoji glyphs inline) and a structured `runs: tuple[MessageRun, ...]` where each `MessageRun` is either a `TextRun` or `EmojiRun(shortcut, image_url, is_custom)`. `runs` defaults to `()` — providers that don't supply structure (`FakeProvider`, `TwitchProvider`) leave it empty and the TUI falls back to `msg.text`.

## Providers — gotchas

- **`TwitchProvider`**: raw `asyncio.open_connection` to `irc.chat.twitch.tv:6667`. `messages()` is a never-ending generator: an outer reconnect loop wraps `_read_session()` (one TCP connection's lifetime). Sessions end on clean server disconnect, network error, or read timeout — `readline()` is wrapped in `asyncio.wait_for(..., timeout=_READ_TIMEOUT=360s)` because the server PINGs every ~5 min and silence past that threshold means the link is dead (NAT drop, route flap) before TCP would ever notice. Reconnect backoff starts at 1s, doubles per failure, caps at 60s, and resets to 1s as soon as a fresh session delivers its first message. Each reconnect rebuilds a fresh `TwitchEmoteRegistry` and re-runs the BTTV/7TV/GQL fetches — catches emote-set changes for free. Cancellation (`KeyboardInterrupt` / task cancel) passes through the reconnect loop. When `TWITCH_OAUTH` is empty it logs in anonymously as `justinfan<rand>` — no creds required to read public channels. Sends `CAP REQ :twitch.tv/tags twitch.tv/commands` before `JOIN` so Twitch decorates messages with the `@`-prefixed IRCv3 tag form. Parses `emotes=` (native positional emotes) and `room-id` (channel ID, captured from `ROOMSTATE` on JOIN); the room-id triggers a background fetch of BTTV channel + 7TV channel + Twitch native channel emote sets via `TwitchEmoteRegistry` (BTTV global is fetched on connect, room-independent). Twitch native channel emotes (subscriber + `localEmoteSets` / follower-only) are pulled via the public Twitch GQL endpoint (`gql.twitch.tv/gql` + the well-known web `Client-Id`) since Helix would require OAuth and anonymous IRC readers never receive `emotes=` tags for channel-only emotes — text-based registry lookup is the only path. Twitch image URLs (both for IRC-tagged native emotes and the GQL-sourced channel emotes) use the `/static/dark/2.0` CDN path so animated emotes return as a single-frame PNG — no Pillow GIF decode needed and no animation work to discard downstream. Native Twitch emote intervals from IRC tags always win over name-collisions with the registry (precedence handled in `build_runs`); within the registry the order is Twitch channel > BTTV channel > 7TV channel > BTTV global. `parse_privmsg`, `parse_tags`, `parse_emotes_tag`, `parse_roomstate`, and `build_runs` are exposed separately so they can be unit-tested with raw IRC fixtures.
- **`YouTubeProvider`**: takes a channel handle (e.g. `somechannel` or `@somechannel`) and resolves it to `https://www.youtube.com/@<channel>/live`. No longer uses `chat_downloader`; instead uses a native `_YouTubeLiveChatPoller` backed by `httpx`. On first iteration the poller fetches the watch page, extracts `ytcfg` (for `INNERTUBE_API_KEY` + `INNERTUBE_CONTEXT`) and `ytInitialData` (for the initial `liveChatRenderer` continuation token) via regex, then polls `youtubei/v1/live_chat/get_live_chat` in a loop, sleeping `timeoutMs` (clamped to 1–10 s) between requests. `_post_chat` retries up to 3 times with exponential backoff on 429 / 5xx. `_resolve_video_url()` walks the live URL through redirects and HTML to extract a `watch?v=` URL before handing off to the poller (falls back to the raw live URL with a stderr warning if resolution fails). Regular text messages (`liveChatTextMessageRenderer`) and SuperChats (`liveChatPaidMessageRenderer`, prefixed `[SC $amount]`) are both surfaced. Each yielded entry carries a flat `message` string plus an `emotes` list without positional info; `_tokenize` splits `message` against emote names to reconstruct an ordered `runs` tuple. The synchronous poller iterator is bridged to asyncio with `run_in_executor` per `next()`. `messages()` wraps both the chat-open *and* the per-`next()` polling in an outer reconnect loop: an **offline error** (HTTP 400 from `get_live_chat`, meaning the channel has no active stream yet — detected by `_is_offline_error`) is not fatal. Instead of returning, it emits a short `system` message (`_fmt_error` rewrites the raw 400 to `"no active stream (channel may be offline)"` rather than dumping YouTube's verbose error URL), sleeps `_RECONNECT_SLEEP_S` (30 s), and re-opens the chat from scratch — so launching termchat before the stream goes live now waits and connects instead of giving up. Any *other* exception still emits a `system` message and returns (fatal). The iterator also ends naturally when a live broadcast stops (`entry is None` with no reconnect flag). `live_url` is exposed as a property for testing.
- Both providers expose a `from_env()` classmethod (`TWITCH_CHANNEL`/`TWITCH_OAUTH`, `YOUTUBE_CHANNEL`). These were originally added for the env-gated integration tests; those tests have since been removed (commit `b1c161b`), so `from_env()` is currently unexercised public surface (see ARCHREVIEW backlog item A5 — re-test or drop).
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
- `pytest-timeout` guards against hangs; `addopts = ["--tb=short"]`; `testpaths = ["tests"]`.
- The suite is now entirely unit tests covering parsing, mapping, and the cache/renderer with fixtures and injected fakes (`ImageSource`, `_HTTPClient`, `FakeProvider`). The env-gated integration tests that hit real Twitch/YouTube were removed (commit `b1c161b`) — `make test` runs offline with no creds.

## Development Approach

- Modern Python (3.12+): use `match`, `dataclass`, `TypeAlias`, `Protocol`, `asyncio.TaskGroup`, etc.
- Each provider lives in its own module under `providers/` and is registered in a central provider registry or passed explicitly at startup
- Keep providers stateless where possible; connection/session state belongs inside the provider class, not in global scope
