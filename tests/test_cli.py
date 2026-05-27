import pytest

from termchat.__main__ import build_parser


def test_help_exits_cleanly(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--twitch" in out
    assert "--youtube" in out


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
