"""User configuration and the small bit of state kept between runs."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from . import palette


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


CONFIG_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "wallglow" / "config.toml"
STATE_PATH = _xdg("XDG_CACHE_HOME", ".cache") / "wallglow" / "state.json"
DEFAULT_PALETTE = _xdg("XDG_CONFIG_HOME", ".config") / "noctalia" / "colors.json"


@dataclass(frozen=True)
class Config:
    address: str | None = None
    role: str = "primary"
    mode: str = "faithful"
    brightness: int = 255
    fade_ms: int = 400
    step_ms: int = 50
    palette: Path = DEFAULT_PALETTE

    def __post_init__(self) -> None:
        if self.role not in palette.ROLES:
            raise ValueError(f"role must be one of {sorted(palette.ROLES)}, got {self.role!r}")
        if self.mode not in palette.MODES:
            raise ValueError(f"mode must be one of {palette.MODES}, got {self.mode!r}")
        if not 0 <= self.brightness <= 255:
            raise ValueError(f"brightness must be 0..255, got {self.brightness}")
        if self.fade_ms < 0 or self.step_ms <= 0:
            raise ValueError("fade_ms must be >= 0 and step_ms > 0")

    @property
    def fade_steps(self) -> int:
        return max(1, self.fade_ms // self.step_ms)


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    try:
        return load_config_from_text(path.read_text())
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def set_config_value(key: str, value: str, path: Path = CONFIG_PATH) -> None:
    """Set one string key in the TOML file, keeping the other lines and comments.

    Validated by reloading afterwards, so a bad value raises before it sticks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    entry = f'{key} = "{value}"'
    for i, line in enumerate(lines):
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith((f"{key} ", f"{key}=")):
            comment = line[line.index("#") :] if "#" in line else ""
            lines[i] = f"{entry}  {comment}".rstrip() if comment else entry
            break
    else:
        lines.append(entry)
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)  # syntax check
    load_config_from_text(text)  # value validation
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def load_config_from_text(text: str) -> Config:
    raw = tomllib.loads(text)
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown keys: {sorted(unknown)}")
    if "palette" in raw:
        raw["palette"] = Path(raw["palette"]).expanduser()
    return Config(**raw)


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)
