"""
Bluetooth Low Energy control for the Home Depot 6.5 ft "Ultra Skelly".

The skeleton has two radios:
  * BLE control link (this module): advertises as "Ultra Skelly v2" (current firmware)
    or "Animated Skelly" (older firmware). Used for live mode, volume, lights, eyes, movement.
  * Classic Bluetooth speaker: appears as "<name>(Live)" (PIN 1234) only after live mode is
    enabled. Audio played to it comes out of the skeleton and drives the jaw.

Protocol notes come from the community reverse-engineering work in
https://github.com/martinecker/SkellyUltra and https://github.com/TE-DMod/UltraSkelly:
  frame = 0xAA + command byte + payload (zero-padded to >= 8 bytes) + CRC-8/MAXIM

Run directly for quick hardware checks:
  python3 skelly_ble.py scan
  python3 skelly_ble.py live          # turn on the "(Live)" Bluetooth speaker
  python3 skelly_ble.py volume 200
  python3 skelly_ble.py eyes red
  python3 skelly_ble.py move all
"""

import argparse
import asyncio
import shutil
import subprocess
from typing import Dict, Optional, Tuple

from bleak import BleakClient, BleakScanner

import config

WRITE_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

# Command bytes (second byte of the frame, after 0xAA)
CMD_QUERY_LIVE_MODE = 0xE1
CMD_QUERY_VOLUME = 0xE5
CMD_QUERY_LIVE_NAME = 0xE6
CMD_QUERY_VERSION = 0xEE
CMD_SET_LIGHT_MODE = 0xF2
CMD_SET_LIGHT_BRIGHTNESS = 0xF3
CMD_SET_LIGHT_RGB = 0xF4
CMD_SET_EYE_ICON = 0xF9
CMD_SET_VOLUME = 0xFA
CMD_ENABLE_LIVE_MODE = 0xFD
CMD_SET_MOVEMENT = 0xCA

# Light channels on the Ultra Skelly
CHANNEL_TORSO = 0
CHANNEL_HEAD = 1
CHANNEL_ALL = 0xFF

# Movement bitfield
MOVE_HEAD = 0x01
MOVE_ARM = 0x02
MOVE_TORSO = 0x04
MOVE_ALL = 0xFF
MOVEMENTS = {"head": MOVE_HEAD, "arm": MOVE_ARM, "torso": MOVE_TORSO, "all": MOVE_ALL}

LIGHT_MODES = {"static": 1, "strobe": 2, "pulse": 3}

COLORS: Dict[str, Tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "orange": (255, 80, 0),
    "yellow": (255, 200, 0),
    "green": (0, 255, 0),
    "blue": (0, 60, 255),
    "cyan": (0, 220, 255),
    "purple": (160, 0, 255),
    "pink": (255, 40, 160),
    "white": (255, 255, 255),
}

DEFAULT_NAME_HINTS = ("ultra skelly", "animated skelly")


def crc8_maxim(data: bytes) -> int:
    """CRC-8/MAXIM (reflected poly 0x8C, init 0), as used by the Skelly firmware."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def build_frame(cmd: int, payload: bytes = b"\x00") -> bytes:
    body = bytes([0xAA, cmd]) + payload.ljust(8, b"\x00")
    return body + bytes([crc8_maxim(body)])


def _global_setting(*values: int) -> bytes:
    """Payload for per-show settings applied globally: values + cluster 0 + empty filename."""
    return bytes(values) + b"\x00\x00\x00\x00" + b"\x00"


class SkellyBLE:
    """Async BLE controller. All methods must be awaited from one asyncio loop."""

    def __init__(self, address: str = config.SKELLY_BLE_ADDRESS, name_hint: str = config.SKELLY_BLE_NAME):
        self.address = address or None
        self.name_hints = (name_hint.lower(),) if name_hint else DEFAULT_NAME_HINTS
        self.client: Optional[BleakClient] = None
        self.device_name: Optional[str] = None
        self._responses: Dict[int, bytes] = {}
        self._response_event = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    @property
    def live_speaker_name(self) -> str:
        return f"{self.device_name or 'Ultra Skelly v2'}(Live)"

    async def find(self, timeout: float = 10.0):
        if self.address:
            return await BleakScanner.find_device_by_address(self.address, timeout=timeout)

        def _match(device, adv):
            name = (device.name or adv.local_name or "").lower()
            return any(h in name for h in self.name_hints)

        return await BleakScanner.find_device_by_filter(_match, timeout=timeout)

    async def connect(self, timeout: float = 10.0) -> bool:
        device = await self.find(timeout=timeout)
        if not device:
            print("[Skelly] No Skelly found over Bluetooth. Is it powered on, in Bluetooth mode, "
                  "and disconnected from the phone app?")
            return False
        self.device_name = device.name
        self.client = BleakClient(device, disconnected_callback=lambda _c: print("[Skelly] Bluetooth control link dropped."))
        await self.client.connect()
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        print(f"[Skelly] Connected to '{self.device_name}' ({device.address}).")
        return True

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def _on_notify(self, _char, data: bytearray):
        data = bytes(data)
        if len(data) >= 2 and data[0] == 0xBB:
            self._responses[data[1]] = data
            self._response_event.set()

    async def send(self, frame: bytes):
        async with self._lock:
            if not self.is_connected:
                if not await self.connect():
                    raise RuntimeError("Skelly is not connected")
            await self.client.write_gatt_char(WRITE_UUID, frame, response=False)

    async def query(self, cmd: int, timeout: float = 2.0) -> Optional[bytes]:
        self._responses.pop(cmd, None)
        await self.send(build_frame(cmd))
        deadline = asyncio.get_running_loop().time() + timeout
        while cmd not in self._responses:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            self._response_event.clear()
            try:
                await asyncio.wait_for(self._response_event.wait(), remaining)
            except asyncio.TimeoutError:
                return None
        return self._responses[cmd]

    # ---- Controls -------------------------------------------------------

    async def enable_live_mode(self) -> bool:
        """Turns on the Classic Bluetooth '(Live)' speaker. Returns True if the device acknowledged."""
        self._responses.pop(CMD_ENABLE_LIVE_MODE, None)
        await self.send(build_frame(CMD_ENABLE_LIVE_MODE, b"\x01"))
        for _ in range(20):
            if CMD_ENABLE_LIVE_MODE in self._responses:
                return True
            await asyncio.sleep(0.1)
        return False

    async def set_volume(self, volume: int):
        await self.send(build_frame(CMD_SET_VOLUME, bytes([max(0, min(255, volume))])))

    async def get_volume(self) -> Optional[int]:
        resp = await self.query(CMD_QUERY_VOLUME)
        return resp[2] if resp and len(resp) > 2 else None

    async def set_light_color(self, channel: int, rgb: Tuple[int, int, int]):
        r, g, b = (max(0, min(255, v)) for v in rgb)
        await self.send(build_frame(CMD_SET_LIGHT_RGB, bytes([channel]) + _global_setting(r, g, b, 0)))

    async def set_light_mode(self, channel: int, mode: str):
        await self.send(build_frame(CMD_SET_LIGHT_MODE, bytes([channel]) + _global_setting(LIGHT_MODES[mode])))

    async def set_light_brightness(self, channel: int, brightness: int):
        await self.send(build_frame(CMD_SET_LIGHT_BRIGHTNESS, bytes([channel]) + _global_setting(max(0, min(255, brightness)))))

    async def set_eye_icon(self, icon: int):
        """Eye image 1-18 on the LCD eyes."""
        await self.send(build_frame(CMD_SET_EYE_ICON, _global_setting(icon, 0)))

    async def move(self, parts: int = MOVE_ALL):
        """Enables the given movement groups; the firmware performs one movement when this is sent."""
        await self.send(build_frame(CMD_SET_MOVEMENT, _global_setting(parts, 0)))


def connect_live_speaker(name: str) -> bool:
    """Asks macOS to connect the paired '(Live)' speaker via blueutil, if it is installed."""
    blueutil = shutil.which("blueutil")
    if not blueutil:
        return False
    try:
        result = subprocess.run([blueutil, "--connect", name], capture_output=True, text=True, timeout=20)
        return result.returncode == 0
    except Exception:
        return False


def parse_color(text: str) -> Tuple[int, int, int]:
    text = text.strip().lower()
    if text in COLORS:
        return COLORS[text]
    hex_str = text.lstrip("#")
    if len(hex_str) == 6:
        return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
    raise ValueError(f"Unknown color '{text}'. Use one of {', '.join(COLORS)} or #RRGGBB.")


async def _cli(args):
    if args.command == "scan":
        print("[Skelly] Scanning for 8 seconds...")
        for d, adv in (await BleakScanner.discover(timeout=8.0, return_adv=True)).values():
            name = d.name or adv.local_name
            if name:
                marker = "  <-- Skelly" if any(h in name.lower() for h in DEFAULT_NAME_HINTS) else ""
                print(f"  {d.address}  {name}  (RSSI {adv.rssi}){marker}")
        return

    skelly = SkellyBLE()
    if not await skelly.connect():
        return
    try:
        if args.command == "info":
            print(f"  Volume: {await skelly.get_volume()}")
            version = await skelly.query(CMD_QUERY_VERSION)
            print(f"  Version response: {version.hex(' ') if version else 'no reply'}")
        elif args.command == "live":
            acked = await skelly.enable_live_mode()
            print(f"[Skelly] Live mode {'enabled' if acked else 'requested (no ack)'}. "
                  f"Pair/connect '{skelly.live_speaker_name}' on this Mac (PIN 1234).")
        elif args.command == "volume":
            await skelly.set_volume(int(args.value))
            print(f"[Skelly] Volume set to {args.value}.")
        elif args.command == "eyes":
            await skelly.set_light_color(CHANNEL_HEAD, parse_color(args.value))
            print(f"[Skelly] Head light set to {args.value}.")
        elif args.command == "torso":
            await skelly.set_light_color(CHANNEL_TORSO, parse_color(args.value))
            print(f"[Skelly] Torso light set to {args.value}.")
        elif args.command == "icon":
            await skelly.set_eye_icon(int(args.value))
            print(f"[Skelly] Eye icon set to {args.value}.")
        elif args.command == "move":
            await skelly.move(MOVEMENTS[args.value or "all"])
            print(f"[Skelly] Movement '{args.value or 'all'}' sent.")
        await asyncio.sleep(0.5)  # let the write flush before disconnecting
    finally:
        await skelly.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ultra Skelly Bluetooth control")
    parser.add_argument("command", choices=["scan", "info", "live", "volume", "eyes", "torso", "icon", "move"])
    parser.add_argument("value", nargs="?", help="volume 0-255, color name/#RRGGBB, icon 1-18, or head/arm/torso/all")
    asyncio.run(_cli(parser.parse_args()))
