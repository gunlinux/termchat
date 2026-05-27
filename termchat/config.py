import tomllib
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path.home() / ".config" / "termchat" / "config.toml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    resolved = path if path is not None else _DEFAULT_PATH
    if not resolved.exists():
        return {}
    with open(resolved, "rb") as f:
        return tomllib.load(f)
