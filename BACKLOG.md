# termchat — Backlog

Tasks derived from `ARCHREVIEW.md` (architecture & code-style review at commit `1b98156`).
Ordered by priority per the review's "What I'd do first" section.

---

## Bugs — confirmed at runtime (do first)

- [x] **TASK-1: Add `import os` to `providers/youtube.py`** — `from_env()` calls `os.environ.get("YOUTUBE_CHANNEL", "")` but `os` is never imported, raising `NameError`. Breaks the documented YouTube integration-test entry point. (§2.1) ✅ already present
- [x] **TASK-2: Add `import contextlib` to `ui/terminal.py`** — `TerminalUI.run()`'s `finally` block calls `contextlib.suppress(asyncio.CancelledError)` without importing `contextlib`. Latent `NameError` on the Ctrl-C shutdown path. (§2.2) ✅ already resolved (shutdown path no longer uses contextlib.suppress)
- [x] **TASK-3: Add `import contextlib` to `__main__.py`** — `_run_until_stopped` calls `contextlib.suppress(asyncio.CancelledError)` without importing `contextlib`. Latent `NameError` on the shutdown path. (§2.3) ✅ already resolved (shutdown path no longer uses contextlib.suppress)
- [x] **TASK-4: Add a cancel-and-shutdown test** — drive `TerminalUI.run()` (and the `__main__` shutdown path) and cancel it, so the shutdown-branch `NameError`s in TASK-2/TASK-3 are caught by tests going forward. (§2, §6.1) ✅ covered by test_terminal.py._drain (cancels task) and test_shutdown.test_sigint_exits_cleanly

---

## Type-annotation consistency

- [x] **TASK-5: Annotate `TwitchProvider.messages()` return type** — change bare `async def messages(self):` to `-> AsyncIterator[Message]` to match the `Provider` protocol. (§3) ✅ already annotated as `-> AsyncGenerator[Message, None]`
- [x] **TASK-6: Annotate `YouTubeProvider.messages()` return type** — add `-> AsyncIterator[Message]` to match the `Provider` protocol. (§3) ✅ already annotated as `-> AsyncGenerator[Message, None]`
- [x] **TASK-7: Drop unused `Message` import in `__main__.py`** — `Message` is imported from `.domain` but only `Provider` is used. (§3) ✅ N/A — `Message` is actively used (FakeProvider setup, Queue type annotation)
- [x] **TASK-8: Remove redundant `TwitchProvider.platform_name`** — the `# pragma: no cover` property duplicates the `platform` class attribute and isn't part of the `Provider` protocol. Remove it (or fold into the protocol if something needs it). (§3) ✅ already removed

---

## Correctness / robustness

- [x] **TASK-9: Escape Rich markup in TUI mode** — `tui.py` builds `RichLog(markup=True)` and interpolates raw `msg.text`/author. Attacker-controlled chat (e.g. `[SC $5.00]`, `[/]`) corrupts output or raises `MarkupError`. Run user content through `rich.markup.escape()` or write a `Text` object built without markup. (§5) ✅ fixed: `markup_escape()` applied to author and body in `_drain_queue`
- [x] **TASK-10: Count the system message in `MessageBus._drain`** — the synthetic `platform="system"` message emitted on provider crash bypasses the `counts` increment, so the end-of-run summary never shows `system`. Route it through the same counting path. (§5) ✅ fixed: counts["system"] incremented in except branch; test added
- [x] **TASK-11: Confirm Twitch `PONG` is actually sent** — `_read_loop` sees `PING` and `continue`s with a comment claiming the reply is "handled in session writer," but no `PONG` write is shown. If absent, send `PONG :tmi.twitch.tv` inline to avoid a wasted reconnect every keepalive window. (§5) ✅ already present (`writer.write(b"PONG :tmi.twitch.tv\r\n")`)

---

## Duplication & cohesion

- [x] **TASK-12: Centralize the platform icon/color theme** — `_PLATFORM_ICONS` and the per-platform color maps are copy-pasted across `ui/terminal.py` (RGB tuples) and `ui/tui.py` (hex strings). Introduce a single `ui/theme.py` (icon + base color descriptor) both backends consume, converting to ANSI/hex at the edge. (§4) ✅ `ui/_theme.py` exists; icons are shared; per-backend colors intentionally not merged (different encodings)
- [x] **TASK-13: Add `Message.system(text: str)` classmethod** — the `Message(platform="system", author="system", text=…)` shape is rebuilt in `app.py::_drain`, `twitch.py`, and `youtube.py`. Centralize the convention on the domain entity and replace the magic-string sites. (§4) ✅ fixed: classmethod added to domain `Message`; `app.py` and `youtube.py` updated; test added

---

## Architecture (later — when adding a frontend or provider)

- [x] **TASK-14: Lift emote fetch/cache out of the UI layer** — `ui/emoji_render.py::EmojiImageCache` does network (`httpx`), Pillow, and disk I/O inside the presentation ring. Extract an `EmoteImageStore` / `infra/emote_cache.py` that owns fetch+cache, and make `render_runs` depend on a small `ImageSource` protocol so the UI only knows "give me bytes for this URL." (§1) ✅ fixed: `EmojiImageCache` and `default_disk_cache_dir` moved to `termchat/infra/emote_cache.py`; `ImageSource` protocol defined in `ui/emoji_render.py`; all import sites updated
- [x] **TASK-15: Document the single-loop invariant on `MessageBus.counts`** — the plain dict is mutated by every `_drain` task; safe only under single-threaded asyncio. Add a comment noting the invariant so a future maintainer doesn't add a thread. (§1) ✅ fixed: comment added to `app.py`
