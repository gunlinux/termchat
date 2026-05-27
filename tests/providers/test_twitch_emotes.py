import httpx

from termchat.domain.message import EmojiRun, TextRun
from termchat.providers.twitch_emotes import EmoteInfo, TwitchEmoteRegistry


# --- tokenize ---

def test_tokenize_empty_returns_empty():
    r = TwitchEmoteRegistry()
    assert r.tokenize("") == ()


def test_tokenize_unknown_words_pass_through_as_text():
    r = TwitchEmoteRegistry()
    runs = r.tokenize("hello world")
    assert runs == (TextRun(text="hello world"),)


def test_tokenize_matches_whole_word():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="Kappa", image_url="u", source="bttv-channel"))
    runs = r.tokenize("hi Kappa friend")
    assert runs == (
        TextRun(text="hi "),
        EmojiRun(shortcut="Kappa", image_url="u", is_custom=True),
        TextRun(text=" friend"),
    )


def test_tokenize_case_sensitive():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="Kappa", image_url="u", source="bttv-channel"))
    runs = r.tokenize("hi kappa friend")
    assert runs == (TextRun(text="hi kappa friend"),)


def test_tokenize_punctuation_breaks_match():
    # "Kappa," is one whitespace-delimited token != "Kappa"; expected NOT to match.
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="Kappa", image_url="u", source="bttv-channel"))
    runs = r.tokenize("Kappa,")
    assert runs == (TextRun(text="Kappa,"),)


def test_tokenize_consecutive_emotes_keep_spacing():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="A", image_url="u1", source="bttv-channel"))
    r._add(EmoteInfo(name="B", image_url="u2", source="bttv-channel"))
    runs = r.tokenize("A B")
    assert runs == (
        EmojiRun(shortcut="A", image_url="u1", is_custom=True),
        TextRun(text=" "),
        EmojiRun(shortcut="B", image_url="u2", is_custom=True),
    )


def test_tokenize_preserves_leading_trailing_whitespace():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="Kappa", image_url="u", source="bttv-channel"))
    runs = r.tokenize("  Kappa  ")
    assert runs == (
        TextRun(text="  "),
        EmojiRun(shortcut="Kappa", image_url="u", is_custom=True),
        TextRun(text="  "),
    )


# --- precedence ---

def test_priority_channel_over_global():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="X", image_url="global", source="bttv-global"))
    r._add(EmoteInfo(name="X", image_url="channel", source="bttv-channel"))
    assert r.lookup("X").image_url == "channel"


def test_priority_global_does_not_override_channel():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="X", image_url="channel", source="bttv-channel"))
    r._add(EmoteInfo(name="X", image_url="global", source="bttv-global"))
    assert r.lookup("X").image_url == "channel"


def test_priority_bttv_channel_over_7tv_channel():
    r = TwitchEmoteRegistry()
    r._add(EmoteInfo(name="X", image_url="7tv", source="7tv-channel"))
    r._add(EmoteInfo(name="X", image_url="bttv", source="bttv-channel"))
    assert r.lookup("X").image_url == "bttv"


# --- HTTP loading shapes ---

class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url in self._responses:
            return httpx.Response(200, json=self._responses[url])
        return httpx.Response(404)


async def test_load_global_hits_bttv_global_endpoint():
    transport = _RecordingTransport({
        "https://api.betterttv.net/3/cached/emotes/global": [
            {"id": "abc", "code": "GlobeRound", "imageType": "png"},
            {"id": "def", "code": "GlobeSpin", "imageType": "gif"},
        ]
    })
    client = httpx.AsyncClient(transport=transport)
    r = TwitchEmoteRegistry(client=client)
    await r.load_global()
    assert r.lookup("GlobeRound") == EmoteInfo(
        name="GlobeRound",
        image_url="https://cdn.betterttv.net/emote/abc/2x",
        source="bttv-global",
    )
    assert r.lookup("GlobeSpin") is not None
    await r.aclose()


async def test_load_channel_hits_both_bttv_and_7tv():
    room_id = "12345"
    transport = _RecordingTransport({
        f"https://api.betterttv.net/3/cached/users/twitch/{room_id}": {
            "channelEmotes": [{"id": "ch1", "code": "ChanEmote"}],
            "sharedEmotes": [{"id": "sh1", "code": "SharedEmote"}],
        },
        f"https://7tv.io/v3/users/twitch/{room_id}": {
            "emote_set": {
                "emotes": [{"id": "stv1", "name": "PepoG"}]
            }
        },
    })
    client = httpx.AsyncClient(transport=transport)
    r = TwitchEmoteRegistry(client=client)
    await r.load_channel(room_id)
    assert r.lookup("ChanEmote") == EmoteInfo(
        name="ChanEmote",
        image_url="https://cdn.betterttv.net/emote/ch1/2x",
        source="bttv-channel",
    )
    assert r.lookup("SharedEmote") is not None
    assert r.lookup("PepoG") == EmoteInfo(
        name="PepoG",
        image_url="https://cdn.7tv.app/emote/stv1/2x.png",
        source="7tv-channel",
    )
    assert (
        f"https://api.betterttv.net/3/cached/users/twitch/{room_id}"
        in transport.calls
    )
    assert f"https://7tv.io/v3/users/twitch/{room_id}" in transport.calls
    await r.aclose()


async def test_load_global_silently_swallows_network_error():
    class _Boom(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

    client = httpx.AsyncClient(transport=_Boom())
    r = TwitchEmoteRegistry(client=client)
    await r.load_global()  # must not raise
    assert r.lookup("anything") is None
    await r.aclose()


async def test_load_channel_partial_failure_keeps_other_source():
    room_id = "9"

    class _Mixed(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "7tv" in str(request.url):
                raise httpx.ConnectError("7tv down")
            return httpx.Response(
                200,
                json={"channelEmotes": [{"id": "x", "code": "OnlyBTTV"}], "sharedEmotes": []},
            )

    client = httpx.AsyncClient(transport=_Mixed())
    r = TwitchEmoteRegistry(client=client)
    await r.load_channel(room_id)
    assert r.lookup("OnlyBTTV") is not None
    await r.aclose()
