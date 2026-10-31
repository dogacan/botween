import os
import sys
import time
from pathlib import Path
import numpy as np
import sounddevice as sd

import config
import tts
from audio import AudioListener
from camera import Camera
from devices import list_cameras, list_microphones


def test_spooky_tts(voice_override=None):
    print("\n--- 1. Testing Spooky TTS Voice ---")
    if voice_override:
        config.GEMINI_VOICE_NAME = voice_override
    phrase = "<gasp> Mwahahaha! <whisper> Welcome to the front yard of doom! </whisper> <cackle> Your costume looks delightfully spooky! </cackle>"
    print(f"Speaking: '{phrase}'")
    if config.TTS_ENGINE == "gemini":
        print(f"Engine: Gemini ({config.GEMINI_TTS_MODEL}) | Locked Voice: {config.GEMINI_VOICE_NAME} | FX: {config.ENABLE_SPOOKY_FX}")
    else:
        print(f"Engine: Edge-TTS | Voice: {config.TTS_VOICE} | Pitch: {config.TTS_PITCH} | FX: {config.ENABLE_SPOOKY_FX}")
    start_t = time.time()
    tts.speak(phrase, block=True)
    print(f"Speech playback finished in {time.time() - start_t:.2f}s.")


def test_microphone():
    print("\n--- 2. Testing Microphone Input ---")
    print("Listing available input audio devices:")
    for idx, name in list_microphones():
        default_marker = " (Default)" if idx == sd.default.device[0] else ""
        print(f"  [{idx}] {name}{default_marker}")

    listener = AudioListener()
    print("\nMeasuring ambient room noise for 1.5 seconds...")
    threshold = listener.calibrate_noise_floor(duration_sec=1.5)
    print(f"Calibrated threshold: {threshold:.4f}")

    print("\nPlease say something now (recording 3 seconds of audio)...")
    rec = sd.rec(
        int(3 * config.SAMPLE_RATE),
        samplerate=config.SAMPLE_RATE,
        channels=config.CHANNELS,
        device=listener.device,
    )
    sd.wait()
    peak = float(np.max(np.abs(rec)))
    rms = float(np.sqrt(np.mean(rec**2)))
    print(f"Recorded 3 seconds -> Peak Level: {peak:.4f}, RMS Level: {rms:.4f}")
    if peak > 0.01:
        print("✅ Microphone is working properly!")
    else:
        print("⚠️ Warning: Microphone input was very quiet. Check system microphone permissions or input gain.")


def test_camera():
    print("\n--- 3. Testing Webcam Capture ---")
    print("Available cameras:")
    for idx, name in list_cameras():
        print(f"  [{idx}] {name}")
    cam = Camera()
    cam.start()
    print("Waiting for camera to warm up...")
    time.sleep(1.0)
    jpeg_bytes = cam.get_latest_jpeg_bytes()
    cam.stop()

    if jpeg_bytes and len(jpeg_bytes) > 1000:
        out_path = config.BASE_DIR / "test_snapshot.jpg"
        with open(out_path, "wb") as f:
            f.write(jpeg_bytes)
        print(f"✅ Camera working! Captured snapshot ({len(jpeg_bytes) // 1024} KB).")
        print(f"Saved test image to: {out_path}")
    else:
        print(f"⚠️ Warning: Could not capture frame from camera [{cam.camera_index}] {cam.camera_name}.")
        print("Make sure Terminal / Python has Camera permissions in macOS System Settings.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Botween Hardware & Audio Diagnostic")
    parser.add_argument("--voice", type=str, default=None, help="Test a specific Gemini voice (e.g. Puck, Charon, Fenrir, Kore)")
    parser.add_argument("--tts-only", action="store_true", help="Only test voice playback without mic/camera")
    parser.add_argument("--camera", default=None, help="Camera name or index (default from CAMERA_DEVICE)")
    parser.add_argument("--mic", default=None, help="Microphone name or index (default from MIC_DEVICE)")
    args = parser.parse_args()
    if args.camera is not None:
        config.CAMERA_DEVICE = args.camera
    if args.mic is not None:
        config.MIC_DEVICE = args.mic

    print("==========================================")
    print("   Botween Hardware & Audio Diagnostic   ")
    print("==========================================")

    test_spooky_tts(voice_override=args.voice)
    if not args.tts_only:
        test_microphone()
        test_camera()

    print("\n==========================================")
    print("Diagnostic complete!")
    print("To run the full skeleton prop:")
    print("  python3 skeleton.py --mode manual   # for indoor keyboard test")
    print("  python3 skeleton.py --mode auto     # for hands-free VAD mode")
    print("==========================================")


if __name__ == "__main__":
    main()
