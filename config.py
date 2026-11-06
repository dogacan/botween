import json
import os
import random
import re
from pathlib import Path
from typing import Iterable, Optional
from dotenv import load_dotenv

# Load .env file from project root if present
load_dotenv()

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")

# Project Directories
BASE_DIR = Path(__file__).parent.resolve()
PROMPTS_DIR = BASE_DIR / "prompts"

# Prompt & Cue Markdown Files
PROMPT_FILE = BASE_DIR / os.getenv("PROMPT_FILE", "prompt.md")
LIVE_ADDENDUM_FILE = PROMPTS_DIR / "live_addendum.md"
RECENT_LINES_ADDENDUM_FILE = PROMPTS_DIR / "recent_lines_addendum.md"
TOOLS_ADDENDUM_FILE = PROMPTS_DIR / "tools_addendum.md"
CUES_FILE = PROMPTS_DIR / "cues.md"
JOKES_FILE = BASE_DIR / os.getenv("JOKES_FILE", "jokes.json")
JOKES_PER_SESSION = int(os.getenv("JOKES_PER_SESSION", "5"))
JOKES_PLACEHOLDER = "{{JOKES}}"

LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "tr": "Turkish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "hi": "Hindi", "gu": "Gujarati", "ar": "Arabic", "ru": "Russian",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.split("-")[0].lower(), code)


def load_jokes() -> list[str]:
    """Reads jokes.json: either a plain list of jokes or {category: [jokes]}."""
    try:
        data = json.loads(JOKES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Config] Warning: Failed to read {JOKES_FILE}: {e}")
        return []
    groups = data.values() if isinstance(data, dict) else [data]
    return [joke for group in groups for joke in group if isinstance(joke, str) and joke.strip()]


_joke_deck: list[str] = []  # shuffled jokes not dealt yet; refilled when it runs low
_last_bag: list[str] = []   # the jokes the previous visitor got


def pick_jokes() -> str:
    """The next handful of jokes from a shuffled deck, so back-to-back visitors get different ones."""
    global _joke_deck
    jokes = load_jokes()
    if not jokes:
        return "- (The joke bag is empty tonight. Make up your own bone puns!)"
    count = min(JOKES_PER_SESSION, len(jokes))
    _joke_deck = [joke for joke in _joke_deck if joke in jokes]  # jokes.json may have been edited
    if len(_joke_deck) < count:
        # Reshuffle without the leftovers so a joke can't be dealt twice in one bag.
        fresh = [joke for joke in jokes if joke not in _joke_deck]
        random.shuffle(fresh)
        # ...and the last visitor's jokes go to the bottom, so the next visitor doesn't get them again.
        fresh.sort(key=lambda joke: joke in _last_bag)
        _joke_deck += fresh
    picked, _joke_deck = _joke_deck[:count], _joke_deck[count:]
    _last_bag[:] = picked
    return "\n".join(f"- {joke}" for joke in picked)


def _read_md(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"[Config] Warning: Failed to read {path}: {e}")
        return ""


def load_system_instruction() -> str:
    """Loads the skeleton prompt from prompt.md, filling {{JOKES}} with a random set from jokes.json."""
    if PROMPT_FILE.exists():
        prompt = _read_md(PROMPT_FILE)
        if prompt:
            if JOKES_PLACEHOLDER in prompt:
                prompt = prompt.replace(JOKES_PLACEHOLDER, pick_jokes())
            return prompt
    return "You are Bonesy, an enchanted, witty talking skeleton for Halloween."


def build_system_instruction(
    recent_lines: Optional[Iterable[str]] = None,
    movement_parts: Optional[list[str]] = None,
) -> str:
    """Assembles the full Live system instruction from prompt.md and the markdown addendums in prompts/."""
    instruction = load_system_instruction()

    live_addendum = _read_md(LIVE_ADDENDUM_FILE)
    if live_addendum:
        instruction += "\n\n" + live_addendum.format(language=language_name(LIVE_LANGUAGE))

    recent = list(recent_lines or [])
    if recent:
        recent_addendum = _read_md(RECENT_LINES_ADDENDUM_FILE)
        if recent_addendum:
            lines_text = "\n".join(f'- "{line}"' for line in recent)
            instruction += "\n\n" + recent_addendum.format(lines=lines_text)

    if movement_parts is not None:
        tools_addendum = _read_md(TOOLS_ADDENDUM_FILE)
        if tools_addendum:
            move_text = f" and `move` wiggles your {' or '.join(movement_parts)}" if movement_parts else ""
            instruction += "\n\n" + tools_addendum.format(move_text=move_text)

    return instruction


def load_cues() -> dict[str, str]:
    """Parses `## <cue_name>` sections from prompts/cues.md into a dictionary of stage cues."""
    raw = _read_md(CUES_FILE)
    if not raw:
        return {}
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    cues: dict[str, str] = {}
    current_key: Optional[str] = None
    buffer: list[str] = []
    for line in raw.splitlines():
        if line.startswith("## "):
            if current_key and buffer:
                cues[current_key] = " ".join(" ".join(buffer).split())
            current_key = line[3:].strip()
            buffer = []
        elif current_key is not None and line.strip():
            buffer.append(line.strip())
    if current_key and buffer:
        cues[current_key] = " ".join(" ".join(buffer).split())
    return cues


def get_cue(name: str) -> str:
    """Returns a named stage cue from prompts/cues.md."""
    return load_cues().get(name, "")


# Hardware & Devices
# Camera and mic: a device name (word match, see devices.py) or an index. List them with:
#   python3 devices.py
CAMERA_DEVICE = os.getenv("CAMERA_DEVICE", "USB Camera").strip()  # blank = camera 0
MIC_DEVICE = os.getenv("MIC_DEVICE", "USB Camera").strip()        # blank = system default mic
# Turn the picture right side up when the camera is mounted upside down
CAMERA_ROTATE_180 = os.getenv("CAMERA_ROTATE_180", "false").strip().lower() in ("true", "1", "yes")

# Audio Input & Local Voice Trigger
SAMPLE_RATE = 16000  # 16kHz standard
CHANNELS = 1         # Mono
VAD_ENERGY_THRESHOLD = float(os.getenv("VAD_ENERGY_THRESHOLD", "0.03"))

# Real-Time Spooky Voice FX (voice_fx.py)
ENABLE_SPOOKY_FX = os.getenv("ENABLE_SPOOKY_FX", "true").lower() in ("true", "1", "yes")


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes")


# Gemini Live API
# Camera frames sent during a conversation: at most one every this many seconds (Live allows 1/s).
# A fresh frame also goes out when a conversation opens, so a longer gap mostly saves tokens.
LIVE_VIDEO_INTERVAL_SEC = max(1.0, float(os.getenv("LIVE_VIDEO_INTERVAL_SEC", "5")))
LIVE_VIDEO_MAX_DIM = int(os.getenv("LIVE_VIDEO_MAX_DIM", "768"))    # smaller frames = fewer tokens
LIVE_IDLE_TIMEOUT_SEC = float(os.getenv("LIVE_IDLE_TIMEOUT_SEC", "25"))  # close session -> next visitor
LIVE_MAX_TURNS = int(os.getenv("LIVE_MAX_TURNS", "6"))                  # max turns per visitor before memory reset
LIVE_MAX_SESSION_SEC = float(os.getenv("LIVE_MAX_SESSION_SEC", "90"))   # max total seconds per visitor session
LIVE_CAP_COOLDOWN_SEC = float(os.getenv("LIVE_CAP_COOLDOWN_SEC", "5"))  # ignore the mic this long after a cap goodbye
LIVE_END_SILENCE_MS = int(os.getenv("LIVE_END_SILENCE_MS", "600"))  # server VAD: silence that ends a turn
LIVE_ECHO_TAIL_SEC = float(os.getenv("LIVE_ECHO_TAIL_SEC", "1.2"))  # keep mic muted after speech (BT delay)
# Live voice. Measured pitch: Alnilam ~92 Hz, Gacrux ~93, Umbriel ~93, Algenib ~99 (gravelly), Puck ~109.
LIVE_VOICE_NAME = os.getenv("LIVE_VOICE_NAME", "Algenib").strip() or "Algenib"
# Real-time voice FX (applied when ENABLE_SPOOKY_FX=true): pitch shift + crypt reverb + echo
LIVE_PITCH_SEMITONES = float(os.getenv("LIVE_PITCH_SEMITONES", "-3"))  # negative = deeper; 0 = off
LIVE_ECHO_DELAY_MS = float(os.getenv("LIVE_ECHO_DELAY_MS", "220"))      # time between echo repeats
LIVE_ECHO_FEEDBACK = float(os.getenv("LIVE_ECHO_FEEDBACK", "0.35"))     # how much each repeat keeps (0-0.9)
LIVE_ECHO_MIX = float(os.getenv("LIVE_ECHO_MIX", "0.2"))               # echo loudness; 0 = no echo
# Voice loudness boost (1 = unchanged, 2-2.5 = sweet spot outdoors). Peaks are softly limited instead of clipping.
LIVE_OUTPUT_GAIN = float(os.getenv("LIVE_OUTPUT_GAIN", "1.0"))
# Mic input boost sent to Gemini (1 = unchanged, 1.5-2 = helps pick up quiet voices at porch distance).
LIVE_INPUT_GAIN = float(os.getenv("LIVE_INPUT_GAIN", "1.5"))
# Default conversation language (BCP-47). Bonesy only switches if a visitor clearly speaks another language.
LIVE_LANGUAGE = os.getenv("LIVE_LANGUAGE", "en-US").strip() or "en-US"
LIVE_BARGE_IN = _env_bool("LIVE_BARGE_IN", "false")                 # let visitors interrupt (needs no echo!)
# Save each conversation (transcripts, cues and every frame sent to Gemini) for replay.py.
# Off by default: the frames are pictures of visitors, often kids.
RECORD_SESSIONS = _env_bool("RECORD_SESSIONS", "false")
RECORD_DIR = BASE_DIR / os.getenv("RECORD_DIR", "recordings")
# Say an awakening line at startup. Off by default: motion detection greets visitors anyway, and the
# line moves the skeleton right when the camera is learning what the empty porch looks like.
STARTUP_GREETING = _env_bool("STARTUP_GREETING", "false")
LIVE_KEEPALIVE_LEVEL = float(os.getenv("LIVE_KEEPALIVE_LEVEL", "0.001"))  # faint noise keeps BT speaker awake

# Live Session Timing, Fallback & Watchdogs
JOKE_SEEN_SEC = float(os.getenv("JOKE_SEEN_SEC", "4.0"))              # movement needed within this window for idle joke
GOODBYE_TIMEOUT_SEC = float(os.getenv("GOODBYE_TIMEOUT_SEC", "10.0"))  # close anyway if cap goodbye never arrives
FALLBACK_GREET_SEC = float(os.getenv("FALLBACK_GREET_SEC", "2.5"))    # no words heard after sound -> greet whoever is in view
FALLBACK_AFTER_SOUND_SEC = float(os.getenv("FALLBACK_AFTER_SOUND_SEC", "2.5"))
FALLBACK_GIVE_UP_SEC = float(os.getenv("FALLBACK_GIVE_UP_SEC", "15.0"))
FALLBACK_SEEN_SEC = float(os.getenv("FALLBACK_SEEN_SEC", "5.0"))
SEND_TIMEOUT_SEC = float(os.getenv("SEND_TIMEOUT_SEC", "5.0"))        # bound websocket sends
OUTPUT_STALL_SEC = float(os.getenv("OUTPUT_STALL_SEC", "2.0"))        # speaker callback stall timeout
WATCHDOG_EXTRA_SEC = float(os.getenv("WATCHDOG_EXTRA_SEC", "30.0"))   # hard watchdog past idle/cap limits


def _env_roi(name: str):
    """Parses 'x,y,w,h' (fractions of the frame, 0-1) into a tuple, or None when unset."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        x, y, w, h = (float(v) for v in raw.split(","))
    except ValueError:
        print(f"[Config] Warning: {name}='{raw}' should be 'x,y,w,h' fractions. Watching the whole frame.")
        return None
    return (x, y, w, h)


# Camera motion trigger (live engine, auto mode): Bonesy greets visitors as they walk up.
MOTION_ENABLED = _env_bool("MOTION_ENABLED", "true")
MOTION_MIN_AREA = float(os.getenv("MOTION_MIN_AREA", "0.03"))      # moving share of frame that counts as a person
MOTION_MAX_AREA = float(os.getenv("MOTION_MAX_AREA", "0.5"))       # more than this = headlights/lights/shake, ignored
MOTION_SUSTAIN_SEC = float(os.getenv("MOTION_SUSTAIN_SEC", "0.5"))  # motion must last this long
MOTION_REARM_QUIET_SEC = float(os.getenv("MOTION_REARM_QUIET_SEC", "3"))  # porch still this long -> watch again
MOTION_REARM_MAX_SEC = float(os.getenv("MOTION_REARM_MAX_SEC", "30"))      # ...or this long after the last session
MOTION_SILENT_TIMEOUT_SEC = float(os.getenv("MOTION_SILENT_TIMEOUT_SEC", "10"))  # close if visitor never talks
MOTION_JOKE_AFTER_SEC = float(os.getenv("MOTION_JOKE_AFTER_SEC", "6"))  # quiet visitor still in view -> one joke (0 = off)
MOTION_ROI = _env_roi("MOTION_ROI")                                # optional 'x,y,w,h' area to watch
MOTION_DEBUG = _env_bool("MOTION_DEBUG", "false")                   # print motion readings twice a second

# Ultra Skelly hardware
SKELLY_BLE_ENABLED = _env_bool("SKELLY_BLE_ENABLED", "true")
SKELLY_BLE_ADDRESS = os.getenv("SKELLY_BLE_ADDRESS", "").strip()    # optional; macOS uses UUIDs, not MACs
SKELLY_BLE_NAME = os.getenv("SKELLY_BLE_NAME", "").strip()          # optional name hint, e.g. "Ultra Skelly v2"
SKELLY_VOLUME = os.getenv("SKELLY_VOLUME", "").strip()              # optional 0-255, set at startup


def _env_parts(name: str, default: str):
    """Parses a body-part list like 'head,arm' (or 'all' / 'none') into a list of part names."""
    raw = os.getenv(name, default).strip().lower()
    if raw in ("", "none", "false", "off"):
        return []
    if raw == "all":
        return ["head", "arm", "torso"]
    wanted = {v.strip() for v in raw.split(",")}
    return [p for p in ("head", "arm", "torso") if p in wanted]


# Body parts that move (head, arm, torso, all or none). Enabled parts move whenever the skeleton plays
# sound, and the setting stays on the skeleton between runs, so Bonesy always sets it explicitly.
# SKELLY_IDLE_MOVEMENT is used until a visitor speaks (between visitors, the startup greeting and motion
# greetings), so a moving arm can't set off motion detection; SKELLY_MOVEMENT once a visitor talks. The torso is off by default because it shakes the camera view.
SKELLY_MOVEMENT = _env_parts("SKELLY_MOVEMENT", "head,arm")
SKELLY_IDLE_MOVEMENT = [p for p in _env_parts("SKELLY_IDLE_MOVEMENT", "head") if p in SKELLY_MOVEMENT]
# Output device for the voice (substring match). Falls back to the system default if not found.
SKELLY_AUDIO_OUTPUT = os.getenv("SKELLY_AUDIO_OUTPUT", "Skelly").strip()
