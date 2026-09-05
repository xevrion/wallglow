"""A long-lived service that keeps one connection to the strip warm.

The banlanX app feels instant because it never lets go of the Bluetooth
connection. This does the same: it connects once, then waits on a Unix socket
for colour requests and applies each the moment it arrives. The Noctalia hook
sends to this socket and returns immediately, so a wallpaper change no longer
pays for a connect. Rapid changes collapse to the latest colour rather than
queueing, because a fresh request replaces any fade already in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from bleak.exc import BleakError

from . import config, palette, sp621e

log = logging.getLogger("wallglow.daemon")

SOCKET_PATH = config.STATE_PATH.with_name("daemon.sock")
_RECONNECT_BACKOFF = (2, 5, 10, 20, 30)


class Daemon:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self._strip: sp621e.Strip | None = None
        self._current: palette.Rgb | None = None
        self._pending: palette.Rgb | None = None
        self._power = True
        self._wake = asyncio.Event()
        self._fade_task: asyncio.Task | None = None

    async def run(self) -> int:
        state = config.load_state()
        if "color" in state:
            with contextlib.suppress(ValueError):
                self._current = palette.parse_hex(state["color"])
        server = await self._listen()
        log.info("listening on %s", SOCKET_PATH)
        try:
            await asyncio.gather(self._maintain_connection(), server.serve_forever())
        finally:
            server.close()
            with contextlib.suppress(Exception):
                SOCKET_PATH.unlink()
            if self._strip is not None:
                await self._strip.disconnect()
        return 0

    async def _listen(self) -> asyncio.AbstractServer:
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            SOCKET_PATH.unlink()
        return await asyncio.start_unix_server(self._handle_client, path=str(SOCKET_PATH))

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            reply = self._dispatch(json.loads(line))
        except (ValueError, KeyError) as exc:
            reply = {"ok": False, "error": str(exc)}
        writer.write(json.dumps(reply).encode() + b"\n")
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()

    def _dispatch(self, request: dict) -> dict:
        command = request.get("command")
        if command == "status":
            return {
                "ok": True,
                "connected": self._strip is not None and self._strip.is_connected,
                "power": self._power,
                "color": palette.to_hex(self._current) if self._current else None,
                "mode": self.cfg.mode,
                "role": self.cfg.role,
                "address": self.cfg.address,
            }
        if command == "reconnect":
            # The maintenance loop reconnects on its own; nudging the wake event
            # is enough to make a serve_requests iteration notice a dropped link.
            self._wake.set()
            return {"ok": True, "connected": self._strip is not None and self._strip.is_connected}
        if command == "power":
            self._power = bool(request["on"])
            self._request(self._current if self._power else None)
            return {"ok": True}
        if command in ("color", "sync"):
            if command == "sync":
                # Reload config each time so a `wallglow mode`/`role` change takes
                # effect without restarting the daemon.
                self.cfg = config.load_config()
                colors = palette.load(self.cfg.palette)
                target = palette.pick(colors, self.cfg.role, self.cfg.mode)
            else:
                target = palette.parse_hex(request["hex"])
            self._power = True
            self._request(target, fade=request.get("fade", True))
            return {"ok": True, "color": palette.to_hex(target)}
        raise KeyError(f"unknown command: {command!r}")

    def _request(self, target: palette.Rgb | None, fade: bool = True) -> None:
        # A new request always wins: cancel the running fade so rapid wallpaper
        # changes collapse to the latest colour instead of playing every step.
        if self._fade_task is not None and not self._fade_task.done():
            self._fade_task.cancel()
        self._pending = target
        self._fade_wanted = fade
        self._wake.set()

    async def _maintain_connection(self) -> None:
        attempt = 0
        while True:
            try:
                self._strip = sp621e.Strip(await self._locate())
                await self._strip.ensure_connected()
                log.info("connected to %s", self._strip.address)
                attempt = 0
                await self._serve_requests()
            except (BleakError, ConnectionError, OSError) as exc:
                delay = _RECONNECT_BACKOFF[min(attempt, len(_RECONNECT_BACKOFF) - 1)]
                log.warning("%s; retrying in %ss", exc, delay)
                attempt += 1
                self._strip = None
                await asyncio.sleep(delay)

    async def _locate(self) -> object:
        """Find the strip to connect to.

        Prefer a fresh advertisement, but when a configured address is present
        and nothing is advertising, hand back the bare address: BlueZ can still
        connect to a device it already knows, which recovers from a stale
        connection that left the controller silent.
        """
        device = await sp621e.find(self.cfg.address, timeout=sp621e.CONNECT_SCAN_TIMEOUT)
        if device is not None:
            return device
        if self.cfg.address:
            log.debug("no advertisement; trying %s by address", self.cfg.address)
            return self.cfg.address
        raise ConnectionError("no SP621E advertising and no address configured")

    async def _serve_requests(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            target = self._pending
            self._pending = None
            if target is None:
                await self._strip.send([sp621e.power(False)])
                continue
            self._fade_task = asyncio.create_task(self._apply(target, self._fade_wanted))
            with contextlib.suppress(asyncio.CancelledError):
                await self._fade_task

    async def _apply(self, target: palette.Rgb, fade: bool) -> None:
        await self._strip.send([sp621e.power(True), sp621e.effect(sp621e.EFFECT_SOLID)], ack=True)
        if fade and self._current is not None and self._current != target:
            # A fade is many quick writes, so send them unacknowledged; the gap
            # is floored so BlueZ finishes each before the next and never floods
            # with InProgress.
            ramp = palette.steps(self._current, target, self.cfg.fade_steps)
            gap = max(self.cfg.step_ms / 1000, 0.03)
            await self._strip.send(
                (sp621e.color(rgb, self.cfg.brightness) for rgb in ramp), gap=gap, ack=False
            )
        else:
            # A single instant colour: one acknowledged write waits its turn
            # cleanly instead of racing an unacknowledged one.
            await self._strip.send([sp621e.color(target, self.cfg.brightness)], ack=True)
        self._current = target
        config.save_state({"color": palette.to_hex(target), "address": self.cfg.address})


def send_request(request: dict, timeout: float = 5.0) -> dict:
    """Talk to a running daemon from the CLI. Returns the daemon's reply."""

    async def _talk() -> dict:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        writer.close()
        return json.loads(line)

    return asyncio.run(_talk())


def is_running() -> bool:
    return SOCKET_PATH.exists() and os.path.exists(SOCKET_PATH)
