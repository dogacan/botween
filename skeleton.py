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


BLOCKED_FINISH_REASONS = {
    types.FinishReason.SAFETY,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.SPII,
}

# Said when the safety filter blocks a reply. It may be a false alarm on an innocent kid,
# so it redirects without accusing anyone.
BLOCKED_REPLY = "<gasp> Rattle my ribs, I lost my train of thought! Grab a treat and have a frightfully fun night!"
EMPTY_REPLY = "<cackle> Boo! </cackle> Happy Halloween!"
GOODBYE_HINT = (
    " This is your last reply to this visitor: wrap up with a short, friendly goodbye "
    "and tell them to rattle on to the next house."
)


def _was_blocked(response: types.GenerateContentResponse) -> bool:
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        return True
    return any(c.finish_reason in BLOCKED_FINISH_REASONS for c in response.candidates or [])


class SkeletonBrain:
    """Manages Gemini (config.GEMINI_MODEL) interaction and conversation memory."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError(
                "Gemini API key is not set! Please set GEMINI_API_KEY in your .env file "
                "or export GEMINI_API_KEY='your_key'."
            )
        self.client = genai.Client(api_key=api_key)
        self.chat_history: List[types.Content] = []
        self.last_interaction_time = 0
        self.session_timeout_sec = config.LIVE_IDLE_TIMEOUT_SEC  # Reset memory if no speech for 25s
        self.max_turns = config.LIVE_MAX_TURNS

    def _check_session_timeout(self):
        now = time.time()
        if self.chat_history and (now - self.last_interaction_time) > self.session_timeout_sec:
            print("\n[Brain] Visitor moved on. Resetting conversation for the next trick-or-treater! 🎃")
            self.chat_history.clear()
        self.last_interaction_time = now

    def generate_response(self, audio_bytes: bytes, image_bytes: Optional[bytes] = None) -> str:
        """Sends the audio utterance and latest camera image to Gemini."""
        self._check_session_timeout()
        last_turn = self.max_turns > 0 and len(self.chat_history) >= (self.max_turns - 1) * 2

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
        if last_turn:
            prompt_context += GOODBYE_HINT
        parts.append(types.Part.from_text(text=prompt_context))

        user_content = types.Content(role="user", parts=parts)
        self.chat_history.append(user_content)

        # Call Gemini (reloads prompt.md dynamically so you can edit the prompt live)
        gen_config = types.GenerateContentConfig(
            system_instruction=config.load_system_instruction(),
            temperature=0.75,
            max_output_tokens=150,  # Keep responses short and punchy
            safety_settings=config.SAFETY_SETTINGS,
        )

        response = self.client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=self.chat_history,
            config=gen_config,
        )

        if _was_blocked(response):
            # Forget the blocked turn; left in history it would get every later reply blocked too.
            print("[Brain] Safety filter blocked this turn. Dropping it from memory.")
            self.chat_history.pop()
            return BLOCKED_REPLY

        reply_text = response.text.strip() if response.text else EMPTY_REPLY
        # Add model's answer to history for multi-turn dialogue
        self.chat_history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply_text)]))

        if last_turn:
            print(f"[Brain] Max turns ({self.max_turns}) reached. Resetting conversation memory! 🎃")
            self.chat_history.clear()

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


def run_skeleton(mode: str = "auto", show_preview: bool = False, no_camera: bool = False, engine: str = config.ENGINE):
    print("=" * 60)
    print("      💀 BONESY THE TALKING SKELETON - BOTWEEN 🎃")
    print("=" * 60)
    if engine == "live":
        print(f"Mode: {mode.upper()} | Engine: LIVE ({config.GEMINI_LIVE_MODEL}) | Voice: {config.LIVE_VOICE_NAME} | "
              f"FX: {f'{config.LIVE_PITCH_SEMITONES:+g} semitones + echo' if config.ENABLE_SPOOKY_FX else 'off'}")
    else:
        voice_info = f"Gemini ({config.GEMINI_VOICE_NAME})" if config.TTS_ENGINE == "gemini" else config.TTS_VOICE
        print(f"Mode: {mode.upper()} | Engine: CLASSIC / TTS {config.TTS_ENGINE.upper()} | Voice: {voice_info} | FX: {config.ENABLE_SPOOKY_FX}")

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

    preview = Preview(cam) if (show_preview and cam) else None
    stop_event = threading.Event()

    if engine == "live":
        from live_brain import LiveSkeleton

        # Live engine handles Skelly Bluetooth, mic calibration and the awakening line itself.
        conversation = LiveSkeleton(mode, cam, preview, stop_event).run_blocking
    else:
        brain = SkeletonBrain(config.GEMINI_API_KEY)
        listener = AudioListener()

        # Calibrate background noise if running in autonomous VAD mode
        if mode == "auto":
            listener.calibrate_noise_floor(duration_sec=1.5)

        # Initial spooky awakening
        print("\n[Skeleton] Bonesy is awakening...")
        tts.speak("<cackle> Hehehe! </cackle> The bones are alive! <whisper> Who approaches my porch on this dark night? </whisper>", block=True)
        print("[Skeleton] Ready for trick-or-treaters!\n")

        def conversation():
            interaction_loop(mode, brain, listener, cam, preview, stop_event)

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
        "--engine",
        choices=["live", "classic"],
        default=config.ENGINE if config.ENGINE in ("live", "classic") else "live",
        help="'live' streams through the Gemini Live API (low latency); 'classic' records, then calls generate_content + TTS.",
    )

    args = parser.parse_args()
    run_skeleton(mode=args.mode, show_preview=args.preview, no_camera=args.no_camera, engine=args.engine)
