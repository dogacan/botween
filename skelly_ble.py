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
  python3 skelly_ble.py live          # turn on the "(Live)" speaker and wait while you pair it
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
        self._closing = False

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
        self._closing = False
        self.client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await self.client.connect()
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        print(f"[Skelly] Connected to '{self.device_name}' ({device.address}).")
        return True

    def _on_disconnect(self, _client):
        if not self._closing:
            print("[Skelly] Bluetooth control link dropped.")

    async def disconnect(self):
        self._closing = True
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


def find_output_device(name_hint: str) -> Optional[int]:
    """Index of the first audio output whose name contains name_hint (case-insensitive)."""
    import sounddevice as sd

    if not name_hint:
        return None
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0 and name_hint.lower() in dev["name"].lower():
            return idx
    return None


def refresh_audio_devices():
    """PortAudio caches the device list at startup; re-scan so a newly connected speaker shows up."""
    import sounddevice as sd

    sd._terminate()
    sd._initialize()


def _is_live_speaker(device_name: str, wanted: str) -> bool:
    device_name = device_name.lower()
    if device_name == wanted.lower():
        return True
    hints = (config.SKELLY_BLE_NAME.lower(),) if config.SKELLY_BLE_NAME else DEFAULT_NAME_HINTS
    return "(live)" in device_name and any(h in device_name for h in hints)


def connect_live_speaker(name: str) -> Optional[str]:
    """
    Asks macOS to connect the already-paired '(Live)' speaker. Blocking; returns how it was
    requested ("IOBluetooth" or "blueutil"), or None if it couldn't be.
    """
    try:
        from IOBluetooth import IOBluetoothDevice  # pip install pyobjc-framework-IOBluetooth

        for dev in IOBluetoothDevice.pairedDevices() or []:
            if _is_live_speaker(dev.name() or "", name):
                if dev.isConnected() or dev.openConnection() == 0:
                    return "IOBluetooth"
                return None
    except ImportError:
        pass
    except Exception as e:
        print(f"[Skelly] IOBluetooth connect failed: {e}")

    blueutil = shutil.which("blueutil")
    if blueutil:
        try:
            result = subprocess.run([blueutil, "--connect", name], capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                return "blueutil"
        except Exception:
            pass
    return None


async def wait_for_live_speaker(skelly: "SkellyBLE", timeout_sec: float, retry_every: float = 5.0) -> Optional[int]:
    """
    Waits until the '(Live)' speaker shows up as an audio output, asking macOS to connect it
    every few seconds and keeping the BLE control link (and so live mode) up meanwhile.
    Returns the sounddevice output index, or None on timeout.
    """
    hint = config.SKELLY_AUDIO_OUTPUT or "Skelly"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    next_connect = loop.time() + 1.0  # give live mode a moment to come up
    announced = False
    while True:
        refresh_audio_devices()
        device = find_output_device(hint)
        if device is not None:
            return device
        now = loop.time()
        if now >= deadline:
            return None
        if now >= next_connect:
            how = await asyncio.to_thread(connect_live_speaker, skelly.live_speaker_name)
            if how and not announced:
                print(f"[Skelly] Asked macOS to connect '{skelly.live_speaker_name}' ({how})...")
                announced = True
            next_connect = loop.time() + retry_every
        if not skelly.is_connected:
            print("[Skelly] Reconnecting control link...")
            try:
                if await skelly.connect():
                    await skelly.enable_live_mode()
            except Exception as e:
                print(f"[Skelly] Reconnect failed: {e}")
        await asyncio.sleep(1.0)


def parse_color(text: str) -> Tuple[int, int, int]:
    text = text.strip().lower()
    if text in COLORS:
        return COLORS[text]
    hex_str = text.lstrip("#")
    if len(hex_str) == 6:
        return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
    raise ValueError(f"Unknown color '{text}'. Use one of {', '.join(COLORS)} or #RRGGBB.")


async def _wait_for_live_speaker(skelly: SkellyBLE, timeout_sec: int):
    """Keeps the control link open while the user pairs the '(Live)' speaker, until it shows up as audio output."""
    if find_output_device(config.SKELLY_AUDIO_OUTPUT or "Skelly") is None:
        print(f"[Skelly] If '{skelly.live_speaker_name}' isn't paired yet: System Settings > Bluetooth, "
              f"connect it (PIN 1234).\n[Skelly] Keeping the skeleton connected for up to {timeout_sec}s "
              "while it connects (Ctrl+C to stop)...")
    if await wait_for_live_speaker(skelly, timeout_sec) is not None:
        print(f"[Skelly] '{skelly.live_speaker_name}' is connected as an audio output. All set!")
    else:
        print("[Skelly] Timed out waiting for the speaker. Run this again to retry.")


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
            print(f"[Skelly] Live mode {'enabled' if acked else 'requested (no ack)'}.")
            await _wait_for_live_speaker(skelly, int(args.value or 180))
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
    parser.add_argument("value", nargs="?", help="volume 0-255, color name/#RRGGBB, icon 1-18, head/arm/torso/all, "
                                                 "or for 'live' how many seconds to wait for pairing (default 180)")
    asyncio.run(_cli(parser.parse_args()))
