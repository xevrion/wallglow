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

MODES = ("faithful", "vivid", "raw")


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


# An LED strip cannot show a colour that is too dark, too pale, or too washed
# out: it reads as off, as white, or as a dim smear. These bounds are the
# window inside which the hue actually shows, tuned by eye against the strip.
_MIN_LIGHT = 0.35
_MAX_LIGHT = 0.72
_MIN_SAT = 0.45
_GREY_SAT = 0.05


def faithful(rgb: Rgb) -> Rgb:
    """The palette colour as seen on screen, rescued only where a strip cannot show it.

    Material tones run pale in dark mode and dark in light mode. We keep the
    hue, lightness and saturation the palette chose, and clamp only the values
    that would vanish on LEDs, so the strip stays close to the screen. True
    greys are left alone rather than forced to a hue.
    """
    h, light, sat = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    if sat > _GREY_SAT:
        light = min(max(light, _MIN_LIGHT), _MAX_LIGHT)
        sat = max(sat, _MIN_SAT)
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, light, sat))


def vivid(rgb: Rgb) -> Rgb:
    """Same hue and saturation at mid lightness, for a bolder, less accurate look."""
    h, _light, sat = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, 0.5, sat))


_TRANSFORMS = {"faithful": faithful, "vivid": vivid, "raw": lambda rgb: rgb}


def pick(palette: dict[str, str], role: str = "primary", mode: str = "faithful") -> Rgb:
    rgb = parse_hex(palette[ROLES[role]])
    return _TRANSFORMS[mode](rgb)


def lerp(start: Rgb, end: Rgb, t: float) -> Rgb:
    return tuple(round(a + (b - a) * t) for a, b in zip(start, end))


def steps(start: Rgb, end: Rgb, count: int) -> list[Rgb]:
    """``count`` colours moving from just after ``start`` to exactly ``end``."""
    if count < 1:
        raise ValueError("count must be at least 1")
    return [lerp(start, end, i / count) for i in range(1, count + 1)]
