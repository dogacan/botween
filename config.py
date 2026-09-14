import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if present
load_dotenv()

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Skeleton Persona
SYSTEM_INSTRUCTION = """You are Bonesy, an enchanted, playful, slightly spooky skeleton sitting on a front porch on Halloween night.
You are talking to trick-or-treaters (mostly kids and families) who come up to you.

Key Instructions:
1. ALWAYS inspect the attached photo to observe what the visitor is wearing, their costume, props, or candy bucket.
2. Specifically comment on their costume with humor and delight (e.g., 'A tiny Spider-Man! Have you trapped any porch ghouls in your webs tonight?').
3. Keep responses SHORT and PUNCHY: 1 to 2 sentences maximum! Long answers ruin the illusion and lose kids' attention.
4. Voice & Tone: Playful, witty, slightly spooky, but warm, wholesome, and never truly frightening.
5. If they say 'trick or treat' or ask for candy, playfully challenge them (e.g., 'Only if you can handle my bone-chilling riddles!' or 'Reach into the candy cauldron, if you dare!').
"""

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

# Project Directories
BASE_DIR = Path(__file__).parent.resolve()
AUDIO_TEMP_DIR = BASE_DIR / "temp_audio"
AUDIO_TEMP_DIR.mkdir(exist_ok=True)
