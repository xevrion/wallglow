import pytest

from wallglow import config


def test_defaults_when_no_file(tmp_path):
    cfg = config.load_config(tmp_path / "missing.toml")
    assert cfg.role == "primary"
    assert cfg.mode == "faithful"
    assert cfg.brightness == 255
    assert cfg.fade_steps == 8


def test_reads_and_expands_paths(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('address = "AA:BB:CC:DD:EE:FF"\nrole = "tertiary"\npalette = "~/x.json"\n')
    cfg = config.load_config(path)
    assert cfg.address == "AA:BB:CC:DD:EE:FF"
    assert cfg.role == "tertiary"
    assert not str(cfg.palette).startswith("~")


def test_unknown_key_is_an_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("colour = 3\n")
    with pytest.raises(ValueError, match="unknown keys"):
        config.load_config(path)


@pytest.mark.parametrize(
    "line", ["brightness = 256", 'role = "accent"', 'mode = "loud"', "step_ms = 0"]
)
def test_bad_values_are_rejected(tmp_path, line):
    path = tmp_path / "config.toml"
    path.write_text(line + "\n")
    with pytest.raises(ValueError):
        config.load_config(path)


def test_fade_steps_never_below_one(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("fade_ms = 0\n")
    assert config.load_config(path).fade_steps == 1


def test_state_round_trip_and_missing(tmp_path):
    path = tmp_path / "state.json"
    assert config.load_state(path) == {}
    config.save_state({"color": "#00ff00"}, path)
    assert config.load_state(path) == {"color": "#00ff00"}
