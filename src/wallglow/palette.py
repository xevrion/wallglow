"""Turn a Noctalia ``colors.json`` into a colour the strip can show."""

from __future__ import annotations

import colorsys
import json
from pathlib import Path

Rgb = tuple[int, int, int]

ROLES = {
    "primary": "mPrimary",
    "secondary": "mSecondary",
    "tertiary": "mTertiary",
}

MODES = ("vivid", "raw")


def load(path: Path) -> dict[str, str]:
    return json.loads(path.expanduser().read_text())


def parse_hex(text: str) -> Rgb:
    digits = text.strip().lstrip("#")
    if len(digits) != 6:
        raise ValueError(f"expected rrggbb, got {text!r}")
    value = int(digits, 16)
    return (value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF)


def to_hex(rgb: Rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def vivid(rgb: Rgb) -> Rgb:
    """Same hue and saturation at mid lightness.

    Material palettes hand us tints (tone 80 in dark mode) or shades (tone 40
    in light mode). An LED strip renders a tint as whitish and a shade as dim,
    so we pull the lightness back to the middle, where the hue actually shows.
    """
    h, _l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, 0.5, s))


def pick(palette: dict[str, str], role: str = "primary", mode: str = "vivid") -> Rgb:
    key = ROLES[role]
    rgb = parse_hex(palette[key])
    return vivid(rgb) if mode == "vivid" else rgb


def lerp(start: Rgb, end: Rgb, t: float) -> Rgb:
    return tuple(round(a + (b - a) * t) for a, b in zip(start, end))


def steps(start: Rgb, end: Rgb, count: int) -> list[Rgb]:
    """``count`` colours moving from just after ``start`` to exactly ``end``."""
    if count < 1:
        raise ValueError("count must be at least 1")
    return [lerp(start, end, i / count) for i in range(1, count + 1)]
