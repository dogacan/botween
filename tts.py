import asyncio
import os
import re
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


def strip_stage_tags(text: str) -> str:
    """Strips <whispers>, <short pause>, [short pause], *stress* marks, etc. so fallback TTS engines don't read them aloud."""
    cleaned = re.sub(r"</?[a-zA-Z _\-]+>", " ", text)
    cleaned = re.sub(r"\[[a-zA-Z _\-]+\]", " ", cleaned)
    cleaned = cleaned.replace("*", "")
    return " ".join(cleaned.split())


def generate_gemini_speech(text: str, output_path: Path, voice_name: str = None) -> Path:
    """Generates expressive speech with stage directions using a locked Gemini prebuilt voice."""
    from google import genai
    from google.genai import types

    chosen_voice = voice_name or config.GEMINI_VOICE_NAME
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    # Explicitly lock the voice identity across all calls
    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=chosen_voice
            )
        )
    )
    gen_config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=speech_config,
    )

    resp = client.models.generate_content(
        model=config.GEMINI_TTS_MODEL,
        contents=text,
        config=gen_config,
    )

    audio_data = None
    if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
        for part in resp.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                audio_data = part.inline_data.data
                break

    if not audio_data:
        raise ValueError(f"No inline audio data returned from {config.GEMINI_TTS_MODEL}")

    output_path.write_bytes(audio_data)
    return output_path


async def generate_edge_speech(text: str, output_path: Path) -> Path:
    """Uses edge-tts to generate a spoken audio file."""
    clean_text = strip_stage_tags(text)
    communicate = edge_tts.Communicate(
        text=clean_text,
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
    - slight treble rolloff: Softens harsh highs
    """
    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(ffmpeg_bin):
        return input_file

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
        print(f"[TTS] Warning: ffmpeg FX filter failed ({e}), using raw audio.")
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
            # Brief 300ms tail delay before releasing mute
            time.sleep(0.3)
            _is_speaking_event.clear()

    if block:
        _playback_thread()
    else:
        t = threading.Thread(target=_playback_thread, daemon=True)
        t.start()
        return t


def speak(text: str, block: bool = True):
    """Full pipeline: generates expressive TTS (Gemini or Edge), applies spooky FX, and plays audio."""
    if not text.strip():
        return

    timestamp = int(time.time() * 1000)
    raw_file = config.AUDIO_TEMP_DIR / f"tts_raw_{timestamp}.wav"
    fx_file = config.AUDIO_TEMP_DIR / f"tts_fx_{timestamp}.mp3"

    try:
        used_gemini = False
        if config.TTS_ENGINE == "gemini" and config.GEMINI_API_KEY:
            try:
                print(f"[TTS] Generating expressive voice via {config.GEMINI_TTS_MODEL} [Voice: {config.GEMINI_VOICE_NAME}]...")
                generate_gemini_speech(text, raw_file)
                used_gemini = True
            except Exception as e:
                print(f"[TTS] Warning: Gemini TTS failed ({e}), falling back to Edge-TTS...")

        if not used_gemini:
            edge_file = config.AUDIO_TEMP_DIR / f"tts_raw_{timestamp}.mp3"
            asyncio.run(generate_edge_speech(text, edge_file))
            raw_file = edge_file

        if config.ENABLE_SPOOKY_FX:
            final_audio = apply_spooky_effects(raw_file, fx_file)
        else:
            final_audio = raw_file

        play_audio_file(final_audio, block=block)
    finally:
        for f in (raw_file, fx_file):
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass
