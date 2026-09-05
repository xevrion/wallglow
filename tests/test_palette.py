import json

import pytest

from wallglow import palette


@pytest.mark.parametrize("text", ["#c1c1ff", "c1c1ff", " #C1C1FF "])
def test_parse_hex_accepts_common_forms(text):
    assert palette.parse_hex(text) == (0xC1, 0xC1, 0xFF)


@pytest.mark.parametrize("text", ["#fff", "#c1c1ffaa", "", "zzzzzz"])
def test_parse_hex_rejects_other_shapes(text):
    with pytest.raises(ValueError):
        palette.parse_hex(text)


def test_hex_round_trip():
    assert palette.to_hex(palette.parse_hex("#0a1b2c")) == "#0a1b2c"


def test_vivid_pulls_a_tint_to_the_pure_hue():
    assert palette.vivid((0xC1, 0xC1, 0xFF)) == (0, 0, 255)


def test_vivid_pulls_a_shade_up_too():
    assert palette.vivid((0x40, 0x00, 0x00)) == (255, 0, 0)


def test_vivid_leaves_grey_grey():
    assert palette.vivid((0x80, 0x80, 0x80)) == (128, 128, 128)
    assert palette.vivid((0, 0, 0)) == (128, 128, 128)


def test_vivid_keeps_a_muted_colour_muted():
    rgb = palette.vivid((0xC6, 0xC4, 0xDD))
    assert max(rgb) == rgb[2]
    assert max(rgb) - min(rgb) < 100


def test_pick_by_role_and_mode(tmp_path):
    colors = {"mPrimary": "#c1c1ff", "mSecondary": "#c6c4dd", "mTertiary": "#e9b9d3"}
    path = tmp_path / "colors.json"
    path.write_text(json.dumps(colors))
    loaded = palette.load(path)
    assert palette.pick(loaded, "primary", "raw") == (0xC1, 0xC1, 0xFF)
    assert palette.pick(loaded, "primary", "vivid") == (0, 0, 255)
    assert palette.pick(loaded, "tertiary", "raw") == (0xE9, 0xB9, 0xD3)
    assert palette.pick(loaded, "primary") == palette.faithful((0xC1, 0xC1, 0xFF))


def test_faithful_preserves_hue_of_a_peach():
    import colorsys

    src = (0xFF, 0xB4, 0xA3)
    out = palette.faithful(src)
    h_src = colorsys.rgb_to_hls(*(c / 255 for c in src))[0]
    h_out = colorsys.rgb_to_hls(*(c / 255 for c in out))[0]
    assert abs(h_src - h_out) < 0.01
    # stays a warm peach, not pushed to pure red-orange like vivid does
    assert out != palette.vivid(src)
    assert out[0] > out[1] > out[2]


def test_faithful_leaves_an_already_good_colour_untouched():
    assert palette.faithful((0xDB, 0xC5, 0x8C)) == (0xDB, 0xC5, 0x8C)


def test_faithful_lifts_a_too_dark_colour():
    r, g, b = palette.faithful((0x0B, 0x1A, 0x3A))
    assert max(r, g, b) > 0x3A


def test_faithful_leaves_grey_grey():
    assert palette.faithful((0x8A, 0x8A, 0x8A)) == (0x8A, 0x8A, 0x8A)


def test_pick_unknown_role():
    with pytest.raises(KeyError):
        palette.pick({"mPrimary": "#000000"}, "accent")


def test_steps_end_exactly_on_target():
    start, end = (0, 0, 0), (255, 128, 1)
    assert palette.steps(start, end, 1) == [end]
    path = palette.steps(start, end, 25)
    assert len(path) == 25
    assert path[-1] == end
    assert path[0] != start
    reds = [c[0] for c in path]
    assert reds == sorted(reds)


def test_steps_rejects_zero():
    with pytest.raises(ValueError):
        palette.steps((0, 0, 0), (1, 1, 1), 0)
