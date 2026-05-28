import asyncio
import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Iterator, Protocol

import httpx

from termchat.domain.message import EmojiRun, Message, MessageRun, TextRun


class _HTTPClient(Protocol):
    """Minimal synchronous HTTP client surface the poller depends on.

    `httpx.Client` satisfies this structurally; tests pass a lightweight fake.
    """

    def get(self, url: str) -> Any: ...

    def post(self, url: str, *, json: Any = ...) -> Any: ...

    def close(self) -> None: ...


def _largest_image_url(images: list[dict[str, Any]] | None) -> str | None:
    if not images:
        return None
    sized = [img for img in images if img.get("url")]
    if not sized:
        return None
    sized.sort(
        key=lambda img: (img.get("width") or 0) * (img.get("height") or 0), reverse=True
    )
    return sized[0]["url"]


def _tokenize(message: str, emotes: list[dict[str, Any]]) -> tuple[MessageRun, ...]:
    if not emotes:
        return (TextRun(text=message),) if message else ()

    by_name: dict[str, dict[str, Any]] = {}
    for emote in emotes:
        name = emote.get("name")
        if isinstance(name, str) and name:
            by_name[name] = emote

    if not by_name:
        return (TextRun(text=message),) if message else ()

    names_sorted = sorted(by_name.keys(), key=len, reverse=True)
    pattern = re.compile("(" + "|".join(re.escape(n) for n in names_sorted) + ")")

    runs: list[MessageRun] = []
    for token in pattern.split(message):
        if not token:
            continue
        emote = by_name.get(token)
        if emote is None:
            runs.append(TextRun(text=token))
        else:
            runs.append(
                EmojiRun(
                    shortcut=token,
                    image_url=_largest_image_url(emote.get("images")),
                    is_custom=bool(emote.get("is_custom_emoji", False)),
                )
            )
    return tuple(runs)


def _map_entry(entry: dict[str, Any]) -> Message | None:
    text = entry.get("message") or ""
    if not text:
        return None

    author: Any = entry.get("author") or ""
    if isinstance(author, dict):
        author = author.get("name") or "unknown"

    ts_usec = entry.get("timestamp")
    ts = (
        datetime.fromtimestamp(ts_usec / 1_000_000, tz=timezone.utc)
        if ts_usec
        else datetime.now(timezone.utc)
    )

    emotes = entry.get("emotes") or []
    runs = _tokenize(str(text), emotes)

    return Message(
        id=entry.get("message_id") or str(uuid.uuid4()),
        author=str(author) or "unknown",
        text=str(text),
        timestamp=ts,
        platform="youtube",
        runs=runs,
    )


def _system_msg(text: str) -> Message:
    return Message(
        id=str(uuid.uuid4()),
        author="system",
        text=text,
        timestamp=datetime.now(timezone.utc),
        platform="system",
    )


def _next_or_none(it: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(it)
    except StopIteration:
        return None


# --- Native YouTube live chat poller (replaces unmaintained chat_downloader 0.2.8) ---

_YT_INITIAL_DATA_RE = (
    r'(?:window\s*\[\s*["\']ytInitialData["\']\s*\]|ytInitialData)\s*=\s*'
    r"({.+?})\s*;\s*(?:var\s+(?:meta|head)|</script|\n)"
)
_YT_CFG_RE = r"ytcfg\.set\s*\(\s*({.+?})\s*\)\s*;"
_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_LIVE_CHAT_API = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
_CONTINUATION_KEYS = (
    "invalidationContinuationData",
    "timedContinuationData",
    "reloadContinuationData",
    "liveChatReplayContinuationData",
)


class _YouTubeBootstrapError(RuntimeError):
    pass


def _walk(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _walk(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _walk(v, key)
            if r is not None:
                return r
    return None


def _extract_continuation(
    continuations: list[dict[str, Any]],
) -> tuple[str | None, float]:
    if not continuations:
        return None, 0.0
    cont = continuations[0]
    if not isinstance(cont, dict):
        return None, 0.0
    for key in _CONTINUATION_KEYS:
        data = cont.get(key)
        if isinstance(data, dict) and data.get("continuation"):
            timeout_ms = data.get("timeoutMs")
            sleep_s = float(timeout_ms) / 1000.0 if timeout_ms else 2.0
            sleep_s = max(1.0, min(10.0, sleep_s))
            return str(data["continuation"]), sleep_s
    return None, 0.0


def _extract_bootstrap(html: str) -> tuple[str, dict[str, Any], str]:
    m_cfg = re.search(_YT_CFG_RE, html)
    if not m_cfg:
        raise _YouTubeBootstrapError("Unable to parse initial video data")
    try:
        ytcfg = json.loads(m_cfg.group(1))
    except (ValueError, json.JSONDecodeError) as e:
        raise _YouTubeBootstrapError("Unable to parse initial video data") from e

    api_key = ytcfg.get("INNERTUBE_API_KEY")
    ctx = ytcfg.get("INNERTUBE_CONTEXT")
    if not api_key or not isinstance(ctx, dict):
        raise _YouTubeBootstrapError("Unable to parse initial video data")

    m_id = re.search(_YT_INITIAL_DATA_RE, html)
    if not m_id:
        raise _YouTubeBootstrapError("Unable to parse initial video data")
    try:
        yid = json.loads(m_id.group(1))
    except (ValueError, json.JSONDecodeError) as e:
        raise _YouTubeBootstrapError("Unable to parse initial video data") from e

    lcr = _walk(yid, "liveChatRenderer")
    if not isinstance(lcr, dict):
        raise _YouTubeBootstrapError("Unable to parse initial video data")
    continuation, _ = _extract_continuation(lcr.get("continuations") or [])
    if not continuation:
        raise _YouTubeBootstrapError("Unable to parse initial video data")
    return str(api_key), ctx, continuation


def _author_name(renderer: dict[str, Any]) -> str:
    name = renderer.get("authorName")
    if isinstance(name, dict):
        return str(name.get("simpleText") or "unknown")
    return str(name) if name else "unknown"


def _parse_ts(renderer: dict[str, Any]) -> int | None:
    ts = renderer.get("timestampUsec")
    if ts is None:
        return None
    try:
        return int(ts)
    except (ValueError, TypeError):
        return None


def _runs_to_flat_and_emotes(
    runs: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    parts: list[str] = []
    emotes_by_name: dict[str, dict[str, Any]] = {}
    for run in runs:
        if "text" in run:
            parts.append(str(run.get("text") or ""))
        elif "emoji" in run:
            emoji = run["emoji"] or {}
            shortcuts = emoji.get("shortcuts") or []
            name = shortcuts[0] if shortcuts else emoji.get("emojiId") or ""
            if not name:
                continue
            parts.append(name)
            if name not in emotes_by_name:
                thumbs = (emoji.get("image") or {}).get("thumbnails") or []
                emotes_by_name[name] = {
                    "name": name,
                    "images": thumbs,
                    "is_custom_emoji": bool(emoji.get("isCustomEmoji", False)),
                }
    return "".join(parts), list(emotes_by_name.values())


def _renderer_to_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    text_msg = item.get("liveChatTextMessageRenderer")
    if isinstance(text_msg, dict):
        runs = (text_msg.get("message") or {}).get("runs") or []
        text, emotes = _runs_to_flat_and_emotes(runs)
        if not text:
            return None
        return {
            "message_id": text_msg.get("id"),
            "author": {"name": _author_name(text_msg)},
            "message": text,
            "timestamp": _parse_ts(text_msg),
            "emotes": emotes,
        }
    paid_msg = item.get("liveChatPaidMessageRenderer")
    if isinstance(paid_msg, dict):
        runs = (paid_msg.get("message") or {}).get("runs") or []
        text, emotes = _runs_to_flat_and_emotes(runs)
        amount = (paid_msg.get("purchaseAmountText") or {}).get("simpleText") or ""
        prefix = f"[SC {amount}] " if amount else "[SC] "
        return {
            "message_id": paid_msg.get("id"),
            "author": {"name": _author_name(paid_msg)},
            "message": prefix + text,
            "timestamp": _parse_ts(paid_msg),
            "emotes": emotes,
        }
    return None


def _iter_action_entries(action: dict[str, Any]) -> Iterator[dict[str, Any]]:
    item = (action.get("addChatItemAction") or {}).get("item")
    if isinstance(item, dict):
        entry = _renderer_to_entry(item)
        if entry:
            yield entry
        return
    replay = (action.get("replayChatItemAction") or {}).get("actions") or []
    for inner in replay:
        if isinstance(inner, dict):
            yield from _iter_action_entries(inner)


class _YouTubeLiveChatPoller:
    _MAX_RETRIES = 3

    def __init__(
        self,
        watch_url: str,
        *,
        client: _HTTPClient | None = None,
        sleep: Any = None,
        stop: threading.Event | None = None,
    ) -> None:
        self._watch_url = watch_url
        self._client = client
        self._stop = stop if stop is not None else threading.Event()
        self._sleep = sleep if sleep is not None else self._default_sleep

    def _default_sleep(self, seconds: float) -> None:
        remaining = float(seconds)
        while remaining > 0 and not self._stop.is_set():
            time.sleep(min(0.1, remaining))
            remaining -= 0.1

    def __iter__(self) -> Iterator[dict[str, Any]]:
        client = self._client
        owns_client = client is None
        if owns_client:
            client = httpx.Client(
                timeout=15.0,
                headers={"User-Agent": _DEFAULT_UA},
                follow_redirects=True,
            )
        try:
            if self._stop.is_set():
                return
            html = self._fetch_watch(client)
            api_key, ctx, continuation = _extract_bootstrap(html)
            api_url = f"{_LIVE_CHAT_API}?key={api_key}&prettyPrint=false"
            while continuation and not self._stop.is_set():
                payload = self._post_chat(client, api_url, ctx, continuation)
                lcc = (payload.get("continuationContents") or {}).get(
                    "liveChatContinuation"
                )
                if not isinstance(lcc, dict):
                    return
                for action in lcc.get("actions") or []:
                    if isinstance(action, dict):
                        if self._stop.is_set():
                            return
                        yield from _iter_action_entries(action)
                continuation, sleep_s = _extract_continuation(
                    lcc.get("continuations") or []
                )
                if not continuation or self._stop.is_set():
                    return
                if sleep_s > 0:
                    self._sleep(sleep_s)
        finally:
            if owns_client:
                client.close()

    def _fetch_watch(self, client: _HTTPClient) -> str:
        resp = client.get(self._watch_url)
        resp.raise_for_status()
        return resp.text

    def _post_chat(
        self,
        client: _HTTPClient,
        api_url: str,
        ctx: dict[str, Any],
        continuation: str,
    ) -> dict[str, Any]:
        body = {"context": ctx, "continuation": continuation}
        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                resp = client.post(api_url, json=body)
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < self._MAX_RETRIES:
                        self._sleep(backoff)
                        backoff *= 2
                        continue
                resp.raise_for_status()
                return resp.json()
            except httpx.TransportError as e:
                last_exc = e
                if attempt < self._MAX_RETRIES:
                    self._sleep(backoff)
                    backoff *= 2
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("retries exhausted")


class YouTubeProvider:
    def __init__(self, channel: str) -> None:
        self._channel = channel.lstrip("@")

    @classmethod
    def from_env(cls) -> "YouTubeProvider":
        return cls(os.environ["YOUTUBE_CHANNEL"])

    @property
    def live_url(self) -> str:
        return f"https://www.youtube.com/@{self._channel}/live"

    async def messages(self) -> AsyncGenerator[Message, None]:
        loop = asyncio.get_running_loop()
        stop = threading.Event()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        startup_time = datetime.now(timezone.utc)
        try:
            try:
                chat = await loop.run_in_executor(
                    executor, lambda: self._open_chat(stop)
                )
            except Exception as e:
                yield _system_msg(f"[youtube] failed to open chat: {e}")
                return
            while True:
                try:
                    entry = await loop.run_in_executor(executor, _next_or_none, chat)
                except Exception as e:
                    yield _system_msg(f"[youtube] {e}")
                    return
                if entry is None:
                    return
                msg = _map_entry(entry)
                if msg and msg.timestamp >= startup_time:
                    yield msg
        finally:
            stop.set()
            executor.shutdown(wait=False, cancel_futures=True)

    def _resolve_video_url(self) -> str | None:
        req = urllib.request.Request(
            self.live_url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", resp.url)
                if m:
                    return f"https://www.youtube.com/watch?v={m.group(1)}"
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return None

        m = re.search(
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html
        )
        if m and "watch?v=" in m.group(1):
            vid = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", m.group(1))
            if vid:
                return f"https://www.youtube.com/watch?v={vid.group(1)}"

        m = re.search(
            r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', html
        )
        if m and "watch?v=" in m.group(1):
            vid = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", m.group(1))
            if vid:
                return f"https://www.youtube.com/watch?v={vid.group(1)}"

        m = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', html)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"

        return None

    def _open_chat(self, stop: threading.Event) -> Iterator[dict[str, Any]]:
        import sys

        url = self._resolve_video_url()
        if url is None:
            print(
                f"[youtube] could not resolve live video URL, trying {self.live_url}",
                file=sys.stderr,
            )
            url = self.live_url
        return iter(_YouTubeLiveChatPoller(url, stop=stop))
