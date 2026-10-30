import os
from pathlib import Path
from dotenv import load_dotenv

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


def load_system_instruction() -> str:
    """Loads the skeleton prompt from prompt.md, falling back to a default if missing."""
    if PROMPT_FILE.exists():
        try:
            return PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[Config] Warning: Failed to read {PROMPT_FILE}: {e}")
    return "You are Bonesy, an enchanted, witty talking skeleton for Halloween."


SYSTEM_INSTRUCTION = load_system_instruction()

# Hardware & Devices
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
_raw_audio_device = os.getenv("AUDIO_INPUT_DEVICE", "").strip()
if not _raw_audio_device:
    AUDIO_INPUT_DEVICE = None
elif _raw_audio_device.isdigit():
    AUDIO_INPUT_DEVICE = int(_raw_audio_device)
else:
    AUDIO_INPUT_DEVICE = _raw_audio_device

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

# Ultra Skelly hardware
SKELLY_BLE_ENABLED = _env_bool("SKELLY_BLE_ENABLED", "true")
SKELLY_BLE_ADDRESS = os.getenv("SKELLY_BLE_ADDRESS", "").strip()    # optional; macOS uses UUIDs, not MACs
SKELLY_BLE_NAME = os.getenv("SKELLY_BLE_NAME", "").strip()          # optional name hint, e.g. "Ultra Skelly v2"
SKELLY_VOLUME = os.getenv("SKELLY_VOLUME", "").strip()              # optional 0-255, set at startup
# Output device for the voice (substring match). Falls back to the system default if not found.
SKELLY_AUDIO_OUTPUT = os.getenv("SKELLY_AUDIO_OUTPUT", "Skelly").strip()
