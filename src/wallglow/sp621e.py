"""BanlanX SP621E protocol and BLE transport.

The frame layout comes from the ``banlanx2`` family in monty68/uniled, which
covers the SP611E, SP617E, SP620E and SP621E. Every command is a short
unencrypted write to one characteristic; the controller accepts a single BLE
connection at a time, so the phone app has to be closed while we talk to it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakDBusError, BleakError

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Bluetooth SIG company IDs seen in SP6xxE advertisements. 20563 is 0x5053, "SP".
MANUFACTURER_IDS = (20563, 5053)

# Discovery is skipped entirely when an address is configured; this bounds the
# scan used only when it is not.
CONNECT_SCAN_TIMEOUT = 10.0

EFFECT_SOLID = 0xBE
EFFECT_WHITE = 0xBF

Rgb = tuple[int, int, int]

STATUS_MAGIC = b"\x53\x43"


@dataclass(frozen=True)
class Status:
    power: bool
    effect: int
    brightness: int
    speed: int
    length: int
    rgb: Rgb

    @classmethod
    def parse(cls, payload: bytes) -> Status:
        """Decode the reassembled reply to :func:`state_query`.

        Layout per UniLED: power, loop, effect, chip order, brightness, speed,
        effect length, then red, green, blue. Later bytes are audio settings,
        firmware identification and timers, which we do not need.
        """
        if len(payload) < 10:
            raise ValueError(f"status payload too short: {payload.hex()}")
        return cls(
            power=bool(payload[0]),
            effect=payload[2],
            brightness=payload[4],
            speed=payload[5],
            length=payload[6],
            rgb=(payload[7], payload[8], payload[9]),
        )


class StatusAssembler:
    """Collects the notification packets one status reply is split across.

    Each packet is ``53 43 <n> <total> <len>`` followed by ``len`` payload
    bytes; ``total`` is the payload length of the whole message.
    """

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._total = 0
        self.done = asyncio.Event()
        self.payload = b""

    def feed(self, packet: bytes) -> None:
        if len(packet) < 5 or packet[:2] != STATUS_MAGIC:
            return
        number, total, length = packet[2], packet[3], packet[4]
        if number == 1:
            self._parts, self._total = [], total
        elif not self._parts:
            return
        self._parts.append(packet[5 : 5 + length])
        joined = b"".join(self._parts)
        if len(joined) >= self._total:
            self.payload = joined[: self._total]
            self.done.set()


def power(on: bool) -> bytes:
    return bytes((0xA0, 0x62, 0x01, 0x01 if on else 0x00))


def effect(code: int) -> bytes:
    return bytes((0xA0, 0x63, 0x01, _u8(code)))


def brightness(level: int) -> bytes:
    return bytes((0xA0, 0x66, 0x01, _u8(level)))


def color(rgb: Rgb, level: int = 255) -> bytes:
    r, g, b = rgb
    return bytes((0xA0, 0x69, 0x04, _u8(r), _u8(g), _u8(b), _u8(level)))


def state_query() -> bytes:
    return bytes((0xA0, 0x70, 0x00))


def _u8(value: int) -> int:
    if not 0 <= value <= 255:
        raise ValueError(f"byte out of range: {value}")
    return value


def looks_like_sp6xx(adv: AdvertisementData) -> bool:
    return any(mid in adv.manufacturer_data for mid in MANUFACTURER_IDS)


async def scan(timeout: float = 5.0) -> list[tuple[BLEDevice, AdvertisementData]]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return [(dev, adv) for dev, adv in found.values() if looks_like_sp6xx(adv)]


async def find(address: str | None, timeout: float = 10.0) -> BLEDevice | None:
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=timeout)
    return await BleakScanner.find_device_by_filter(
        lambda _dev, adv: looks_like_sp6xx(adv), timeout=timeout
    )


class Strip:
    """One connection to the controller, used as an async context manager."""

    def __init__(self, device: BLEDevice | str, attempts: int = 3) -> None:
        # A bare address lets BlueZ connect to a device it already knows even
        # when it is not currently advertising, which a fresh BLEDevice needs.
        self.device = device
        self.attempts = attempts
        self._client: BleakClient | None = None

    @property
    def address(self) -> str:
        return self.device if isinstance(self.device, str) else self.device.address

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        # A link BlueZ already holds (a stale connection left by a killed process,
        # or an auto-reconnect) deadlocks a fresh connect: it returns InProgress
        # and BlueZ shows Connected while the new client never does. Clearing that
        # link first lets the connect below start from the clean state that works.
        await self._clear_stale_link()
        last: BleakError | None = None
        for attempt in range(1, self.attempts + 1):
            client = BleakClient(self.device, timeout=20.0)
            try:
                await client.connect()
            except BleakError as exc:
                last = exc
                if client.is_connected:
                    self._client = client
                    return
                if attempt < self.attempts:
                    settle = 2.5 if isinstance(exc, BleakDBusError) else 1.0
                    await asyncio.sleep(settle)
                continue
            self._client = client
            return
        raise ConnectionError(f"could not connect to {self.address}: {last}")

    async def _clear_stale_link(self) -> None:
        """Drop any connection BlueZ is already holding to this address.

        Bleak's disconnect on a not-yet-connected client asks BlueZ to tear the
        device down, which is a no-op when nothing is connected and the fix when
        something stale is.
        """
        with contextlib.suppress(BleakError, EOFError, AttributeError):
            await BleakClient(self.device, timeout=10.0).disconnect()
            await asyncio.sleep(0.5)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send(self, frames: Iterable[bytes], gap: float = 0.0, ack: bool = True) -> None:
        """Write each frame, spacing them ``gap`` seconds apart including write time.

        Acknowledged writes take about 110 ms on this controller, too slow for
        a smooth fade; unacknowledged ones go out every 40 ms without loss.
        """
        if self._client is None:
            raise ConnectionError("not connected")
        loop = asyncio.get_running_loop()
        for frame in frames:
            due = loop.time() + gap
            await self._client.write_gatt_char(WRITE_UUID, frame, response=ack)
            if gap:
                await asyncio.sleep(max(0.0, due - loop.time()))

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def ensure_connected(self) -> None:
        if not self.is_connected:
            self._client = None
            await self.connect()

    async def query_status(self, timeout: float = 3.0) -> Status:
        if self._client is None:
            raise ConnectionError("not connected")
        assembler = StatusAssembler()
        await self._client.start_notify(WRITE_UUID, lambda _c, data: assembler.feed(bytes(data)))
        try:
            await self._client.write_gatt_char(WRITE_UUID, state_query(), response=True)
            await asyncio.wait_for(assembler.done.wait(), timeout)
        finally:
            await self._client.stop_notify(WRITE_UUID)
        return Status.parse(assembler.payload)
