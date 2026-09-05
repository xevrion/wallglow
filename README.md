# wallglow

Makes a BanlanX SP621E LED strip follow the colour palette that [Noctalia](https://noctalia.dev) derives from your wallpaper. Change the wallpaper, the shell recolours itself, and a moment later the strip fades to match.

## How it works

Noctalia writes its generated Material palette to `~/.config/noctalia/colors.json` and fires a *Color generation* hook once the file is complete. wallglow runs from that hook: it reads the palette, picks a colour, connects to the strip over Bluetooth Low Energy, and fades it there.

The strip speaks a small unencrypted protocol, a few bytes written to one GATT characteristic. The frames come from [UniLED](https://github.com/monty68/uniled)'s `banlanx2` implementation, which also covers the SP611E, SP617E and SP620E, so those should work as well. This was written against an SP621E.

### Which colour

Material palettes are made of tints and shades. In dark mode the primary is a pale tone such as `#c1c1ff`, which an LED strip shows as faintly blue white, and in light mode it is a dark tone that just looks dim. By default wallglow keeps the hue and saturation but moves the lightness to the middle, so the strip shows the colour the palette is actually about. Set `mode = "raw"` to send the exact hex instead.

## Install

Needs Python 3.11 or newer and BlueZ, which any recent Linux desktop has. With [uv](https://docs.astral.sh/uv/):

    uv tool install git+https://github.com/xevrion/wallglow

That puts a `wallglow` command in `~/.local/bin`.

## Usage

Close the banlanX app on your phone first. These controllers accept one Bluetooth connection at a time, and the app keeps it while open.

    wallglow scan            # find the controller and print its address
    wallglow set ff6600      # any rrggbb colour
    wallglow sync            # push the current Noctalia palette colour
    wallglow off
    wallglow status          # daemon connection and current colour

`sync` and `set` fade from the previous colour over a fraction of a second. Add `--no-fade` to jump, or `--dry-run` to print the frames without touching Bluetooth.

## Configuration

Optional, at `~/.config/wallglow/config.toml`. Every key has a default.

```toml
address = "AA:BB:CC:DD:EE:FF"   # from `wallglow scan`; skips discovery
role = "primary"                # primary | secondary | tertiary
mode = "vivid"                  # vivid | raw
brightness = 255                # 0..255
fade_ms = 400
step_ms = 50
palette = "~/.config/noctalia/colors.json"
```

## Instant changes: the daemon

Each `wallglow` run connects, writes, and disconnects, which costs about a second. That is fine for the odd manual change, but a wallpaper hook that fires it every time feels laggy, and rapid changes pile up because each waits for the last connection to finish.

The daemon fixes this the way the phone app does: it holds one Bluetooth connection open and applies each new colour the instant it arrives, in well under a fifth of a second. Run it once,

    wallglow daemon

and from then on `sync`, `set`, `on` and `off` talk to it over a local socket and return immediately, falling back to a direct connection only if it is not running. A colour that arrives mid-fade cancels the fade, so flipping through wallpapers quickly lands on the last one rather than playing every colour in between.

To keep it running across logins, install it as a user service. Create `~/.config/systemd/user/wallglow.service`:

```ini
[Unit]
Description=wallglow LED daemon
After=bluetooth.target

[Service]
ExecStart=%h/.local/bin/wallglow daemon
Restart=on-failure

[Install]
WantedBy=default.target
```

Then `systemctl --user enable --now wallglow`. Check it with `wallglow status`.

## Hooking into Noctalia

Open Noctalia's settings, go to Hooks, enable them, and set the *Color generation* hook to:

    ~/.local/bin/wallglow sync

The hook fires after every palette regeneration, so the strip also follows you when you pick a predefined scheme or flip dark mode. Setting `address` in the config is worth doing here, since it saves a discovery scan on every change.

## Credits

The protocol was reverse engineered by the authors of [monty68/uniled](https://github.com/monty68/uniled) and [phhusson/ha-banlanx](https://github.com/phhusson/ha-banlanx). wallglow only wires it to Noctalia.

## License

MIT
