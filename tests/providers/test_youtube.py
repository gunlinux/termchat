import json
import os
import urllib.error
from datetime import timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from termchat.domain.message import EmojiRun, TextRun
from termchat.providers.youtube import (
    YouTubeProvider,
    _YouTubeBootstrapError,
    _YouTubeLiveChatPoller,
    _extract_bootstrap,
    _extract_continuation,
    _iter_action_entries,
    _map_entry,
    _renderer_to_entry,
    _runs_to_flat_and_emotes,
    _tokenize,
)


# --- unit tests: entry mapping ---

def test_map_entry_basic():
    entry = {
        "message_id": "abc",
        "author": {"name": "viewer1"},
        "message": "Nice stream!",
        "timestamp": 1_700_000_000_000_000,
    }
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.id == "abc"
    assert msg.author == "viewer1"
    assert msg.text == "Nice stream!"
    assert msg.platform == "youtube"
    assert msg.timestamp.tzinfo is not None
    assert msg.runs == (TextRun(text="Nice stream!"),)


def test_map_entry_author_as_string():
    entry = {"author": "viewer2", "message": "Hello"}
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.author == "viewer2"


def test_map_entry_no_text_returns_none():
    entry = {"author": "user", "message": ""}
    assert _map_entry(entry) is None


def test_map_entry_missing_id_generates_uuid():
    entry = {"author": "a", "message": "hi"}
    msg = _map_entry(entry)
    assert msg is not None
    assert len(msg.id) > 0


def test_map_entry_no_timestamp_uses_now():
    entry = {"author": "a", "message": "hi"}
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.timestamp.tzinfo == timezone.utc


def test_map_entry_with_mixed_emotes():
    entry = {
        "message_id": "m1",
        "author": {"name": "alice"},
        "message": "hi :smile: friends :_custom_:!",
        "timestamp": 1_700_000_000_000_000,
        "emotes": [
            {
                "id": "U+1F600",
                "name": ":smile:",
                "shortcuts": [":smile:"],
                "images": [{"url": "https://yt/smile.png", "width": 24, "height": 24}],
                "is_custom_emoji": False,
            },
            {
                "id": "UCabc/custom1",
                "name": ":_custom_:",
                "shortcuts": [":_custom_:"],
                "images": [
                    {"url": "https://yt/custom_24.png", "width": 24, "height": 24},
                    {"url": "https://yt/custom_48.png", "width": 48, "height": 48},
                ],
                "is_custom_emoji": True,
            },
        ],
    }
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.runs == (
        TextRun(text="hi "),
        EmojiRun(shortcut=":smile:", image_url="https://yt/smile.png", is_custom=False),
        TextRun(text=" friends "),
        EmojiRun(shortcut=":_custom_:", image_url="https://yt/custom_48.png", is_custom=True),
        TextRun(text="!"),
    )


# --- _tokenize ---

def test_tokenize_without_emotes():
    assert _tokenize("plain text", []) == (TextRun(text="plain text"),)


def test_tokenize_emote_at_start():
    emotes = [
        {"name": ":wave:", "images": [{"url": "u", "width": 1, "height": 1}], "is_custom_emoji": False}
    ]
    runs = _tokenize(":wave: hello", emotes)
    assert runs == (
        EmojiRun(shortcut=":wave:", image_url="u", is_custom=False),
        TextRun(text=" hello"),
    )


def test_tokenize_emote_at_end():
    emotes = [
        {"name": ":wave:", "images": [{"url": "u", "width": 1, "height": 1}], "is_custom_emoji": False}
    ]
    runs = _tokenize("hi :wave:", emotes)
    assert runs == (
        TextRun(text="hi "),
        EmojiRun(shortcut=":wave:", image_url="u", is_custom=False),
    )


def test_tokenize_overlapping_names_picks_longest():
    # If two emote names overlap by prefix, the longer one wins
    emotes = [
        {"name": ":x:", "images": [{"url": "a", "width": 1, "height": 1}]},
        {"name": ":xyz:", "images": [{"url": "b", "width": 1, "height": 1}]},
    ]
    runs = _tokenize(":xyz: foo :x: bar", emotes)
    assert runs == (
        EmojiRun(shortcut=":xyz:", image_url="b", is_custom=False),
        TextRun(text=" foo "),
        EmojiRun(shortcut=":x:", image_url="a", is_custom=False),
        TextRun(text=" bar"),
    )


def test_tokenize_emote_without_images():
    emotes = [{"name": ":nopic:"}]
    runs = _tokenize(":nopic:", emotes)
    assert runs == (EmojiRun(shortcut=":nopic:", image_url=None, is_custom=False),)


# --- provider iteration ---

async def test_youtube_provider_maps_entries():
    entries = [
        {
            "message_id": "1",
            "author": {"name": "alice"},
            "message": "Hey",
            "timestamp": 1_700_000_000_000_000,
        },
        {"message_id": "2", "author": {"name": "bob"}, "message": "", "timestamp": None},
        {"message_id": "3", "author": {"name": "carol"}, "message": "Hi", "timestamp": None},
    ]
    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_open_chat", return_value=iter(entries)):
        msgs = [m async for m in provider.messages()]

    assert len(msgs) == 2  # empty text entry is skipped
    assert msgs[0].author == "alice"
    assert msgs[1].author == "carol"


async def test_youtube_provider_exits_when_chat_ends():
    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_open_chat", return_value=iter([])):
        msgs = [m async for m in provider.messages()]
    assert msgs == []


async def test_youtube_messages_yields_system_on_open_chat_failure():
    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_open_chat", side_effect=RuntimeError("Unable to parse initial video data")):
        msgs = [m async for m in provider.messages()]
    assert len(msgs) == 1
    assert msgs[0].platform == "system"
    assert "Unable to parse initial video data" in msgs[0].text


async def test_youtube_messages_yields_system_on_iteration_error():
    def _bad_iter():
        raise RuntimeError("chat gone")
        yield  # make it a generator

    provider = YouTubeProvider("somechannel")
    with patch.object(provider, "_open_chat", return_value=_bad_iter()):
        msgs = [m async for m in provider.messages()]
    assert len(msgs) == 1
    assert msgs[0].platform == "system"
    assert "chat gone" in msgs[0].text


def test_youtube_live_url_builds_from_channel():
    assert YouTubeProvider("somechannel").live_url == "https://www.youtube.com/@somechannel/live"


def test_youtube_live_url_strips_leading_at():
    assert YouTubeProvider("@somechannel").live_url == "https://www.youtube.com/@somechannel/live"


# --- _resolve_video_url ---

def _fake_urlopen(url_str: str, html: str):
    resp = MagicMock()
    resp.url = url_str
    resp.read.return_value = html.encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_resolve_video_url_from_canonical_link():
    html = '<link rel="canonical" href="https://www.youtube.com/watch?v=abcd1234567">'
    provider = YouTubeProvider("gunlinux")
    with patch("urllib.request.urlopen", return_value=_fake_urlopen("https://www.youtube.com/@gunlinux/live", html)):
        result = provider._resolve_video_url()
    assert result == "https://www.youtube.com/watch?v=abcd1234567"


def test_resolve_video_url_from_og_url():
    html = '<meta property="og:url" content="https://www.youtube.com/watch?v=abcd1234567">'
    provider = YouTubeProvider("gunlinux")
    with patch("urllib.request.urlopen", return_value=_fake_urlopen("https://www.youtube.com/@gunlinux/live", html)):
        result = provider._resolve_video_url()
    assert result == "https://www.youtube.com/watch?v=abcd1234567"


def test_resolve_video_url_from_redirect():
    html = ""
    provider = YouTubeProvider("gunlinux")
    with patch("urllib.request.urlopen", return_value=_fake_urlopen("https://www.youtube.com/watch?v=abcd1234567", html)):
        result = provider._resolve_video_url()
    assert result == "https://www.youtube.com/watch?v=abcd1234567"


def test_resolve_video_url_from_yt_initial_data():
    html = 'var ytInitialData = {"videoId":"abcd1234567","other":"stuff"};'
    provider = YouTubeProvider("gunlinux")
    with patch("urllib.request.urlopen", return_value=_fake_urlopen("https://www.youtube.com/@gunlinux/live", html)):
        result = provider._resolve_video_url()
    assert result == "https://www.youtube.com/watch?v=abcd1234567"


def test_resolve_video_url_no_live_stream():
    html = "<html><head><title>gunlinux - YouTube</title></head></html>"
    provider = YouTubeProvider("gunlinux")
    with patch("urllib.request.urlopen", return_value=_fake_urlopen("https://www.youtube.com/@gunlinux/live", html)):
        result = provider._resolve_video_url()
    assert result is None


def test_resolve_video_url_network_error():
    provider = YouTubeProvider("gunlinux")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
        result = provider._resolve_video_url()
    assert result is None


# --- _open_chat integration with the new poller ---

def test_open_chat_uses_resolved_url():
    provider = YouTubeProvider("gunlinux")
    resolved = "https://www.youtube.com/watch?v=abcd1234567"
    captured: dict = {}

    class _Poller:
        def __init__(self, url, **kwargs):
            captured["url"] = url

        def __iter__(self):
            return iter([])

    with patch.object(provider, "_resolve_video_url", return_value=resolved):
        with patch("termchat.providers.youtube._YouTubeLiveChatPoller", _Poller):
            import threading
            list(provider._open_chat(threading.Event()))
    assert captured["url"] == resolved


def test_open_chat_falls_back_to_live_url_when_resolve_fails():
    provider = YouTubeProvider("gunlinux")
    captured: dict = {}

    class _Poller:
        def __init__(self, url, **kwargs):
            captured["url"] = url

        def __iter__(self):
            return iter([])

    with patch.object(provider, "_resolve_video_url", return_value=None):
        with patch("termchat.providers.youtube._YouTubeLiveChatPoller", _Poller):
            import threading
            list(provider._open_chat(threading.Event()))
    assert captured["url"] == provider.live_url


def test_open_chat_logs_to_stderr_when_resolve_fails(capsys):
    provider = YouTubeProvider("gunlinux")

    class _Poller:
        def __init__(self, url, **kwargs): pass
        def __iter__(self): return iter([])

    with patch.object(provider, "_resolve_video_url", return_value=None):
        with patch("termchat.providers.youtube._YouTubeLiveChatPoller", _Poller):
            import threading
            list(provider._open_chat(threading.Event()))
    err = capsys.readouterr().err
    assert "gunlinux" in err


async def test_youtube_messages_surfaces_bootstrap_error_as_system_msg():
    provider = YouTubeProvider("gunlinux")

    class _Poller:
        def __init__(self, url, **kwargs): pass
        def __iter__(self):
            raise _YouTubeBootstrapError("Unable to parse initial video data")

    with patch.object(provider, "_resolve_video_url", return_value=None):
        with patch("termchat.providers.youtube._YouTubeLiveChatPoller", _Poller):
            msgs = [m async for m in provider.messages()]
    assert len(msgs) == 1
    assert msgs[0].platform == "system"
    assert "Unable to parse initial video data" in msgs[0].text


# --- _extract_bootstrap ---

def _bootstrap_html(api_key="K1", client_version="2.0", continuation="CONT0"):
    ytcfg = {
        "INNERTUBE_API_KEY": api_key,
        "INNERTUBE_CONTEXT": {"client": {"clientName": "WEB", "clientVersion": client_version}},
    }
    yt_initial = {
        "contents": {
            "twoColumnWatchNextResults": {
                "conversationBar": {
                    "liveChatRenderer": {
                        "continuations": [
                            {"invalidationContinuationData": {"continuation": continuation, "timeoutMs": 5000}}
                        ]
                    }
                }
            }
        }
    }
    return (
        f"<html><script>ytcfg.set({json.dumps(ytcfg)});"
        f"var ytInitialData = {json.dumps(yt_initial)};</script></html>"
    )


def test_extract_bootstrap_happy_path():
    html = _bootstrap_html(api_key="THE_KEY", continuation="TOKEN0")
    api_key, ctx, cont = _extract_bootstrap(html)
    assert api_key == "THE_KEY"
    assert ctx["client"]["clientName"] == "WEB"
    assert cont == "TOKEN0"


def test_extract_bootstrap_accepts_reload_continuation():
    ytcfg = {"INNERTUBE_API_KEY": "K", "INNERTUBE_CONTEXT": {"client": {}}}
    yt_initial = {"liveChatRenderer": {"continuations": [{"reloadContinuationData": {"continuation": "RC1"}}]}}
    html = f"<script>ytcfg.set({json.dumps(ytcfg)});ytInitialData = {json.dumps(yt_initial)};</script>"
    _, _, cont = _extract_bootstrap(html)
    assert cont == "RC1"


def test_extract_bootstrap_missing_ytcfg_raises():
    html = "<html>no ytcfg here</html>"
    with pytest.raises(_YouTubeBootstrapError):
        _extract_bootstrap(html)


def test_extract_bootstrap_missing_api_key_raises():
    ytcfg = {"INNERTUBE_CONTEXT": {"client": {}}}
    html = f"<script>ytcfg.set({json.dumps(ytcfg)});</script>"
    with pytest.raises(_YouTubeBootstrapError):
        _extract_bootstrap(html)


def test_extract_bootstrap_missing_continuation_raises():
    ytcfg = {"INNERTUBE_API_KEY": "K", "INNERTUBE_CONTEXT": {"client": {}}}
    yt_initial = {"contents": {"foo": "bar"}}
    html = f"<script>ytcfg.set({json.dumps(ytcfg)});ytInitialData = {json.dumps(yt_initial)};</script>"
    with pytest.raises(_YouTubeBootstrapError):
        _extract_bootstrap(html)


def test_extract_bootstrap_broken_live_chat_page_raises():
    # mirrors the symptom: ytInitialData assignment value isn't a JSON object
    html = (
        '<script>ytcfg.set({"INNERTUBE_API_KEY":"K","INNERTUBE_CONTEXT":{"client":{}}});'
        'window["ytInitialData"] = <!-- "> \'> -->;</script>'
    )
    with pytest.raises(_YouTubeBootstrapError):
        _extract_bootstrap(html)


# --- _extract_continuation ---

def test_extract_continuation_invalidation_variant():
    token, sleep_s = _extract_continuation(
        [{"invalidationContinuationData": {"continuation": "T1", "timeoutMs": 3000}}]
    )
    assert token == "T1"
    assert sleep_s == 3.0


def test_extract_continuation_timed_variant():
    token, sleep_s = _extract_continuation(
        [{"timedContinuationData": {"continuation": "T2", "timeoutMs": 7000}}]
    )
    assert token == "T2"
    assert sleep_s == 7.0


def test_extract_continuation_reload_variant():
    token, _ = _extract_continuation([{"reloadContinuationData": {"continuation": "T3"}}])
    assert token == "T3"


def test_extract_continuation_replay_variant():
    token, _ = _extract_continuation([{"liveChatReplayContinuationData": {"continuation": "T4"}}])
    assert token == "T4"


def test_extract_continuation_default_sleep_when_no_timeout():
    _, sleep_s = _extract_continuation([{"invalidationContinuationData": {"continuation": "T"}}])
    assert sleep_s == 2.0


def test_extract_continuation_clamps_high_timeout():
    _, sleep_s = _extract_continuation(
        [{"invalidationContinuationData": {"continuation": "T", "timeoutMs": 99999}}]
    )
    assert sleep_s == 10.0


def test_extract_continuation_clamps_low_timeout():
    _, sleep_s = _extract_continuation(
        [{"invalidationContinuationData": {"continuation": "T", "timeoutMs": 200}}]
    )
    assert sleep_s == 1.0


def test_extract_continuation_empty_returns_none():
    token, sleep_s = _extract_continuation([])
    assert token is None
    assert sleep_s == 0.0


def test_extract_continuation_unknown_key_returns_none():
    token, _ = _extract_continuation([{"mysteryContinuationData": {"continuation": "X"}}])
    assert token is None


# --- _runs_to_flat_and_emotes ---

def test_runs_to_flat_text_only():
    text, emotes = _runs_to_flat_and_emotes([{"text": "hello "}, {"text": "world"}])
    assert text == "hello world"
    assert emotes == []


def test_runs_to_flat_with_emoji():
    runs = [
        {"text": "hi "},
        {
            "emoji": {
                "emojiId": "1f600",
                "shortcuts": [":smile:"],
                "image": {"thumbnails": [{"url": "https://yt/s.png", "width": 24, "height": 24}]},
                "isCustomEmoji": False,
            }
        },
        {"text": "!"},
    ]
    text, emotes = _runs_to_flat_and_emotes(runs)
    assert text == "hi :smile:!"
    assert emotes == [
        {
            "name": ":smile:",
            "images": [{"url": "https://yt/s.png", "width": 24, "height": 24}],
            "is_custom_emoji": False,
        }
    ]


def test_runs_to_flat_with_custom_emoji():
    runs = [
        {
            "emoji": {
                "emojiId": "UCabc/x",
                "shortcuts": [":_dance_:"],
                "image": {
                    "thumbnails": [
                        {"url": "https://yt/x_24.png", "width": 24, "height": 24},
                        {"url": "https://yt/x_48.png", "width": 48, "height": 48},
                    ]
                },
                "isCustomEmoji": True,
            }
        }
    ]
    text, emotes = _runs_to_flat_and_emotes(runs)
    assert text == ":_dance_:"
    assert emotes[0]["is_custom_emoji"] is True
    assert len(emotes[0]["images"]) == 2


def test_runs_to_flat_dedups_repeated_emoji():
    e = {
        "shortcuts": [":x:"],
        "image": {"thumbnails": [{"url": "u", "width": 1, "height": 1}]},
        "isCustomEmoji": False,
    }
    text, emotes = _runs_to_flat_and_emotes([{"emoji": e}, {"text": " "}, {"emoji": e}])
    assert text == ":x: :x:"
    assert len(emotes) == 1


# --- _renderer_to_entry ---

def test_renderer_to_entry_text_message():
    item = {
        "liveChatTextMessageRenderer": {
            "id": "m1",
            "authorName": {"simpleText": "alice"},
            "timestampUsec": "1700000000000000",
            "message": {"runs": [{"text": "hello"}]},
        }
    }
    entry = _renderer_to_entry(item)
    assert entry == {
        "message_id": "m1",
        "author": {"name": "alice"},
        "message": "hello",
        "timestamp": 1_700_000_000_000_000,
        "emotes": [],
    }


def test_renderer_to_entry_text_message_with_emoji_roundtrips_through_map_entry():
    item = {
        "liveChatTextMessageRenderer": {
            "id": "m2",
            "authorName": {"simpleText": "bob"},
            "timestampUsec": "1700000000000000",
            "message": {
                "runs": [
                    {"text": "hi "},
                    {
                        "emoji": {
                            "emojiId": "smile",
                            "shortcuts": [":smile:"],
                            "image": {"thumbnails": [{"url": "https://yt/s.png", "width": 24, "height": 24}]},
                            "isCustomEmoji": False,
                        }
                    },
                    {"text": "!"},
                ]
            },
        }
    }
    entry = _renderer_to_entry(item)
    assert entry is not None
    msg = _map_entry(entry)
    assert msg is not None
    assert msg.runs == (
        TextRun(text="hi "),
        EmojiRun(shortcut=":smile:", image_url="https://yt/s.png", is_custom=False),
        TextRun(text="!"),
    )


def test_renderer_to_entry_empty_text_returns_none():
    item = {
        "liveChatTextMessageRenderer": {
            "id": "m3",
            "authorName": {"simpleText": "alice"},
            "message": {"runs": []},
        }
    }
    assert _renderer_to_entry(item) is None


def test_renderer_to_entry_paid_message_prefixes_amount():
    item = {
        "liveChatPaidMessageRenderer": {
            "id": "p1",
            "authorName": {"simpleText": "patron"},
            "timestampUsec": "1700000000000000",
            "purchaseAmountText": {"simpleText": "$5.00"},
            "message": {"runs": [{"text": "thanks!"}]},
        }
    }
    entry = _renderer_to_entry(item)
    assert entry is not None
    assert entry["message"] == "[SC $5.00] thanks!"
    assert entry["author"] == {"name": "patron"}


def test_renderer_to_entry_skips_unknown_renderers():
    item = {"liveChatViewerEngagementMessageRenderer": {"id": "x"}}
    assert _renderer_to_entry(item) is None


# --- _iter_action_entries ---

def test_iter_action_entries_addchat_action():
    action = {
        "addChatItemAction": {
            "item": {
                "liveChatTextMessageRenderer": {
                    "id": "m1",
                    "authorName": {"simpleText": "a"},
                    "message": {"runs": [{"text": "hi"}]},
                }
            }
        }
    }
    entries = list(_iter_action_entries(action))
    assert len(entries) == 1
    assert entries[0]["message"] == "hi"


def test_iter_action_entries_unwraps_replay():
    action = {
        "replayChatItemAction": {
            "actions": [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatTextMessageRenderer": {
                                "id": "m1",
                                "authorName": {"simpleText": "a"},
                                "message": {"runs": [{"text": "replayed"}]},
                            }
                        }
                    }
                }
            ]
        }
    }
    entries = list(_iter_action_entries(action))
    assert len(entries) == 1
    assert entries[0]["message"] == "replayed"


def test_iter_action_entries_ignores_unknown_action():
    assert list(_iter_action_entries({"markChatItemAsDeletedAction": {}})) == []


# --- _YouTubeLiveChatPoller ---

class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )


class _FakeClient:
    def __init__(self, watch_html="", post_responses=None):
        self._watch_html = watch_html
        self._post_responses = list(post_responses or [])
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    def get(self, url, **_):
        self.get_calls.append(url)
        return _FakeResponse(200, text=self._watch_html)

    def post(self, url, *, json=None, **_):
        self.post_calls.append((url, json or {}))
        if not self._post_responses:
            return _FakeResponse(200, json_data={})
        return self._post_responses.pop(0)

    def close(self):
        pass


def _innertube_action_response(continuation: str | None, messages: list[tuple[str, str]]):
    actions = [
        {
            "addChatItemAction": {
                "item": {
                    "liveChatTextMessageRenderer": {
                        "id": f"m{i}",
                        "authorName": {"simpleText": author},
                        "timestampUsec": "1700000000000000",
                        "message": {"runs": [{"text": text}]},
                    }
                }
            }
        }
        for i, (author, text) in enumerate(messages)
    ]
    lcc: dict = {"actions": actions}
    if continuation:
        lcc["continuations"] = [
            {"invalidationContinuationData": {"continuation": continuation, "timeoutMs": 1000}}
        ]
    else:
        lcc["continuations"] = []
    return _FakeResponse(200, json_data={"continuationContents": {"liveChatContinuation": lcc}})


def test_poller_yields_messages_and_advances_continuation():
    client = _FakeClient(
        watch_html=_bootstrap_html(api_key="K1", continuation="C0"),
        post_responses=[
            _innertube_action_response("C1", [("alice", "hi")]),
            _innertube_action_response("C2", [("bob", "yo"), ("carol", "hey")]),
            _innertube_action_response(None, []),
        ],
    )
    sleeps: list[float] = []
    poller = _YouTubeLiveChatPoller(
        "https://www.youtube.com/watch?v=test", client=client, sleep=sleeps.append
    )
    entries = list(poller)
    assert [e["message"] for e in entries] == ["hi", "yo", "hey"]
    assert [e["author"]["name"] for e in entries] == ["alice", "bob", "carol"]
    # initial continuation from /watch is C0, then advances C1, then C2
    assert [body["continuation"] for _, body in client.post_calls] == ["C0", "C1", "C2"]
    # api key on URL
    for url, _ in client.post_calls:
        assert "key=K1" in url
        assert "prettyPrint=false" in url
    # only sleeps between iterations that returned a next continuation (twice: after C0 and C1 responses)
    assert sleeps == [1.0, 1.0]


def test_poller_stops_when_no_continuation():
    client = _FakeClient(
        watch_html=_bootstrap_html(continuation="C0"),
        post_responses=[_innertube_action_response(None, [("a", "bye")])],
    )
    entries = list(_YouTubeLiveChatPoller("u", client=client, sleep=lambda _: None))
    assert len(entries) == 1
    assert len(client.post_calls) == 1


def test_poller_retries_on_429_then_succeeds():
    client = _FakeClient(
        watch_html=_bootstrap_html(continuation="C0"),
        post_responses=[
            _FakeResponse(429),
            _innertube_action_response(None, [("a", "ok")]),
        ],
    )
    sleeps: list[float] = []
    entries = list(_YouTubeLiveChatPoller("u", client=client, sleep=sleeps.append))
    assert len(entries) == 1
    assert len(client.post_calls) == 2
    # one backoff sleep before the retry
    assert sleeps[0] == 1.0


def test_poller_raises_after_max_429s():
    client = _FakeClient(
        watch_html=_bootstrap_html(continuation="C0"),
        post_responses=[_FakeResponse(429) for _ in range(10)],
    )
    with pytest.raises(httpx.HTTPStatusError):
        list(_YouTubeLiveChatPoller("u", client=client, sleep=lambda _: None))


def test_poller_raises_bootstrap_error_on_broken_page():
    client = _FakeClient(watch_html="<html>nothing useful here</html>")
    with pytest.raises(_YouTubeBootstrapError):
        list(_YouTubeLiveChatPoller("u", client=client, sleep=lambda _: None))


def test_poller_handles_missing_liveChatContinuation():
    client = _FakeClient(
        watch_html=_bootstrap_html(continuation="C0"),
        post_responses=[_FakeResponse(200, json_data={"continuationContents": {}})],
    )
    entries = list(_YouTubeLiveChatPoller("u", client=client, sleep=lambda _: None))
    assert entries == []


# --- integration test: requires env var ---

@pytest.mark.skipif(
    not os.getenv("YOUTUBE_CHANNEL"),
    reason="YOUTUBE_CHANNEL not set",
)
async def test_youtube_integration():
    provider = YouTubeProvider.from_env()
    received = []
    async for msg in provider.messages():
        received.append(msg)
        if len(received) >= 1:
            break
    assert len(received) >= 1
    assert received[0].platform == "youtube"
