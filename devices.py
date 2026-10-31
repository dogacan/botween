"""
Pick the camera and microphone by name (or index).

Names match case-insensitively, either as a substring or when every word of the hint
appears in the device name. So "USB Camera" finds both the "USB Camera" video device and
its "USB 2.0 Camera" microphone.

Run `python3 devices.py` to list what's connected.
"""
import re
import sys
from typing import List, Optional, Tuple, Union

DeviceSpec = Union[int, str, None]


def parse_spec(raw) -> DeviceSpec:
    """'' -> None, '2' -> 2, 'USB Camera' -> 'USB Camera'."""
    if raw is None or isinstance(raw, int):
        return raw
    raw = str(raw).strip()
    if not raw:
        return None
    return int(raw) if raw.isdigit() else raw


def name_matches(hint: str, name: str) -> bool:
    hint, name = hint.lower().strip(), name.lower()
    if hint in name:
        return True
    words = re.findall(r"[a-z0-9]+", hint)
    name_words = set(re.findall(r"[a-z0-9]+", name))
    return bool(words) and all(w in name_words for w in words)


def _best_match(hint: str, devices: List[Tuple[int, str]]) -> Optional[int]:
    """Exact name first, then substring, then all-words match."""
    h = hint.lower().strip()
    for test in (lambda n: n.lower() == h, lambda n: h in n.lower(), lambda n: name_matches(hint, n)):
        for idx, name in devices:
            if test(name):
                return idx
    return None


# ---- cameras ---------------------------------------------------------------

def list_cameras() -> List[Tuple[int, str]]:
    """(OpenCV index, name) for each camera, in the same order OpenCV uses on macOS."""
    if sys.platform != "darwin":
        return []
    try:
        import AVFoundation as AVF
    except ImportError:
        print("[Devices] Camera names unavailable: pip3 install pyobjc-framework-AVFoundation")
        return []
    # Mirrors OpenCV's cap_avfoundation_mac.mm: video + muxed devices, sorted by uniqueID.
    devs = (list(AVF.AVCaptureDevice.devicesWithMediaType_(AVF.AVMediaTypeVideo))
            + list(AVF.AVCaptureDevice.devicesWithMediaType_(AVF.AVMediaTypeMuxed)))
    devs.sort(key=lambda d: str(d.uniqueID()))
    return [(i, str(d.localizedName())) for i, d in enumerate(devs)]


def resolve_camera(spec: DeviceSpec, fallback: int = 0) -> Tuple[int, str]:
    """Returns (OpenCV index, label). Unknown names fall back to `fallback` with a warning."""
    spec = parse_spec(spec)
    cams = list_cameras()
    names = dict(cams)
    if spec is None:
        spec = fallback
    if isinstance(spec, int):
        return spec, names.get(spec, f"camera {spec}")
    idx = _best_match(spec, cams)
    if idx is not None:
        return idx, names[idx]
    found = ", ".join(f"[{i}] {n}" for i, n in cams) or "none listed"
    print(f"[Camera] Warning: no camera matching '{spec}' (found: {found}). Using camera {fallback}.")
    return fallback, names.get(fallback, f"camera {fallback}")


# ---- microphones -----------------------------------------------------------

def list_microphones() -> List[Tuple[int, str]]:
    import sounddevice as sd
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def find_microphone(spec: DeviceSpec) -> Optional[int]:
    """sounddevice index for a mic name or index, or None if it isn't connected."""
    spec = parse_spec(spec)
    mics = list_microphones()
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec if spec in dict(mics) else None
    return _best_match(spec, mics)


def print_devices():
    import sounddevice as sd
    import config

    cam_idx, _ = resolve_camera(config.CAMERA_DEVICE)
    mic_idx = find_microphone(config.MIC_DEVICE)
    print("Cameras (CAMERA_DEVICE / --camera):")
    for i, name in list_cameras():
        print(f"  [{i}] {name}{'  <- selected' if i == cam_idx else ''}")
    print("\nMicrophones (MIC_DEVICE / --mic):")
    default_in = sd.default.device[0]
    for i, name in list_microphones():
        tags = [t for t, on in (("system default", i == default_in), ("selected", i == mic_idx)) if on]
        print(f"  [{i}] {name}{'  <- ' + ', '.join(tags) if tags else ''}")
    print(f"\nCurrent settings: CAMERA_DEVICE={config.CAMERA_DEVICE!r}  MIC_DEVICE={config.MIC_DEVICE!r}")


if __name__ == "__main__":
    print_devices()
