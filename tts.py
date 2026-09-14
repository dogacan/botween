import asyncio
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
import edge_tts

import config

# Global flag to signal when the skeleton is speaking (for half-duplex mute)
_is_speaking_event = threading.Event()
_current_player_process = None
_player_lock = threading.Lock()


def is_speaking() -> bool:
    """Returns True if the skeleton is currently playing audio."""
    return _is_speaking_event.is_set()


def stop_speaking():
    """Immediately stops any currently playing speech."""
    global _current_player_process
    with _player_lock:
        if _current_player_process and _current_player_process.poll() is None:
            try:
                _current_player_process.terminate()
            except Exception:
                pass
            _current_player_process = None
    _is_speaking_event.clear()


async def generate_speech_file(text: str, output_path: Path) -> Path:
    """Uses edge-tts to generate a spoken audio file."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=config.TTS_VOICE,
        pitch=config.TTS_PITCH,
        rate=config.TTS_RATE,
    )
    await communicate.save(str(output_path))
    return output_path


def apply_spooky_effects(input_file: Path, output_file: Path) -> Path:
    """
    Applies ffmpeg audio filters:
    - aecho: Multi-tap reverb simulating a hollow crypt chamber
    - bass boost: Enhances deep chest-cavity resonance
    - slight treble rolloff: Softens robotic edge
    """
    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg_bin):
        # If ffmpeg is not found, keep original audio
        return input_file

    # aecho parameters: in_gain:out_gain:delays:decays
    # 60ms delay + 120ms delay gives a hollow cavernous/stone crypt reverberation
    filter_chain = (
        "aecho=0.8:0.75:60|120:0.3|0.15,"
        "bass=g=5:f=120,"
        "treble=g=-2:f=3500"
    )

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_file),
        "-af", filter_chain,
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(output_file),
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_file
    except Exception as e:
        print(f"[TTS] Warning: ffmpeg FX filter failed ({e}), using raw TTS audio.")
        return input_file


def play_audio_file(file_path: Path, block: bool = True):
    """Plays an audio file using macOS native afplay with half-duplex mute locking."""
    global _current_player_process
    afplay_bin = shutil.which("afplay") or "/usr/bin/afplay"

    def _playback_thread():
        global _current_player_process
        _is_speaking_event.set()
        try:
            with _player_lock:
                _current_player_process = subprocess.Popen(
                    [afplay_bin, str(file_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            _current_player_process.wait()
        except Exception as e:
            print(f"[TTS] Playback error: {e}")
        finally:
            with _player_lock:
                _current_player_process = None
            # Brief 300ms tail delay before releasing mute to allow acoustic reflections to dissipate
            time.sleep(0.3)
            _is_speaking_event.clear()

    if block:
        _playback_thread()
    else:
        t = threading.Thread(target=_playback_thread, daemon=True)
        t.start()
        return t


def speak(text: str, block: bool = True):
    """Full pipeline: generates TTS, applies spooky FX, and plays the audio."""
    if not text.strip():
        return

    timestamp = int(time.time() * 1000)
    raw_file = config.AUDIO_TEMP_DIR / f"tts_raw_{timestamp}.mp3"
    fx_file = config.AUDIO_TEMP_DIR / f"tts_fx_{timestamp}.mp3"

    try:
        # Run async edge-tts generation
        asyncio.run(generate_speech_file(text, raw_file))

        if config.ENABLE_SPOOKY_FX:
            final_audio = apply_spooky_effects(raw_file, fx_file)
        else:
            final_audio = raw_file

        play_audio_file(final_audio, block=block)
    finally:
        # Cleanup temp audio files
        for f in (raw_file, fx_file):
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass
