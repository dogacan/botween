import argparse
import sys
import threading

import config
from camera import Camera
from live_brain import LiveSkeleton
from preview import Preview


def run_skeleton(mode: str = "auto", show_preview: bool = False, no_camera: bool = False, no_motion: bool = False):
    print("=" * 60)
    print("      💀 BONESY THE TALKING SKELETON - BOTWEEN 🎃")
    print("=" * 60)
    print(f"Mode: {mode.upper()} | Engine: LIVE ({config.GEMINI_LIVE_MODEL}) | Voice: {config.LIVE_VOICE_NAME} | "
          f"FX: {f'{config.LIVE_PITCH_SEMITONES:+g} semitones + echo' if config.ENABLE_SPOOKY_FX else 'off'}")

    if not config.GEMINI_API_KEY:
        print("\n❌ Error: GEMINI_API_KEY is not configured!")
        print("Please copy .env.example to .env and insert your API key:")
        print("  cp .env.example .env")
        print("  nano .env\n")
        sys.exit(1)

    if show_preview and no_camera:
        print("[Preview] --preview requires a camera; ignoring because --no-camera was set.")
        show_preview = False

    cam = None
    if not no_camera:
        cam = Camera()
        cam.start()

    # Camera motion wakes Bonesy as visitors walk up (auto mode only).
    motion = None
    if cam and mode == "auto" and config.MOTION_ENABLED and not no_motion:
        from motion import MotionDetector

        motion = MotionDetector(cam)
        motion.start()

    preview = Preview(cam, motion) if (show_preview and cam) else None
    stop_event = threading.Event()
    conversation = LiveSkeleton(mode, cam, preview, stop_event, motion=motion).run_blocking

    try:
        if preview:
            # GUI must own the main thread on macOS; conversation runs in a worker.
            worker = threading.Thread(target=conversation, daemon=True)
            worker.start()
            preview.run(stop_event)
        else:
            conversation()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        print("\n[Skeleton] Powering down. Happy Halloween!")
        if motion:
            motion.stop()
        if cam:
            cam.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Talking Halloween Skeleton Prop")
    parser.add_argument(
        "--mode",
        choices=["auto", "manual"],
        default="auto",
        help="'auto' runs continuous VAD listening; 'manual' waits for Enter key before listening.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Open a window showing the live camera feed next to the exact frame last sent to Gemini.",
    )
    parser.add_argument(
        "--no-camera",
        action="store_true",
        help="Run audio-only without webcam.",
    )
    parser.add_argument(
        "--no-motion",
        action="store_true",
        help="Don't let camera motion wake Bonesy (voice only).",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Save each conversation (transcripts and the frames sent to Gemini) for replay.py "
             "(or set RECORD_SESSIONS=true).",
    )
    parser.add_argument(
        "--motion-debug",
        action="store_true",
        help="Print motion readings twice a second to tune the trigger (or set MOTION_DEBUG=true).",
    )
    parser.add_argument("--camera", help=f"Camera name or index (default from CAMERA_DEVICE: '{config.CAMERA_DEVICE}').")
    parser.add_argument("--mic", help=f"Microphone name or index (default from MIC_DEVICE: '{config.MIC_DEVICE}').")
    parser.add_argument("--list-devices", action="store_true", help="List cameras and microphones, then exit.")
    parser.add_argument("--rotate-180", action="store_true",
                        help="Rotate the camera picture 180° (for an upside-down mount; or set CAMERA_ROTATE_180=true).")

    args = parser.parse_args()
    if args.motion_debug:
        config.MOTION_DEBUG = True
    if args.record:
        config.RECORD_SESSIONS = True
    if args.rotate_180:
        config.CAMERA_ROTATE_180 = True
    if args.camera is not None:
        config.CAMERA_DEVICE = args.camera
    if args.mic is not None:
        config.MIC_DEVICE = args.mic
    if args.list_devices:
        from devices import print_devices
        print_devices()
        sys.exit(0)
    run_skeleton(mode=args.mode, show_preview=args.preview, no_camera=args.no_camera, no_motion=args.no_motion)
