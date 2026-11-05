import time
import numpy as np
import sounddevice as sd

import config
from audio import AudioListener
from camera import Camera
from devices import list_cameras, list_microphones
from skelly_ble import find_output_device
from voice_fx import VoiceFX, apply_gain

OUT_RATE = 24000


def test_speaker_output():
    print("\n--- 1. Testing Speaker Output ---")
    device = find_output_device(config.SKELLY_AUDIO_OUTPUT)
    if device is not None:
        dev_name = sd.query_devices(device)["name"]
        print(f"Selected output device: [{device}] {dev_name}")
    else:
        default_out = sd.default.device[1]
        dev_name = sd.query_devices(default_out)["name"] if default_out is not None and default_out >= 0 else "System Default"
        print(f"No output matching '{config.SKELLY_AUDIO_OUTPUT}'; using system default: {dev_name}")

    # Synthesize a short two-note spooky organ chime at 24 kHz and run it through the Live VoiceFX pipeline
    duration = 0.8
    t = np.linspace(0, duration, int(OUT_RATE * duration), endpoint=False, dtype=np.float32)
    envelope = np.exp(-3.0 * t) * np.minimum(1.0, t / 0.02)
    wave = 0.25 * (np.sin(2 * np.pi * 220.0 * t) + 0.6 * np.sin(2 * np.pi * 311.13 * t)) * envelope
    pcm = (np.clip(wave, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    if config.ENABLE_SPOOKY_FX:
        fx = VoiceFX(
            OUT_RATE,
            semitones=config.LIVE_PITCH_SEMITONES,
            echo_delay_ms=config.LIVE_ECHO_DELAY_MS,
            echo_feedback=config.LIVE_ECHO_FEEDBACK,
            echo_mix=config.LIVE_ECHO_MIX,
        )
        pcm = fx.process(pcm) + fx.flush()
    pcm = apply_gain(pcm, config.LIVE_OUTPUT_GAIN)

    samples = np.frombuffer(pcm, dtype=np.int16)
    print(f"Playing test chime (FX={'on' if config.ENABLE_SPOOKY_FX else 'off'}, gain={config.LIVE_OUTPUT_GAIN:g}x)...")
    sd.play(samples, samplerate=OUT_RATE, device=device, blocking=True)
    print("✅ Speaker playback finished!")


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
    parser.add_argument("--speaker-only", action="store_true", help="Only test speaker playback without mic/camera")
    parser.add_argument("--camera", default=None, help="Camera name or index (default from CAMERA_DEVICE)")
    parser.add_argument("--mic", default=None, help="Microphone name or index (default from MIC_DEVICE)")
    parser.add_argument("--rotate-180", action="store_true", help="Rotate the camera picture 180° (or set CAMERA_ROTATE_180=true)")
    args = parser.parse_args()
    if args.rotate_180:
        config.CAMERA_ROTATE_180 = True
    if args.camera is not None:
        config.CAMERA_DEVICE = args.camera
    if args.mic is not None:
        config.MIC_DEVICE = args.mic

    print("==========================================")
    print("   Botween Hardware & Audio Diagnostic   ")
    print("==========================================")

    test_speaker_output()
    if not args.speaker_only:
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
