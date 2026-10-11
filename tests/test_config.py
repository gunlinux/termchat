import argparse

from termchat.__main__ import _apply_config
from termchat.config import load_config


def _namespace(**overrides):
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


def test_load_config_missing_file_returns_empty(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg == {}


def test_load_config_reads_twitch_channel(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[twitch]\nchannel = "mychan"\n')
    cfg = load_config(cfg_file)
    assert cfg["twitch"]["channel"] == "mychan"


def test_load_config_reads_youtube_channel(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[youtube]\nchannel = "somechannel"\n')
    cfg = load_config(cfg_file)
    assert cfg["youtube"]["channel"] == "somechannel"


def test_apply_config_fills_missing_twitch(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[twitch]\nchannel = "fromconfig"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = _namespace()
    _apply_config(args)
    assert args.twitch == "fromconfig"


def test_apply_config_cli_flag_overrides_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[twitch]\nchannel = "fromconfig"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = _namespace(twitch="fromcli")
    _apply_config(args)
    assert args.twitch == "fromcli"


def test_apply_config_fills_youtube_channel(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[youtube]\nchannel = "somechannel"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = _namespace()
    _apply_config(args)
    assert args.youtube == "somechannel"


def test_apply_config_fills_amqp_from_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[amqp]\ndsn = "amqp://fromconfig"\nexchange = "chat"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = _namespace()
    _apply_config(args)
    assert args.amqp == "amqp://fromconfig"
    assert args.amqp_exchange == "chat"


def test_apply_config_amqp_cli_overrides_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[amqp]\ndsn = "amqp://fromconfig"\nexchange = "cfg-exchange"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = _namespace(amqp="amqp://fromcli", amqp_exchange="cli-exchange")
    _apply_config(args)
    assert args.amqp == "amqp://fromcli"
    assert args.amqp_exchange == "cli-exchange"
