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
