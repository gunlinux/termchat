import argparse

from termchat.config import load_config
from termchat.__main__ import _apply_config


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

    args = argparse.Namespace(twitch=None, youtube=None, demo=False, tui=False)
    _apply_config(args)
    assert args.twitch == "fromconfig"


def test_apply_config_cli_flag_overrides_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[twitch]\nchannel = "fromconfig"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = argparse.Namespace(twitch="fromcli", youtube=None, demo=False, tui=False)
    _apply_config(args)
    assert args.twitch == "fromcli"


def test_apply_config_fills_youtube_channel(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[youtube]\nchannel = "somechannel"\n')
    monkeypatch.setattr("termchat.config._DEFAULT_PATH", cfg_file)

    args = argparse.Namespace(twitch=None, youtube=None, demo=False, tui=False)
    _apply_config(args)
    assert args.youtube == "somechannel"
