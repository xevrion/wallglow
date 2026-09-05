"""Command line entry point.

Colour commands prefer a running daemon, which holds the Bluetooth connection
open so the change is instant. With no daemon they fall back to a one-shot
connection, which is correct but pays a connect on every call.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import logging
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from . import config, daemon, palette, sp621e

log = logging.getLogger("wallglow")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    try:
        cfg = config.load_config(args.config)
        if args.address:
            cfg = replace(cfg, address=args.address)
        return args.run(args, cfg)
    except (ValueError, OSError, ConnectionError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallglow", description="Sync a BanlanX SP621E LED strip with Noctalia's palette."
    )
    parser.add_argument(
        "--config", type=Path, default=config.CONFIG_PATH, help="config.toml to read"
    )
    parser.add_argument("--address", help="controller MAC, overrides the config file")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_apply_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true", help="print frames instead of sending")
        p.add_argument("--no-fade", action="store_true", help="jump straight to the colour")

    p = sub.add_parser("sync", help="push the current Noctalia palette colour")
    add_apply_flags(p)
    p.set_defaults(run=cmd_sync)

    p = sub.add_parser("set", help="push one colour, as rrggbb")
    p.add_argument("hex")
    add_apply_flags(p)
    p.set_defaults(run=cmd_set)

    p = sub.add_parser("on", help="power the strip on")
    p.set_defaults(run=cmd_on)

    p = sub.add_parser("off", help="power the strip off")
    p.set_defaults(run=cmd_off)

    p = sub.add_parser("mode", help=f"show or set the colour mode {palette.MODES}")
    p.add_argument("mode", nargs="?", choices=palette.MODES, help="omit to show the current mode")
    p.set_defaults(run=cmd_mode)

    p = sub.add_parser("role", help="show or set which palette colour to follow")
    p.add_argument(
        "role", nargs="?", choices=sorted(palette.ROLES), help="omit to show the current role"
    )
    p.set_defaults(run=cmd_role)

    p = sub.add_parser("status", help="show the daemon's connection and colour")
    p.set_defaults(run=cmd_status)

    p = sub.add_parser("daemon", help="run the keep-alive service in the foreground")
    p.set_defaults(run=cmd_daemon)

    p = sub.add_parser("scan", help="list BanlanX controllers in range")
    p.add_argument("--timeout", type=float, default=5.0)
    p.set_defaults(run=cmd_scan)
    return parser


def cmd_sync(args: argparse.Namespace, cfg: config.Config) -> int:
    if _via_daemon({"command": "sync", "fade": not args.no_fade}, args.dry_run):
        return 0
    try:
        colors = palette.load(cfg.palette)
        target = palette.pick(colors, cfg.role, cfg.mode)
    except KeyError as exc:
        raise ValueError(f"{cfg.palette} has no {exc} entry") from exc
    return asyncio.run(_apply_oneshot(cfg, target, dry_run=args.dry_run, fade=not args.no_fade))


def cmd_set(args: argparse.Namespace, cfg: config.Config) -> int:
    target = palette.parse_hex(args.hex)
    request = {"command": "color", "hex": palette.to_hex(target), "fade": not args.no_fade}
    if _via_daemon(request, args.dry_run):
        return 0
    return asyncio.run(_apply_oneshot(cfg, target, dry_run=args.dry_run, fade=not args.no_fade))


def cmd_on(_args: argparse.Namespace, cfg: config.Config) -> int:
    if _via_daemon({"command": "power", "on": True}):
        return 0
    return asyncio.run(_send_oneshot(cfg, [sp621e.power(True)]))


def cmd_off(_args: argparse.Namespace, cfg: config.Config) -> int:
    if _via_daemon({"command": "power", "on": False}):
        return 0
    return asyncio.run(_send_oneshot(cfg, [sp621e.power(False)]))


def cmd_mode(args: argparse.Namespace, cfg: config.Config) -> int:
    return _show_or_set(args, cfg, "mode", args.mode, cfg.mode)


def cmd_role(args: argparse.Namespace, cfg: config.Config) -> int:
    return _show_or_set(args, cfg, "role", args.role, cfg.role)


def _show_or_set(
    args: argparse.Namespace, cfg: config.Config, key: str, new: str | None, current: str
) -> int:
    if new is None:
        log.info("%s is %s", key, current)
        return 0
    config.set_config_value(key, new, args.config)
    log.info("%s set to %s", key, new)
    # Apply it now so the change is visible without waiting for a wallpaper change.
    return cmd_sync(
        argparse.Namespace(dry_run=False, no_fade=False), config.load_config(args.config)
    )


def cmd_status(_args: argparse.Namespace, _cfg: config.Config) -> int:
    if not daemon.is_running():
        log.info("daemon not running")
        return 1
    reply = daemon.send_request({"command": "status"})
    where = "connected" if reply.get("connected") else "disconnected"
    power = "on" if reply.get("power") else "off"
    log.info("daemon %s, power %s, colour %s", where, power, reply.get("color") or "?")
    return 0


def cmd_daemon(_args: argparse.Namespace, cfg: config.Config) -> int:
    return asyncio.run(daemon.Daemon(cfg).run())


def cmd_scan(args: argparse.Namespace, _cfg: config.Config) -> int:
    found = asyncio.run(sp621e.scan(timeout=args.timeout))
    if not found:
        log.info("no BanlanX controller seen; is it powered on with the app closed?")
        return 1
    for dev, adv in found:
        print(f"{dev.address}  {dev.name or '?':12}  rssi {adv.rssi}")
    log.info('put address = "%s" in %s to skip discovery', found[0][0].address, config.CONFIG_PATH)
    return 0


def _via_daemon(request: dict, dry_run: bool = False) -> bool:
    """Send to the daemon if one is up. Returns True when it handled the request."""
    if dry_run or not daemon.is_running():
        return False
    try:
        reply = daemon.send_request(request)
    except (OSError, ValueError) as exc:
        log.debug("daemon unreachable (%s); falling back to a direct connection", exc)
        return False
    if not reply.get("ok"):
        raise ValueError(reply.get("error", "daemon rejected the request"))
    if reply.get("color"):
        log.info("%s (via daemon)", reply["color"])
    return True


async def _apply_oneshot(
    cfg: config.Config, target: palette.Rgb, *, dry_run: bool, fade: bool
) -> int:
    state = config.load_state()
    previous = palette.parse_hex(state["color"]) if fade and "color" in state else None
    ramp = (
        palette.steps(previous, target, cfg.fade_steps)
        if previous and previous != target
        else [target]
    )
    setup = [sp621e.power(True), sp621e.effect(sp621e.EFFECT_SOLID)]
    frames = [sp621e.color(rgb, cfg.brightness) for rgb in ramp]

    log.info("target %s (%d frames)", palette.to_hex(target), len(frames))
    if dry_run:
        for frame in setup + frames:
            print(frame.hex())
        return 0
    gap = cfg.step_ms / 1000 if len(frames) > 1 else 0.0
    address = await _connect_and_send(cfg, setup, frames, gap=gap)
    config.save_state({"color": palette.to_hex(target), "address": address})
    return 0


async def _send_oneshot(cfg: config.Config, frames: list[bytes]) -> int:
    await _connect_and_send(cfg, frames)
    return 0


async def _connect_and_send(cfg: config.Config, *batches: list[bytes], gap: float = 0.0) -> str:
    with _lock():
        device = await sp621e.find(cfg.address, timeout=sp621e.CONNECT_SCAN_TIMEOUT)
        if device is None:
            raise ConnectionError(
                "no SP621E found; is it powered on, in range, and is the banlanX app closed?"
            )
        log.debug("using %s at %s", device.name, device.address)
        async with sp621e.Strip(device) as strip:
            for i, frames in enumerate(batches):
                await strip.send(frames, gap=gap if i == len(batches) - 1 else 0.0, ack=i == 0)
    return device.address


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    # With no daemon, two hook invocations racing for the controller's single
    # connection both fail; the second waiting here costs a couple of seconds.
    path = config.STATE_PATH.with_name("lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


if __name__ == "__main__":
    sys.exit(main())
