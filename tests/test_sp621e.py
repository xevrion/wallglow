import pytest

from wallglow import sp621e


def test_power_frames_match_uniled():
    assert sp621e.power(True) == bytes.fromhex("a0620101")
    assert sp621e.power(False) == bytes.fromhex("a0620100")


def test_effect_and_brightness_frames():
    assert sp621e.effect(sp621e.EFFECT_SOLID) == bytes.fromhex("a06301be")
    assert sp621e.brightness(0) == bytes.fromhex("a0660100")
    assert sp621e.brightness(255) == bytes.fromhex("a06601ff")


def test_color_frame_carries_rgb_and_level():
    assert sp621e.color((0xFF, 0x72, 0x72), 0x70) == bytes.fromhex("a06904ff727270")
    assert sp621e.color((0, 0, 0), 0) == bytes.fromhex("a0690400000000")
    assert sp621e.color((255, 255, 255)) == bytes.fromhex("a06904ffffffff")


@pytest.mark.parametrize("bad", [-1, 256, 1000])
def test_bytes_out_of_range_are_rejected(bad):
    with pytest.raises(ValueError):
        sp621e.brightness(bad)
    with pytest.raises(ValueError):
        sp621e.color((bad, 0, 0))


def test_state_query_frame():
    assert sp621e.state_query() == bytes.fromhex("a07000")
