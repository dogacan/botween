import argparse
import sys
import threading
import time
from typing import List, Optional
from google import genai
from google.genai import types

import config
from audio import AudioListener
from camera import Camera
from preview import Preview
import tts


class SkeletonBrain:
    """Manages Gemini 2.0 Flash interaction and conversation memory."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(
                "Gemini API key is not set! Please set GEMINI_API_KEY in your .env file "
                "or export GEMINI_API_KEY='your_key'."
            )
        self.client = genai.Client(api_key=api_key)
        self.chat_history: List[types.Content] = []
        self.last_interaction_time = 0
        self.session_timeout_sec = 25.0  # Reset memory if no speech for 25s (new trick-or-treater)

    def _check_session_timeout(self):
        now = time.time()
        if self.chat_history and (now - self.last_interaction_time) > self.session_timeout_sec:
            print("\n[Brain] Visitor moved on. Resetting conversation for the next trick-or-treater! 🎃")
            self.chat_history.clear()
        self.last_interaction_time = now

    def generate_response(self, audio_bytes: bytes, image_bytes: Optional[bytes] = None) -> str:
        """Sends the audio utterance and latest camera image to Gemini 2.0 Flash."""
        self._check_session_timeout()

        parts = []
        # Add vision frame if available
        if image_bytes:
            parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

        # Add microphone audio recording
        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"))

        prompt_context = (
            "Here is the view from your eye sockets and the audio from the trick-or-treater. "
            "Inspect their costume and respond directly in character as Bonesy the skeleton."
        )
        parts.append(types.Part.from_text(text=prompt_context))

        user_content = types.Content(role="user", parts=parts)
        self.chat_history.append(user_content)

        # Call Gemini (reloads prompt.md dynamically so you can edit the prompt live)
        gen_config = types.GenerateContentConfig(
            system_instruction=config.load_system_instruction(),
            temperature=0.75,
            max_output_tokens=150,  # Keep responses short and punchy
        )

        response = self.client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=self.chat_history,
            config=gen_config,
        )

        reply_text = response.text.strip() if response.text else "Boo! Happy Halloween!"
        # Add model's answer to history for multi-turn dialogue
        self.chat_history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply_text)]))

        # Keep history bounded
        if len(self.chat_history) > 8:
            self.chat_history = self.chat_history[-6:]

        return reply_text


def interaction_loop(mode, brain, listener, cam, preview, stop_event):
    """Listen -> snapshot -> Gemini -> speak. Runs on a worker thread when the preview window is open."""

    def status(text):
        if preview:
            preview.set_status(text)

    try:
        while not stop_event.is_set():
            audio_bytes = None

            if mode == "manual":
                status("Waiting for ENTER (manual mode)")
                input("\n[Manual Mode] Press [ENTER] when you are ready to speak...")
                print("[Audio] Recording for up to 8 seconds or until silence...")
                status("Listening...")
                audio_bytes = listener.listen_for_speech()
            else:
                # Autonomous VAD loop
                status("Listening for trick-or-treaters...")
                audio_bytes = listener.listen_for_speech()

            if not audio_bytes:
                continue

            # Grab current camera snapshot
            image_bytes = None
            if cam:
                image_bytes = cam.get_latest_jpeg_bytes()
                if image_bytes:
                    print(f"[Camera] Captured visitor snapshot ({len(image_bytes) // 1024} KB).")
                    if preview:
                        preview.update_sent(image_bytes)  # exactly what the model will see

            print("[Brain] Thinking and inspecting visitor...")
            status("Thinking...")
            start_t = time.time()
            try:
                reply = brain.generate_response(audio_bytes=audio_bytes, image_bytes=image_bytes)
                latency = time.time() - start_t
                print(f"[Brain] Response in {latency:.2f}s: \"{reply}\"")

                print("[TTS] Speaking to trick-or-treater...")
                status("Speaking...")
                tts.speak(reply, block=True)
            except Exception as e:
                print(f"[Brain] Error generating response: {e}")
                status("Error - recovering")
                tts.speak("My old bones are rattling! Say that again, traveller!", block=True)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop_event.set()  # make sure the GUI loop (if any) exits too


def run_skeleton(mode: str = "auto", show_preview: bool = False, no_camera: bool = False):
    print("=" * 60)
    print("      💀 BONESY THE TALKING SKELETON - BOTWEEN 🎃")
    print("=" * 60)
    voice_info = f"Gemini ({config.GEMINI_VOICE_NAME})" if config.TTS_ENGINE == "gemini" else config.TTS_VOICE
    print(f"Mode: {mode.upper()} | Engine: {config.TTS_ENGINE.upper()} | Voice: {voice_info} | FX: {config.ENABLE_SPOOKY_FX}")

    if not config.GEMINI_API_KEY:
        print("\n❌ Error: GEMINI_API_KEY is not configured!")
        print("Please copy .env.example to .env and insert your API key:")
        print("  cp .env.example .env")
        print("  nano .env\n")
        sys.exit(1)

    if show_preview and no_camera:
        print("[Preview] --preview requires a camera; ignoring because --no-camera was set.")
        show_preview = False

    brain = SkeletonBrain(config.GEMINI_API_KEY)
    listener = AudioListener()

    # Calibrate background noise if running in autonomous VAD mode
    if mode == "auto":
        listener.calibrate_noise_floor(duration_sec=1.5)

    cam = None
    if not no_camera:
        cam = Camera()
        cam.start()

    preview = Preview(cam) if (show_preview and cam) else None
    stop_event = threading.Event()

    # Initial spooky awakening
    print("\n[Skeleton] Bonesy is awakening...")
    tts.speak("<cackle> Hehehe! </cackle> The bones are alive! <whisper> Who approaches my porch on this dark night? </whisper>", block=True)
    print("[Skeleton] Ready for trick-or-treaters!\n")

    try:
        if preview:
            # GUI must own the main thread on macOS; conversation runs in a worker.
            worker = threading.Thread(
                target=interaction_loop,
                args=(mode, brain, listener, cam, preview, stop_event),
                daemon=True,
            )
            worker.start()
            preview.run(stop_event)
        else:
            interaction_loop(mode, brain, listener, cam, None, stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        print("\n[Skeleton] Powering down. Happy Halloween!")
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

    args = parser.parse_args()
    run_skeleton(mode=args.mode, show_preview=args.preview, no_camera=args.no_camera)
