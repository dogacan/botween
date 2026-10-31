import json
import os
import random
from pathlib import Path
from dotenv import load_dotenv
from google.genai import types

# Load .env file from project root if present
load_dotenv()

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# TTS Engine Configuration ("gemini" or "edge")
TTS_ENGINE = os.getenv("TTS_ENGINE", "gemini").lower()
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
# Locked Prebuilt Voice (e.g. Puck, Charon, Fenrir, Orus, Kore, Zephyr)
# "Puck" is mischievous & raspy (ideal for a witty skeleton); "Charon" is deep & ominous
GEMINI_VOICE_NAME = os.getenv("GEMINI_VOICE_NAME", "Puck")

# Project Directories
BASE_DIR = Path(__file__).parent.resolve()
AUDIO_TEMP_DIR = BASE_DIR / "temp_audio"
AUDIO_TEMP_DIR.mkdir(exist_ok=True)

# Prompt Configuration
PROMPT_FILE = BASE_DIR / os.getenv("PROMPT_FILE", "prompt.md")
JOKES_FILE = BASE_DIR / os.getenv("JOKES_FILE", "jokes.json")
JOKES_PER_SESSION = int(os.getenv("JOKES_PER_SESSION", "5"))
JOKES_PLACEHOLDER = "{{JOKES}}"


def load_jokes() -> list[str]:
    """Reads jokes.json: either a plain list of jokes or {category: [jokes]}."""
    try:
        data = json.loads(JOKES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Config] Warning: Failed to read {JOKES_FILE}: {e}")
        return []
    groups = data.values() if isinstance(data, dict) else [data]
    return [joke for group in groups for joke in group if isinstance(joke, str) and joke.strip()]


def pick_jokes() -> str:
    """A fresh random handful of jokes as a bullet list, so each visitor hears different ones."""
    jokes = load_jokes()
    if not jokes:
        return "- (The joke bag is empty tonight. Make up your own bone puns!)"
    picked = random.sample(jokes, min(JOKES_PER_SESSION, len(jokes)))
    return "\n".join(f"- {joke}" for joke in picked)


def load_system_instruction() -> str:
    """Loads the skeleton prompt from prompt.md, filling {{JOKES}} with a random set from jokes.json."""
    if PROMPT_FILE.exists():
        try:
            prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
            if JOKES_PLACEHOLDER in prompt:
                prompt = prompt.replace(JOKES_PLACEHOLDER, pick_jokes())
            return prompt
        except Exception as e:
            print(f"[Config] Warning: Failed to read {PROMPT_FILE}: {e}")
    return "You are Bonesy, an enchanted, witty talking skeleton for Halloween."


SYSTEM_INSTRUCTION = load_system_instruction()

# Hardware & Devices
# Camera and mic: a device name (word match, see devices.py) or an index. List them with:
#   python3 devices.py
CAMERA_DEVICE = os.getenv("CAMERA_DEVICE", "USB Camera").strip()  # blank = camera 0
MIC_DEVICE = os.getenv("MIC_DEVICE", "USB Camera").strip()        # blank = system default mic
# Turn the picture right side up when the camera is mounted upside down
CAMERA_ROTATE_180 = os.getenv("CAMERA_ROTATE_180", "false").strip().lower() in ("true", "1", "yes")

# Audio Recording & VAD
SAMPLE_RATE = 16000  # 16kHz standard
CHANNELS = 1         # Mono
VAD_ENERGY_THRESHOLD = float(os.getenv("VAD_ENERGY_THRESHOLD", "0.03"))
SILENCE_DURATION_SEC = float(os.getenv("SILENCE_DURATION_SEC", "1.2"))
MAX_RECORDING_SEC = float(os.getenv("MAX_RECORDING_SEC", "15.0"))
MIN_SPEECH_DURATION_SEC = 0.5  # Ignore sub-second coughs/clicks

# Text to Speech (edge-tts)
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
TTS_PITCH = os.getenv("TTS_PITCH", "-15Hz")
TTS_RATE = os.getenv("TTS_RATE", "-5%")

# Spooky Audio FX (ffmpeg)
ENABLE_SPOOKY_FX = os.getenv("ENABLE_SPOOKY_FX", "true").lower() in ("true", "1", "yes")


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes")


# Conversation engine: "live" streams audio/video through the Gemini Live API (low latency);
# "classic" is the record -> generate_content -> TTS -> afplay pipeline.
ENGINE = os.getenv("ENGINE", "live").strip().lower()

# Gemini Live API
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
LIVE_VIDEO_FPS = float(os.getenv("LIVE_VIDEO_FPS", "1.0"))          # Live API accepts at most 1 frame/s
LIVE_VIDEO_MAX_DIM = int(os.getenv("LIVE_VIDEO_MAX_DIM", "768"))    # smaller frames = fewer tokens
LIVE_IDLE_TIMEOUT_SEC = float(os.getenv("LIVE_IDLE_TIMEOUT_SEC", "25"))  # close session -> next visitor
LIVE_MAX_TURNS = int(os.getenv("LIVE_MAX_TURNS", "6"))                  # max turns per visitor before memory reset
LIVE_MAX_SESSION_SEC = float(os.getenv("LIVE_MAX_SESSION_SEC", "90"))   # max total seconds per visitor session
LIVE_CAP_COOLDOWN_SEC = float(os.getenv("LIVE_CAP_COOLDOWN_SEC", "5"))  # ignore the mic this long after a cap goodbye
LIVE_END_SILENCE_MS = int(os.getenv("LIVE_END_SILENCE_MS", "800"))  # server VAD: silence that ends a turn
LIVE_ECHO_TAIL_SEC = float(os.getenv("LIVE_ECHO_TAIL_SEC", "1.2"))  # keep mic muted after speech (BT delay)
# Live voice. Measured pitch: Alnilam ~92 Hz, Gacrux ~93, Umbriel ~93, Algenib ~99 (gravelly), Puck ~109.
LIVE_VOICE_NAME = os.getenv("LIVE_VOICE_NAME", "Algenib").strip() or "Algenib"
# Real-time voice FX (applied when ENABLE_SPOOKY_FX=true): pitch shift + crypt reverb + echo
LIVE_PITCH_SEMITONES = float(os.getenv("LIVE_PITCH_SEMITONES", "-3"))  # negative = deeper; 0 = off
LIVE_ECHO_DELAY_MS = float(os.getenv("LIVE_ECHO_DELAY_MS", "220"))      # time between echo repeats
LIVE_ECHO_FEEDBACK = float(os.getenv("LIVE_ECHO_FEEDBACK", "0.35"))     # how much each repeat keeps (0-0.9)
LIVE_ECHO_MIX = float(os.getenv("LIVE_ECHO_MIX", "0.2"))               # echo loudness; 0 = no echo
# Default conversation language (BCP-47). Bonesy only switches if a visitor clearly speaks another language.
LIVE_LANGUAGE = os.getenv("LIVE_LANGUAGE", "en-US").strip() or "en-US"
LIVE_BARGE_IN = _env_bool("LIVE_BARGE_IN", "false")                 # let visitors interrupt (needs no echo!)
LIVE_KEEPALIVE_LEVEL = float(os.getenv("LIVE_KEEPALIVE_LEVEL", "0.001"))  # faint noise keeps BT speaker awake


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
MOTION_ROI = _env_roi("MOTION_ROI")                                 # optional 'x,y,w,h' area to watch
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

# Content safety filters, shared by both engines. LOW_AND_ABOVE flags ordinary Halloween talk
# (graves, blood, zombies), so MEDIUM_AND_ABOVE is the default.
SAFETY_THRESHOLD = os.getenv("SAFETY_THRESHOLD", "BLOCK_MEDIUM_AND_ABOVE").strip().upper()
SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold[SAFETY_THRESHOLD])
    for category in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]
