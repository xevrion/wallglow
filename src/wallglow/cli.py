"""Command line entry point."""

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

from . import config, palette, sp621e

log = logging.getLogger("wallglow")

SCAN_TIMEOUT = 10.0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    try:
        cfg = config.load_config(args.config)
        if args.address:
            cfg = replace(cfg, address=args.address)
        return asyncio.run(args.run(args, cfg))
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

    p = sub.add_parser("scan", help="list BanlanX controllers in range")
    p.add_argument("--timeout", type=float, default=5.0)
    p.set_defaults(run=cmd_scan)
    return parser


async def cmd_sync(args: argparse.Namespace, cfg: config.Config) -> int:
    try:
        colors = palette.load(cfg.palette)
        target = palette.pick(colors, cfg.role, cfg.mode)
    except KeyError as exc:
        raise ValueError(f"{cfg.palette} has no {exc} entry") from exc
    return await apply(cfg, target, dry_run=args.dry_run, fade=not args.no_fade)


async def cmd_set(args: argparse.Namespace, cfg: config.Config) -> int:
    return await apply(
        cfg, palette.parse_hex(args.hex), dry_run=args.dry_run, fade=not args.no_fade
    )


async def cmd_on(_args: argparse.Namespace, cfg: config.Config) -> int:
    return await send(cfg, [sp621e.power(True)])


async def cmd_off(_args: argparse.Namespace, cfg: config.Config) -> int:
    return await send(cfg, [sp621e.power(False)])


async def cmd_scan(args: argparse.Namespace, _cfg: config.Config) -> int:
    found = await sp621e.scan(timeout=args.timeout)
    if not found:
        log.info("no BanlanX controller seen; is it powered on with the app closed?")
        return 1
    for dev, adv in found:
        print(f"{dev.address}  {dev.name or '?':12}  rssi {adv.rssi}")
    log.info('put address = "%s" in %s to skip discovery', found[0][0].address, config.CONFIG_PATH)
    return 0


async def apply(cfg: config.Config, target: palette.Rgb, *, dry_run: bool, fade: bool) -> int:
    state = config.load_state()
    previous = palette.parse_hex(state["color"]) if fade and "color" in state else None
    if previous is not None and previous != target:
        path = palette.steps(previous, target, cfg.fade_steps)
    else:
        path = [target]
    setup = [sp621e.power(True), sp621e.effect(sp621e.EFFECT_SOLID)]
    ramp = [sp621e.color(rgb, cfg.brightness) for rgb in path]

    log.info("target %s (%d frames)", palette.to_hex(target), len(ramp))
    if dry_run:
        for frame in setup + ramp:
            print(frame.hex())
        return 0

    gap = cfg.step_ms / 1000 if len(ramp) > 1 else 0.0
    address = await send(cfg, setup, ramp, gap=gap)
    config.save_state({"color": palette.to_hex(target), "address": address})
    return 0


async def send(cfg: config.Config, *batches: list[bytes], gap: float = 0.0) -> str:
    """Connect once, write each batch (the last one paced by ``gap``), return the address."""
    with _lock():
        device = await sp621e.find(cfg.address, timeout=SCAN_TIMEOUT)
        if device is None:
            raise ConnectionError(
                "no SP621E found; is it powered on, in range, and is the banlanX app closed?"
            )
        log.debug("using %s at %s", device.name, device.address)
        async with sp621e.Strip(device) as strip:
            for i, frames in enumerate(batches):
                await strip.send(frames, gap=gap if i == len(batches) - 1 else 0.0)
    return device.address


@contextlib.contextmanager
def _lock() -> Iterator[None]:
    # Two hook invocations racing for the controller's single connection both
    # fail; the second one waiting here costs a couple of seconds instead.
    path = config.STATE_PATH.with_name("lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


if __name__ == "__main__":
    sys.exit(main())
