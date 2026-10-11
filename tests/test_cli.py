import argparse

import pytest

from termchat.__main__ import _build_providers, build_parser, main
from termchat.providers.amqp import AmqpProvider


def test_help_exits_cleanly(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--twitch" in out
    assert "--youtube" in out
    assert "--amqp" in out


def test_twitch_only():
    parser = build_parser()
    args = parser.parse_args(["--twitch", "mychannel"])
    assert args.twitch == "mychannel"
    assert args.youtube is None


def test_youtube_only():
    parser = build_parser()
    args = parser.parse_args(["--youtube", "https://youtube.com/watch?v=abc"])
    assert args.youtube == "https://youtube.com/watch?v=abc"
    assert args.twitch is None


def test_both_providers():
    parser = build_parser()
    args = parser.parse_args(["--twitch", "chan", "--youtube", "https://yt.com"])
    assert args.twitch == "chan"
    assert args.youtube == "https://yt.com"


def test_tui_flag():
    parser = build_parser()
    args = parser.parse_args(["--twitch", "chan", "--tui"])
    assert args.tui is True


def test_no_providers_both_none():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.twitch is None
    assert args.youtube is None


def test_amqp_flags_parse():
    parser = build_parser()
    args = parser.parse_args(["--amqp", "amqp://broker", "--amqp-exchange", "chat"])
    assert args.amqp == "amqp://broker"
    assert args.amqp_exchange == "chat"


def _args(**overrides):
    base = {
        "twitch": None,
        "youtube": None,
        "amqp": None,
        "amqp_exchange": None,
        "demo": False,
        "tui": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_build_providers_includes_amqp():
    providers = _build_providers(_args(amqp="amqp://broker", amqp_exchange="chat"))
    assert len(providers) == 1
    assert isinstance(providers[0], AmqpProvider)


def test_build_providers_none_without_flags():
    assert _build_providers(_args()) == []


def test_main_errors_when_amqp_missing_exchange(tmp_path, monkeypatch):
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", tmp_path / "none.toml")
    monkeypatch.setattr("sys.argv", ["termchat", "--amqp", "amqp://broker"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_main_errors_when_no_source(tmp_path, monkeypatch):
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", tmp_path / "none.toml")
    monkeypatch.setattr("sys.argv", ["termchat"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
