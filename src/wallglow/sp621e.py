"""BanlanX SP621E protocol and BLE transport.

The frame layout comes from the ``banlanx2`` family in monty68/uniled, which
covers the SP611E, SP617E, SP620E and SP621E. Every command is a short
unencrypted write to one characteristic; the controller accepts a single BLE
connection at a time, so the phone app has to be closed while we talk to it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Self

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# Bluetooth SIG company IDs seen in SP6xxE advertisements. 20563 is 0x5053, "SP".
MANUFACTURER_IDS = (20563, 5053)

EFFECT_SOLID = 0xBE
EFFECT_WHITE = 0xBF

Rgb = tuple[int, int, int]


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

    def __init__(self, device: BLEDevice, attempts: int = 3) -> None:
        self.device = device
        self.attempts = attempts
        self._client: BleakClient | None = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        last: BleakError | None = None
        for attempt in range(1, self.attempts + 1):
            client = BleakClient(self.device, timeout=15.0)
            try:
                await client.connect()
            except BleakError as exc:
                # BlueZ regularly aborts the first attempt to these controllers
                # with "le-connection-abort-by-local"; a retry almost always lands.
                last = exc
                if attempt < self.attempts:
                    await asyncio.sleep(1.0)
                continue
            self._client = client
            return
        raise ConnectionError(f"could not connect to {self.device.address}: {last}")

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send(self, frames: Iterable[bytes], gap: float = 0.0) -> None:
        """Write each frame, spacing them ``gap`` seconds apart including write time."""
        if self._client is None:
            raise ConnectionError("not connected")
        loop = asyncio.get_running_loop()
        for frame in frames:
            due = loop.time() + gap
            await self._client.write_gatt_char(WRITE_UUID, frame, response=True)
            if gap:
                await asyncio.sleep(max(0.0, due - loop.time()))
