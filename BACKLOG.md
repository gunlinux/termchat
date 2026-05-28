# termchat — Backlog

Tasks derived from `REVIEW.md` (style/idiom/consistency review at commit `1b98156`).
None are bugs; ordered by leverage. Each item is independently shippable.

---

## High value

### TASK-1: Add ruff as dev dependency and configure it ✅ DONE
- Run `uv add --dev ruff`.
- Add to `pyproject.toml`:
  ```toml
  [tool.ruff]
  target-version = "py312"
  line-length = 100

  [tool.ruff.lint]
  select = ["E", "F", "I", "UP", "B"]
  ```
- Run `uv run ruff check .` and `uv run ruff format .`; fix or `# noqa` remaining issues.
- Fix the 14 lines exceeding the chosen line length.
- This mechanically resolves TASK-2 and TASK-5 via `--select UP`.

### TASK-2: Replace deprecated `typing` ABC aliases with `collections.abc` ✅ DONE
Move `AsyncGenerator` / `Iterator` / `Iterable` imports to `collections.abc`:
- `termchat/domain/provider.py:1` — `AsyncGenerator`
- `termchat/providers/fake.py:2` — `AsyncGenerator`
- `termchat/providers/twitch.py:7` — `AsyncGenerator`
- `termchat/providers/youtube.py:11` — `AsyncGenerator`, `Iterator`
- `termchat/ui/emoji_render.py:18` — `Iterable`

Leave `Literal`, `Any`, `Protocol` in `typing`.

---

## Medium value — consistency

### TASK-3: Make `from __future__ import annotations` consistent ✅ DONE (Option B)
Chose **Option B** per user (project targets Python 3.12, no need for the
future import): removed `from __future__ import annotations` from
`ui/emoji_render.py` and `providers/twitch_emotes.py`. String-quoted forward
refs (`twitch.py`, `youtube.py`) left intact since they still resolve fine.

### TASK-4: De-duplicate `_PLATFORM_ICONS` ✅ DONE
Identical dict defined in `ui/terminal.py:12` and `ui/tui.py:10`.
- Create `termchat/ui/_theme.py` holding `_PLATFORM_ICONS` (and optionally the
  per-platform color maps in a backend-neutral form).
- Import it in both `terminal.py` and `tui.py` so they can't drift.

### TASK-5: Use 3.12 `type` statement for `MessageRun` ✅ DONE
- `termchat/domain/message.py:17`: change
  `MessageRun = TextRun | EmojiRun` → `type MessageRun = TextRun | EmojiRun`.

### TASK-6: Hoist unnecessary lazy imports in `__main__.py` ✅ DONE
Move these stdlib imports to module top:
- `from datetime import datetime, timezone` (currently inside `if args.demo:` at `:53`)
- `import os` (inside `if args.twitch:` at `:65`)
- `from termchat.domain.provider import Provider` (inside `_run` at `:48`)
- `import sys` in `youtube.py:448` (inside `_open_chat`)

Keep the intentional lazy imports of heavy optional deps (`textual`, provider
modules `tui`/`twitch`/`youtube`) but add a one-line comment explaining why.

### TASK-7: Remove confusing `Message as Msg` alias ✅ DONE
- `termchat/__main__.py:54`: delete the `from termchat.domain.message import Message as Msg`
  import and use the `Message` already imported at `:7`. Update usages of `Msg`.

### TASK-8: Pre-compile regexes at module scope in `youtube.py` ✅ DONE
- Convert `_YT_CFG_RE`, `_YT_INITIAL_DATA_RE` from raw strings to `re.compile(...)`
  at module level (matching `twitch.py`'s `_PRIVMSG_RE` / `_ROOMSTATE_RE`).
- Lift the inline `r'[?&]v=...'` patterns in `_resolve_video_url` to compiled
  module-level constants.
- Update `_extract_bootstrap` and `_resolve_video_url` call sites.

### TASK-9: Fully parameterize container type hints in `twitch_emotes.py` ✅ DONE
- `:119` — `entries: list[dict]` → `list[dict[str, Any]]`
- `:180` — `entry: dict` → `dict[str, Any]`

---

## Low value — polish

### TASK-10: Narrow/document broad `except Exception` blocks
For best-effort network paths in `twitch_emotes.py` load methods,
`emoji_render.py` (`_fetch`, `_to_png_first_frame`), `youtube.py:_resolve_video_url`:
- Narrow to expected families where practical (`httpx.HTTPError`, `OSError`, `ValueError`).
- Where broad catch is kept, add a trailing `# best-effort: …` comment
  (model: the disk-cache degradation comment at `emoji_render.py:82`).

### TASK-11: Clarify `except (asyncio.CancelledError, Exception)` in `__main__.py:109` ✅ DONE
- Add a comment `# swallow cancellation during shutdown drain`, or split into
  two `except` clauses. Behavior unchanged.

### TASK-12: Introduce `logging`
- Add `logger = logging.getLogger(__name__)` to modules that emit diagnostics.
- Replace `print(..., file=sys.stderr)` in `youtube.py:452` with `logger.warning(...)`.
- Gives the silent `except` blocks (TASK-10) a `logger.debug(...)` outlet.

### TASK-13: Simplify `runs` default in `Message` ✅ DONE
- `termchat/domain/message.py:27`: replace `field(default_factory=tuple)` with
  `runs: tuple[MessageRun, ...] = ()` (safe — frozen dataclass, immutable tuple).

### TASK-14: Replace `assert` runtime invariant in `emoji_render.py:129` ✅ DONE
- `assert self._cache_dir is not None` is stripped under `python -O`.
- Either raise explicitly, or add `# narrowing: callers guarantee cache_dir set`
  if leaving as a type-narrowing aid.

### TASK-15: Add docstrings to public parsing entry points
One-liners for: `load_config`, `parse_privmsg`, `parse_roomstate`, `_map_entry`.

### TASK-16: Name the iTerm2 escape magic numbers ✅ DONE
- `emoji_render.py:247`: extract hardcoded `width=2;height=1` into a shared named
  constant (e.g. `_EMOJI_CELLS_W = 2`) used by both the Kitty and iTerm2 escape
  builders to keep them in sync.
