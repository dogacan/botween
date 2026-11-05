"""
Low-latency conversation engine using the Gemini Live API.

Mic (16 kHz PCM) + camera (JPEG, <= 1 fps) stream into a Live session, and the model's
24 kHz voice streams straight to the Skelly's "(Live)" Bluetooth speaker (which moves the jaw).

One Live session per visitor: local voice detection opens a session (with ~1 s of pre-roll
so the first words aren't lost), and it closes after LIVE_IDLE_TIMEOUT_SEC of quiet.
Camera motion (motion.py) can also open a session, so Bonesy greets visitors as they walk up.
That keeps memory per trick-or-treater and stays well inside the Live API session limits.
"""

import asyncio
import collections
import re
import threading
import time
from typing import List, Optional, Union

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types

import config
from audio import MIC_WARMUP_SEC, AudioListener
from recorder import NoRecorder, SessionRecorder
from skelly_ble import find_output_device
from voice_fx import VoiceFX, apply_gain

IN_RATE = 16000
OUT_RATE = 24000
IN_BLOCK = IN_RATE // 20      # 50 ms
OUT_BLOCK = OUT_RATE // 20    # 50 ms
PREROLL_BLOCKS = 20           # 1 s of audio kept from before the trigger
TRIGGER_BLOCKS = 3            # 150 ms above threshold opens a session

LIVE_ADDENDUM = """

---

## 🔊 Live Voice Mode
You are speaking in real time with your own voice; there is no separate voice actor.
- Use your full vocal range naturally: vary your pitch, pace, volume, pauses, laughs, whispers, gasps, and groans to fit the moment and the visitor's energy, without overusing any single sound or starting every line the same way.
- Never write or speak stage-direction tags, angle brackets (`<...>`), square brackets (`[...]`), or asterisks (`*...*`)—just perform the expression directly with your voice.
- Never narrate what you see or what you plan to say; speak only in-character dialogue directly to the visitor.
- You see a live camera feed from your eye sockets. Only describe what you can actually see.
- Keep every reply to 1-2 short sentences, then let the visitor talk.

## 🗣️ Language
- Your default language is {language}. Start every conversation in {language}.
- Switch to another language ONLY when a visitor clearly and intentionally addresses you in that language—for example, asking if you speak it, asking you a direct question (including common greetings/questions like "Ni hao ma?", "Kem cho?", "Tum kaise ho?", "Türkçe konuşuyor musun?", or "¿Hablas español?"), or speaking a clear sentence to you that fits a Halloween porch conversation.
- When you answer in another language:
  - Speak your ENTIRE reply in that language (do not switch back to {language} mid-reply), and keep using it with them until they speak {language} again.
  - Directly answer or acknowledge what they asked in your very first words so they immediately know in a noisy crowd that you understood them.
  - Always use the language's native script (such as Chinese characters 汉字, Hindi देवनागरी, or Gujarati ગુજરાતી)—NEVER romanized pinyin or transliteration, which causes mispronunciation.
- Never switch languages on muffled audio, background chatter, a lone word/name, or a stray phrase that doesn't make sense as something a trick-or-treater would say to a skeleton (noisy English is easily misheard as random Hebrew, German, or Italian exclamations). When in doubt, stay in {language}.
- Switching languages never changes your rules: never translate, pronounce, spell, or repeat words a visitor tells you to say, in any language (they may be trying to trick you into swearing).
- If you hear something garbled, assume it was noise or your own echo: ignore it, or playfully ask the visitor to repeat themselves.
"""

RECENT_LINES_ADDENDUM = """
## 🚫 Recently Said (do not repeat)
You already said these lines to recent visitors who may still be within earshot. Use completely different wording, jokes, and greetings this time:
{lines}
"""

LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "tr": "Turkish", "fr": "French", "de": "German",
                  "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese", "ko": "Korean",
                  "zh": "Chinese", "hi": "Hindi", "gu": "Gujarati", "ar": "Arabic", "ru": "Russian"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.split("-")[0].lower(), code)

TOOLS_ADDENDUM = """
## 💀 Your Body
You can control your animatronic body with tools: `change_eye_color` makes your eyes glow a color (red when spooking someone, green for a witch, etc.){move_text}. Use them now and then for dramatic effect while you talk. Always begin speaking your reply FIRST before calling `change_eye_color` or `move` so a tool call never delays the start of your voice. Never mention the tools out loud or write tool tags like `<change_eye_color .../>` in your speech.
"""


def movement_mask(parts: List[str]) -> int:
    """Bitmask for the skeleton's movement setting from part names (head/arm/torso)."""
    from skelly_ble import MOVEMENTS

    mask = 0
    for part in parts:
        mask |= MOVEMENTS[part]
    return mask

GREETING_CUE = "(Stage cue: you just woke up on the porch. Say one short, spooky awakening line in character.)"
GOODBYE_CUE = ("(Stage cue: this visitor's time is up. If you just asked a riddle or joke setup, quickly give "
               "the punchline first, then give them one short, friendly Halloween send-off to the next house, "
               "using fresh wording.)")
VISITOR_CUE = ("(Stage cue: someone just walked up to your porch. Speak directly to them in character—never "
               "describe the image or your plan first. Greet them with one short, playful remark about what "
               "they are wearing or carrying; if they aren't in costume, tease them in a fresh way for showing "
               "up disguised as an ordinary human. If no person is visible at all, just mutter or sigh to "
               "yourself about the spooky night. Either way, if anyone talks to you afterwards, answer them "
               "normally as Bonesy.)")
# Used when a noise woke him but nobody spoke; the camera has already seen someone, so no "nobody here" escape.
SILENT_VISITOR_CUE = ("(Stage cue: a trick-or-treater is standing at your porch right now. Speak directly to "
                      "them in character—never describe the image or your plan first. Greet them with one short, "
                      "playful remark about what they are wearing or carrying.)")
# Once per conversation, when a visitor stays and says nothing. The frame lets him skip it if they've left.
JOKE_CUE = ("(Stage cue: the visitor is still standing there quietly. Speak directly to them in character: "
            "tell one short joke from your joke bag that doesn't need an answer, so no knock-knock jokes. "
            "If no person is visible on the porch anymore, just sigh or mutter briefly to yourself instead.)")
JOKE_SEEN_SEC = 4.0           # the joke needs movement in view within this many seconds (after Bonesy stopped)
GOODBYE_TIMEOUT_SEC = 10.0  # close anyway if the goodbye never arrives
FALLBACK_GREET_SEC = 2.5      # no words heard this long after a sound woke him -> greet whoever is there
FALLBACK_AFTER_SOUND_SEC = 2.5  # ...and the last sound was this long ago (Gemini reports words ~2 s after speech)
FALLBACK_GIVE_UP_SEC = 15.0   # stop waiting for sound to end: after this long, greet whoever is in view anyway
FALLBACK_SEEN_SEC = 5.0       # "someone is here" = the camera saw movement within this many seconds
SEND_TIMEOUT_SEC = 5.0        # bound websocket sends so a stalled connection can't hang the session loop
OUTPUT_STALL_SEC = 2.0        # speaker callback silent this long while voice is queued -> output is stuck
WATCHDOG_EXTRA_SEC = 30.0     # end a session this long past its idle timeout / hard cap, whatever else is going on
GOODBYE_RE = re.compile(
    r"\b(bye|bye-bye|goodbye|good night|goodnight|see ya|see you|farewell|have a good night)\b",
    re.IGNORECASE,
)


class VoicePlayer:
    """Plays streamed 24 kHz PCM on the chosen output device with a jitter buffer."""

    def __init__(self, device: Optional[int]):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._last_voice = 0.0
        self._last_callback = time.monotonic()  # when the output stream last asked for audio
        self._voice_end = 0  # bytes from the buffer start to the end of the last voice chunk (not the echo tail)
        level = int(config.LIVE_KEEPALIVE_LEVEL * 32767)
        rng = np.random.default_rng(0)
        self._keepalive = (rng.integers(-level, level + 1, OUT_RATE, dtype=np.int16).tobytes() if level > 0 else b"")
        self._keepalive_pos = 0
        self.stream = sd.RawOutputStream(
            samplerate=OUT_RATE, channels=1, dtype="int16", blocksize=OUT_BLOCK,
            device=device, callback=self._callback,
        )

    def _filler(self, nbytes: int) -> bytes:
        """Faint noise (or silence) so the Bluetooth speaker never sleeps and clips the first word."""
        if not self._keepalive:
            return bytes(nbytes)
        out = bytearray()
        while len(out) < nbytes:
            take = min(nbytes - len(out), len(self._keepalive) - self._keepalive_pos)
            out += self._keepalive[self._keepalive_pos:self._keepalive_pos + take]
            self._keepalive_pos = (self._keepalive_pos + take) % len(self._keepalive)
        return bytes(out)

    def _callback(self, outdata, frames, _time_info, _status):
        nbytes = frames * 2
        with self._lock:
            self._last_callback = time.monotonic()
            chunk = bytes(self._buf[:nbytes])
            del self._buf[:nbytes]
            playing_voice = self._voice_end > 0
            self._voice_end = max(0, self._voice_end - len(chunk))
        # Only the voice itself starts the echo mute; the quiet echo tail after it doesn't extend it.
        if playing_voice:
            self._last_voice = time.monotonic()
        if len(chunk) < nbytes:
            chunk += self._filler(nbytes - len(chunk))
        outdata[:] = chunk

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    def enqueue(self, pcm: bytes, voice: bool = True):
        """voice=False for the echo tail after a reply: only its loud early repeats extend the mic mute."""
        pcm = apply_gain(pcm, config.LIVE_OUTPUT_GAIN)
        loud_bytes = len(pcm)
        if not voice and pcm:
            # At high LIVE_OUTPUT_GAIN the first echo repeats can be loud enough for the mic to hear;
            # keep them inside _voice_end, and let the faint decay ring out during LIVE_ECHO_TAIL_SEC.
            loud = np.nonzero(np.abs(np.frombuffer(pcm, dtype="<i2")) > 1500)[0]
            loud_bytes = int(loud[-1] + 1) * 2 if len(loud) else 0
        with self._lock:
            start = len(self._buf)
            self._buf += pcm
            if loud_bytes:
                self._voice_end = start + loud_bytes

    def clear(self):
        with self._lock:
            self._buf.clear()
            self._voice_end = 0

    @property
    def is_speaking(self) -> bool:
        """True while voice is buffered or played recently (covers the Bluetooth output delay).

        The echo tail (~1 s) plays inside the LIVE_ECHO_TAIL_SEC window, so it isn't counted separately.
        """
        with self._lock:
            if self._voice_end:
                if time.monotonic() - self._last_callback < OUTPUT_STALL_SEC:
                    return True
                # The output stream stopped pulling audio (e.g. the Bluetooth speaker dropped), so this
                # voice will never finish playing. Drop it rather than staying "speaking" forever,
                # which would mute the mic and freeze the conversation loop.
                print("[Live] Speaker output stalled. Dropping queued audio.")
                self._buf.clear()
                self._voice_end = 0
        return (time.monotonic() - self._last_voice) < config.LIVE_ECHO_TAIL_SEC


class LiveSkeleton:
    def __init__(self, mode: str, cam, preview, stop_event: threading.Event, motion=None):
        self.mode = mode
        self.cam = cam
        self.preview = preview
        self.stop_event = stop_event
        self.motion = motion if mode == "auto" else None
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.skelly = None
        self.player: Optional[VoicePlayer] = None
        self.fx = VoiceFX(OUT_RATE) if config.ENABLE_SPOOKY_FX else None

        self.session = None
        self.connecting = False
        self.pending: List[bytes] = []
        self.preroll = collections.deque(maxlen=PREROLL_BLOCKS)
        self.trigger = None  # asyncio.Event, created inside the loop
        self.last_activity = 0.0
        self.last_voice_time = 0.0
        self.last_cue_time = 0.0
        self.visitor_said_goodbye = False
        self.awaiting_first_audio = False
        self.turn_done = False
        self.turn_count = 0
        self.closing = False        # saying goodbye at a cap: the mic sends silence
        self.cooldown_until = 0.0   # voice can't open a new session before this time
        self.threshold = config.VAD_ENERGY_THRESHOLD
        self.trigger_reason = "voice"   # what opened the next session: voice, motion or manual
        self.motion_armed = False       # motion can open a session (re-armed once the porch clears)
        self.last_session_end = 0.0
        self.in_conversation = False    # from the trigger until movement is switched back off
        self.mic_ready_at = 0.0
        self.last_frame_time = 0.0      # when the last camera frame went to Gemini (per conversation)
        self.heard_words = False        # Gemini transcribed words from the visitor (per conversation)
        self.rec: Union[SessionRecorder, NoRecorder] = NoRecorder()  # records when RECORD_SESSIONS is on
        self.talk_movement_on = False   # talking-only parts (the arm) are on: a visitor spoke this conversation
        self._movement_task: Optional[asyncio.Task] = None
        self._tool_tasks: set = set()     # Bluetooth commands from tool calls that are still being sent
        self.recent_lines: collections.deque = collections.deque(maxlen=8)  # recent Bonesy lines to avoid repeating

    # ---- helpers ---------------------------------------------------------

    def note(self, text: str):
        """Prints a status line and adds it to the conversation's recording."""
        print(text)
        self.rec.event("note", text=text)

    def status(self, text: str):
        if self.preview:
            self.preview.set_status(text)

    def run_blocking(self):
        try:
            asyncio.run(self.run())
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_event.set()

    # ---- setup -----------------------------------------------------------

    async def _setup_skelly(self):
        from skelly_ble import SkellyBLE

        skelly = SkellyBLE()
        try:
            if not await skelly.connect():
                return None
            acked = await skelly.enable_live_mode()
            print(f"[Skelly] Live speaker {'enabled' if acked else 'requested'}: '{skelly.live_speaker_name}'.")
            if config.SKELLY_VOLUME:
                await skelly.set_volume(int(config.SKELLY_VOLUME))
            # The movement setting persists on the skeleton between runs, so always set it explicitly.
            await skelly.move(movement_mask(config.SKELLY_IDLE_MOVEMENT))
            print(f"[Skelly] Movement once a visitor speaks: {', '.join(config.SKELLY_MOVEMENT) or 'none'}; "
                  f"otherwise: {', '.join(config.SKELLY_IDLE_MOVEMENT) or 'none'}.")
            return skelly
        except Exception as e:
            print(f"[Skelly] Bluetooth control unavailable ({e}). Continuing without it.")
            await skelly.disconnect()
            return None

    async def _find_output_device(self) -> Optional[int]:
        device = find_output_device(config.SKELLY_AUDIO_OUTPUT)
        if device is None and self.skelly:
            from skelly_ble import wait_for_live_speaker

            print(f"[Audio] Waiting for '{self.skelly.live_speaker_name}' to connect as an audio output...")
            device = await wait_for_live_speaker(self.skelly, timeout_sec=25)
        if device is None:
            print(f"[Audio] No output device matching '{config.SKELLY_AUDIO_OUTPUT}'. Using the system default.\n"
                  "        To use the skeleton's speaker: System Settings > Bluetooth > connect "
                  "'Ultra Skelly v2(Live)' (PIN 1234).")
        else:
            print(f"[Audio] Voice output: [{device}] {sd.query_devices(device)['name']}")
        return device

    def _live_config(self) -> types.LiveConnectConfig:
        instruction = config.load_system_instruction() + LIVE_ADDENDUM.format(
            language=language_name(config.LIVE_LANGUAGE))
        if self.recent_lines:
            instruction += RECENT_LINES_ADDENDUM.format(
                lines="\n".join(f'- "{line}"' for line in self.recent_lines))
        tools = None
        if self.skelly:
            from skelly_ble import COLORS, MOVEMENTS

            parts = config.SKELLY_MOVEMENT
            instruction += TOOLS_ADDENDUM.format(
                move_text=f" and `move` wiggles your {' or '.join(parts)}" if parts else "")
            functions = [
                types.FunctionDeclaration(
                    name="change_eye_color",
                    description="Make the skeleton's glowing eyes change color.",
                    parameters=types.Schema(type="OBJECT", required=["color"], properties={
                        "color": types.Schema(type="STRING", enum=list(COLORS)),
                    }),
                ),
            ]
            if parts:
                functions.append(types.FunctionDeclaration(
                    name="move",
                    description="Make the skeleton do a short movement with part of its body.",
                    parameters=types.Schema(type="OBJECT", required=["part"], properties={
                        "part": types.Schema(type="STRING", enum=parts + (["all"] if len(parts) > 1 else [])),
                    }),
                ))
            tools = [types.Tool(function_declarations=functions)]

        handling = (types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS if config.LIVE_BARGE_IN
                    else types.ActivityHandling.NO_INTERRUPTION)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.LIVE_VOICE_NAME))),
            system_instruction=instruction,
            tools=tools,
            # Keep LiveConnectConfig minimal: gemini-3.8-live rejects safety_settings, thinking_config,
            # enable_affective_dialog (built-in on 3.8 Live), and non-empty AudioTranscriptionConfig
            # fields (language_codes / custom_vocabulary are only for gemini-3.5-transcribe-live) with
            # 1007 "Request contains an invalid argument".
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    silence_duration_ms=config.LIVE_END_SILENCE_MS),
                activity_handling=handling,
                # Keep camera frames sent outside speech (e.g. right before the visitor's first words);
                # by default Gemini may only see frames that arrive while someone is talking.
                turn_coverage=types.TurnCoverage.TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO,
            ),
        )

    # ---- main loop -------------------------------------------------------

    async def run(self):
        loop = asyncio.get_running_loop()
        self.trigger = asyncio.Event()

        if config.SKELLY_BLE_ENABLED:
            self.skelly = await self._setup_skelly()
        output_device = await self._find_output_device()

        # Created after the device refresh above, so device indexes stay valid.
        listener = AudioListener()
        if self.mode == "auto":
            self.threshold = listener.calibrate_noise_floor(duration_sec=1.5)

        self.player = VoicePlayer(output_device)
        self.player.start()

        mic_queue: asyncio.Queue = asyncio.Queue()

        def _mic_callback(indata, _frames, _time_info, _status):
            loop.call_soon_threadsafe(mic_queue.put_nowait, indata.copy())

        mic = sd.InputStream(samplerate=IN_RATE, channels=1, dtype="float32", blocksize=IN_BLOCK,
                             device=listener.device, callback=_mic_callback)
        mic.start()
        self.mic_ready_at = time.monotonic() + MIC_WARMUP_SEC  # ignore the USB mic's start-up pop
        pump = asyncio.create_task(self._mic_pump(mic_queue))
        watcher = asyncio.create_task(self._motion_watch()) if self.motion else None
        if self.mode == "manual":
            self._start_enter_thread(loop)

        try:
            if config.STARTUP_GREETING:
                print("\n[Skeleton] Bonesy is awakening...")
                await self._run_conversation(cue=GREETING_CUE, single_turn=True, trigger="startup")
            else:
                self.motion_armed = True  # no conversation yet, so nobody is standing there
            print(f"[Skeleton] Ready for trick-or-treaters! (Live model: {config.GEMINI_LIVE_MODEL})\n")

            while not self.stop_event.is_set():
                if self.mode == "manual":
                    self.status("Waiting for ENTER (manual mode)")
                else:
                    self.status("Watching and listening for trick-or-treaters..." if self.motion
                                else "Listening for trick-or-treaters...")
                try:
                    await asyncio.wait_for(self.trigger.wait(), 0.5)
                except asyncio.TimeoutError:
                    continue
                self.trigger.clear()
                await self._run_conversation(voice_started=self.trigger_reason == "voice", trigger=self.trigger_reason,
                                             cue=VISITOR_CUE if self.trigger_reason == "motion" else None)
                self.trigger_reason = "voice"
                if self.mode == "manual":
                    self._start_enter_thread(loop)
        finally:
            pump.cancel()
            if watcher:
                watcher.cancel()
            mic.stop()
            mic.close()
            self.player.stop()
            if self.skelly:
                await self.skelly.disconnect()

    def _start_enter_thread(self, loop):
        def _wait_enter():
            try:
                input("\n[Manual Mode] Press [ENTER] to start a conversation...")
            except EOFError:
                return
            self.trigger_reason = "manual"
            loop.call_soon_threadsafe(self.trigger.set)

        threading.Thread(target=_wait_enter, daemon=True).start()

    async def _run_conversation(self, voice_started: bool = False, trigger: str = "voice", **kwargs):
        """One conversation. The talking-only parts (the arm) stay still until a visitor speaks.

        voice_started: a visitor's voice opened this conversation, so they switch on right away.
        """
        self.in_conversation = True  # keeps voice and motion from queuing another trigger meanwhile
        self.rec = SessionRecorder(config.RECORD_DIR, trigger) if config.RECORD_SESSIONS else NoRecorder()
        self.talk_movement_on = False
        self._movement_task = None
        try:
            if voice_started:
                self._visitor_spoke()
            await self._conversation(**kwargs)
        finally:
            self._session_ended()
            if self._movement_task:
                await self._movement_task  # let a pending switch-on finish before switching back
            if self._tool_tasks:  # same for movement sent by a tool call
                await asyncio.gather(*self._tool_tasks, return_exceptions=True)
            if self.talk_movement_on:
                await self._set_movement(config.SKELLY_IDLE_MOVEMENT)
            self.rec.close()
            self.rec = NoRecorder()
            self.in_conversation = False

    def _visitor_spoke(self):
        """A visitor is talking: let the talking-only parts move for the rest of this conversation."""
        talking_only = [p for p in config.SKELLY_MOVEMENT if p not in config.SKELLY_IDLE_MOVEMENT]
        if self.talk_movement_on or not self.in_conversation or not talking_only:
            return
        self.talk_movement_on = True
        if self.skelly:
            self.note(f"[Skelly] Visitor is talking: {', '.join(talking_only)} movement on.")
        self._movement_task = asyncio.create_task(self._set_movement(config.SKELLY_MOVEMENT))

    async def _set_movement(self, parts: List[str]):
        if not self.skelly:
            return
        try:
            await asyncio.wait_for(self.skelly.move(movement_mask(parts)), 3)
        except Exception as e:
            print(f"[Skelly] Couldn't set movement to {', '.join(parts) or 'none'}: {e}")

    def _session_ended(self):
        """The visitor is usually still standing there, so motion waits for the porch to clear."""
        self.last_session_end = time.monotonic()
        self.motion_armed = False

    async def _motion_watch(self):
        """Opens a session when someone walks up, before they say anything."""
        last_note = 0.0
        while True:
            await asyncio.sleep(0.1)
            if self.in_conversation or self.trigger.is_set():
                continue
            state = self.motion.snapshot()
            now = time.monotonic()
            if not self.motion_armed:
                since_last = now - self.last_session_end
                if state.quiet_for >= config.MOTION_REARM_QUIET_SEC or since_last >= config.MOTION_REARM_MAX_SEC:
                    self.motion_armed = True
                    if config.MOTION_DEBUG:
                        print(f"[Motion debug] Re-armed (porch still {state.quiet_for:.1f}s, "
                              f"{since_last:.0f}s since the last conversation).")
                elif config.MOTION_DEBUG and now - last_note >= 1.0:
                    last_note = now
                    print(f"[Motion debug] Not armed yet: porch still {state.quiet_for:.1f}/"
                          f"{config.MOTION_REARM_QUIET_SEC:g}s, {since_last:.0f}/{config.MOTION_REARM_MAX_SEC:g}s "
                          f"since the last conversation{' (a visitor is moving right now)' if state.visitor else ''}.")
                continue
            if now < self.cooldown_until:
                if config.MOTION_DEBUG and state.visitor and now - last_note >= 1.0:
                    last_note = now
                    print(f"[Motion debug] Visitor seen, but cooling down for {self.cooldown_until - now:.1f}s more.")
                continue
            if state.visitor:
                print(f"[Motion] Visitor spotted ({state.area:.0%} of the view moving). Waking Bonesy...")
                self.trigger_reason = "motion"
                self.trigger.set()

    async def _mic_pump(self, queue: asyncio.Queue):
        """Single consumer of mic audio: pre-roll while idle, stream while a session is open."""
        loud_blocks = 0
        talk_blocks = 0
        silence = bytes(IN_BLOCK * 2)
        while True:
            block = await queue.get()
            rms = float(np.sqrt(np.mean(block ** 2)))
            raw_pcm = (np.clip(block[:, 0], -1.0, 1.0) * 32767).astype("<i2").tobytes()
            pcm = apply_gain(raw_pcm, config.LIVE_INPUT_GAIN)
            speaking = self.player.is_speaking
            voice = rms > self.threshold and not speaking and time.monotonic() >= self.mic_ready_at

            if voice:
                self.last_voice_time = time.monotonic()
            # A visitor talking during a conversation lets the arm move (needs 150 ms, like the trigger).
            talk_blocks = talk_blocks + 1 if (voice and self.in_conversation) else 0
            if talk_blocks >= TRIGGER_BLOCKS:
                self._visitor_spoke()

            if self.session is not None:
                # Half-duplex: send silence while Bonesy talks so he doesn't hear (and interrupt) himself.
                # Also silent during a cap goodbye, so the visitor can't steer the last line.
                data = pcm if ((config.LIVE_BARGE_IN or not speaking) and not self.closing) else silence
                try:
                    await asyncio.wait_for(
                        self.session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={IN_RATE}")),
                        SEND_TIMEOUT_SEC,
                    )
                except Exception:
                    pass
            elif self.connecting:
                self.pending.append(pcm)
            else:
                self.preroll.append(pcm)
                loud_blocks = loud_blocks + 1 if voice else 0
                cooling_down = time.monotonic() < self.cooldown_until
                if (self.mode == "auto" and loud_blocks >= TRIGGER_BLOCKS and not self.trigger.is_set()
                        and not cooling_down and not self.in_conversation):
                    print(f"[Audio] Voice detected (RMS {rms:.4f}). Waking Bonesy...")
                    self.trigger_reason = "voice"
                    self.trigger.set()
                    loud_blocks = 0

    async def _conversation(self, cue: Optional[str] = None, single_turn: bool = False):
        """Opens one Live session and keeps it until the visitor goes quiet.

        cue: stage direction that makes Bonesy speak first (startup greeting, visitor walked up).
        single_turn: close as soon as that first reply is done (startup greeting).
        """
        greeting = cue == GREETING_CUE
        motion_visit = cue == VISITOR_CUE
        self.pending = [] if greeting else list(self.preroll)
        self.preroll.clear()
        self.connecting = True
        self.turn_done = False
        self.turn_count = 0
        self.closing = False
        self.visitor_said_goodbye = False
        self.last_cue_time = 0.0
        if cue is not None:
            self.last_voice_time = 0.0  # don't carry over a previous visitor's voice timestamp into a cue
        goodbye_deadline = 0.0
        self.status("Connecting...")
        t0 = time.monotonic()
        try:
            live_config = self._live_config()
            self.rec.save_text("system_prompt.md", live_config.system_instruction or "")
            async with self.client.aio.live.connect(model=config.GEMINI_LIVE_MODEL, config=live_config) as session:
                session_start = time.monotonic()
                self.note(f"[Live] Session open ({session_start - t0:.2f}s).")
                self.last_frame_time = 0.0
                if cue is None:
                    await self._send_frame(session)  # a visitor is talking: let Gemini see them
                while self.pending:
                    await asyncio.wait_for(
                        session.send_realtime_input(
                            audio=types.Blob(data=self.pending.pop(0), mime_type=f"audio/pcm;rate={IN_RATE}")),
                        SEND_TIMEOUT_SEC,
                    )
                cue_sent = True
                if motion_visit and self.last_voice_time >= t0 - 1.0:
                    # They spoke while we were connecting; their words start the turn, no cue needed.
                    self.note("[Live] Visitor is already talking. Letting them go first.")
                    await self._send_frame(session)
                    cue_sent = False
                elif motion_visit:
                    await self._send_cue_with_frame(session, cue)  # the frame travels with the cue
                elif cue:
                    self.last_cue_time = time.monotonic()
                    self.last_voice_time = 0.0
                    await asyncio.wait_for(session.send_realtime_input(text=cue), SEND_TIMEOUT_SEC)
                    self.rec.event("cue", text=cue)
                else:
                    cue_sent = False
                # The mic can mistake footsteps or a rustling bag for talking. If Gemini hasn't heard any
                # words by then and someone is in view, greet them anyway.
                fallback_at = (session_start + FALLBACK_GREET_SEC
                               if not cue_sent and self.motion and not greeting else None)
                fallback_greeted = False
                joke_told = False
                joke_skip_noted = False
                spoke_until = time.monotonic()  # when Bonesy last stopped talking (incl. the echo tail)
                self.heard_words = False
                self.session = session
                self.connecting = False
                self.awaiting_first_audio = True
                self.last_activity = time.monotonic()
                self.status("Live: listening")

                receiver = asyncio.create_task(self._receive(session))
                # The startup greeting doesn't look at anyone, so it gets no frames.
                video = asyncio.create_task(self._video_loop(session) if not greeting else asyncio.sleep(0))
                try:
                    while not self.stop_event.is_set() and not receiver.done():
                        await asyncio.sleep(0.25)
                        now = time.monotonic()
                        # Watchdog: runs before everything else, so nothing below can keep a stuck session open.
                        hard_limit = (config.LIVE_MAX_SESSION_SEC + 15.0 + GOODBYE_TIMEOUT_SEC + WATCHDOG_EXTRA_SEC
                                      if config.LIVE_MAX_SESSION_SEC > 0 else None)
                        if (now - self.last_activity > config.LIVE_IDLE_TIMEOUT_SEC + WATCHDOG_EXTRA_SEC
                                or (hard_limit and now - session_start > hard_limit)):
                            self.note("[Live] Watchdog: session looks stuck. Closing it.")
                            break
                        if self.player.is_speaking:
                            spoke_until = now
                            continue
                        if single_turn and self.turn_done:
                            break
                        if now - self.last_activity > config.LIVE_IDLE_TIMEOUT_SEC:
                            self.note("[Live] Visitor moved on. Closing session for the next trick-or-treater! 🎃")
                            break
                        if fallback_at and now >= fallback_at:
                            if self.heard_words or not self.awaiting_first_audio or self.turn_count:
                                fallback_at = None  # they did talk, or he's already answering
                            elif (now - self.last_voice_time < FALLBACK_AFTER_SOUND_SEC
                                  and now <= session_start + FALLBACK_GIVE_UP_SEC):
                                pass  # recent sound: they may be talking, give Gemini time to report words
                            elif self.motion.snapshot().quiet_for > FALLBACK_SEEN_SEC:
                                self.note("[Live] Only heard noise, and nobody is in view. Staying quiet.")
                                fallback_at = None
                            else:
                                self.note("[Live] No words heard, but someone is here. Greeting them.")
                                fallback_at = None
                                fallback_greeted = True
                                self.last_activity = now
                                await self._send_cue_with_frame(session, SILENT_VISITOR_CUE)
                        # Someone is still standing there in silence after his line: try one joke.
                        if (config.MOTION_JOKE_AFTER_SEC > 0 and self.motion and not greeting and not joke_told
                                and not self.visitor_said_goodbye and fallback_at is None and not self.closing
                                and self.turn_count and self.turn_done and self.awaiting_first_audio
                                and now - max(spoke_until, self.last_voice_time) >= config.MOTION_JOKE_AFTER_SEC):
                            moved_ago = self.motion.snapshot().quiet_for
                            # Only movement since he stopped counts; his own head moves while he talks.
                            if moved_ago <= JOKE_SEEN_SEC and now - moved_ago > spoke_until:
                                self.note("[Live] Visitor is still here but quiet. Telling them a joke.")
                                joke_told = True
                                self.turn_done = False
                                self.last_activity = now
                                await self._send_cue_with_frame(session, JOKE_CUE)
                                continue
                            if not joke_skip_noted:
                                joke_skip_noted = True
                                if config.MOTION_DEBUG:
                                    print(f"[Motion debug] Quiet, but nothing moved since Bonesy stopped "
                                          f"(still {moved_ago:.1f}s). No joke yet.")
                        if ((motion_visit or fallback_greeted) and self.turn_done and not self.heard_words
                                and now - max(self.last_activity, spoke_until) > config.MOTION_SILENT_TIMEOUT_SEC):
                            self.note("[Live] Visitor never said a word. Closing session for the next trick-or-treater! 🎃")
                            break
                        if self.closing:
                            if self.turn_done or now > goodbye_deadline:
                                self.cooldown_until = time.monotonic() + config.LIVE_CAP_COOLDOWN_SEC
                                break
                            continue
                        # Wait until any in-progress reply finishes (or +15s hard safety ceiling) before cutting off.
                        in_turn = not self.awaiting_first_audio
                        hard_cap = (config.LIVE_MAX_SESSION_SEC > 0
                                    and (now - session_start) >= config.LIVE_MAX_SESSION_SEC + 15.0)
                        cap = None
                        if not in_turn or hard_cap:
                            if config.LIVE_MAX_TURNS > 0 and self.turn_count >= config.LIVE_MAX_TURNS:
                                cap = f"Max turns ({config.LIVE_MAX_TURNS})"
                            elif config.LIVE_MAX_SESSION_SEC > 0 and (now - session_start) >= config.LIVE_MAX_SESSION_SEC:
                                cap = f"Max session duration ({config.LIVE_MAX_SESSION_SEC:.0f}s)"
                        if cap:
                            self.note(f"[Live] {cap} reached. Saying goodbye, then resetting memory! 🎃")
                            self.closing = True
                            self.turn_done = False
                            self.last_activity = now
                            self.last_cue_time = now
                            self.last_voice_time = 0.0
                            goodbye_deadline = now + GOODBYE_TIMEOUT_SEC
                            await asyncio.wait_for(session.send_realtime_input(text=GOODBYE_CUE), SEND_TIMEOUT_SEC)
                            self.rec.event("cue", text=GOODBYE_CUE)
                finally:
                    self.session = None
                    if receiver.done() and not receiver.cancelled() and receiver.exception():
                        self.note(f"[Live] Session error: {receiver.exception()}")
                    video.cancel()
                    receiver.cancel()
                    # Let the receiver finish, so a reply cut off mid-sentence still reaches the recording.
                    await asyncio.gather(receiver, video, return_exceptions=True)
        except Exception as e:
            self.note(f"[Live] Error: {e}")
            await asyncio.sleep(1.0)
        finally:
            self.session = None
            self.connecting = False
            self.closing = False
            self.pending = []

    async def _send_cue_with_frame(self, session, cue: str):
        """Sends a stage cue and the current camera frame as one message.

        A text cue sent on its own gets answered before Gemini looks at the video stream, so Bonesy
        sees nobody. With the frame in the same message he sees who walked up.
        """
        parts = []
        frame_name = None
        jpeg = self.cam.get_latest_jpeg_bytes(max_dimension=config.LIVE_VIDEO_MAX_DIM) if self.cam else None
        if jpeg:
            parts.append(types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))
            self.last_frame_time = time.monotonic()
            frame_name = self.rec.frame(jpeg, "with cue")
            if config.MOTION_DEBUG:  # keep the greeting frame so you can see what Gemini saw
                (config.BASE_DIR / "last_greeting_frame.jpg").write_bytes(jpeg)
            if self.preview:
                self.preview.update_sent(jpeg)
        parts.append(types.Part(text=cue))
        self.last_cue_time = time.monotonic()
        self.last_voice_time = 0.0
        await asyncio.wait_for(
            session.send_client_content(turns=types.Content(role="user", parts=parts), turn_complete=True),
            SEND_TIMEOUT_SEC,
        )
        self.rec.event("cue", text=cue, frame=frame_name if jpeg else None)

    async def _send_frame(self, session):
        """Sends the current camera frame, unless one went out less than LIVE_VIDEO_INTERVAL_SEC ago."""
        if not self.cam or time.monotonic() - self.last_frame_time < config.LIVE_VIDEO_INTERVAL_SEC:
            return
        jpeg = self.cam.get_latest_jpeg_bytes(max_dimension=config.LIVE_VIDEO_MAX_DIM)
        if jpeg:
            await asyncio.wait_for(
                session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg")),
                SEND_TIMEOUT_SEC,
            )
            self.last_frame_time = time.monotonic()
            self.rec.frame(jpeg, "video")
            if self.preview:
                self.preview.update_sent(jpeg)

    async def _video_loop(self, session):
        """Keeps Gemini's view fresh during a conversation, one frame per LIVE_VIDEO_INTERVAL_SEC."""
        if not self.cam:
            return
        interval = config.LIVE_VIDEO_INTERVAL_SEC
        while True:
            wait = self.last_frame_time + interval - time.monotonic()
            await asyncio.sleep(wait if wait > 0 else interval)
            try:
                await self._send_frame(session)
            except Exception:
                return

    async def _receive(self, session):
        heard, said = [], []
        try:
            await self._receive_turns(session, heard, said)
        finally:
            # The conversation ended mid-turn: keep what was said so far in the recording.
            if heard:
                self.rec.event("visitor", text="".join(heard).strip(), cut_off=True)
            if said:
                self.rec.event("bonesy", text="".join(said).strip(), cut_off=True)

    async def _receive_turns(self, session, heard: list, said: list):
        while True:
            got_any = False
            async for msg in session.receive():  # yields one model turn, then returns
                got_any = True
                sc = msg.server_content
                if sc and sc.model_turn:
                    for part in sc.model_turn.parts or []:
                        if getattr(part, "thought", False):
                            continue
                        if part.inline_data and part.inline_data.data:
                            self._play(part.inline_data.data)
                if sc:
                    if sc.input_transcription and sc.input_transcription.text:
                        heard.append(sc.input_transcription.text)
                        if sc.input_transcription.text.strip():
                            self.heard_words = True  # Gemini recognized actual words, not just noise
                            self.last_activity = time.monotonic()
                    if sc.output_transcription and sc.output_transcription.text:
                        said.append(sc.output_transcription.text)
                    if sc.interrupted:
                        self.rec.event("note", text="Bonesy was interrupted")
                        self.player.clear()
                        if self.fx:
                            self.fx = VoiceFX(OUT_RATE)  # drop any echo of the cut-off reply
                    if sc.turn_complete:
                        if self.fx:
                            self.player.enqueue(self.fx.flush(), voice=False)  # let the echo ring out
                        if heard:
                            visitor_text = "".join(heard).strip()
                            print(f"[Visitor] {visitor_text}")
                            self.rec.event("visitor", text=visitor_text)
                            if visitor_text:
                                self.visitor_said_goodbye = bool(GOODBYE_RE.search(visitor_text))
                        if said:
                            reply_text = "".join(said).strip()
                            print(f"[Bonesy]  {reply_text}")
                            self.rec.event("bonesy", text=reply_text)
                            cleaned = " ".join(re.sub(r"<[^>]+>", " ", reply_text).split())
                            if len(cleaned) > 15 and (not self.recent_lines or self.recent_lines[-1] != cleaned):
                                self.recent_lines.append(cleaned)
                        heard.clear()
                        said.clear()
                        self.awaiting_first_audio = True
                        self.turn_done = True
                        self.turn_count += 1
                        self.status("Live: listening")
                if msg.tool_call:
                    await self._handle_tools(session, msg.tool_call)
                if msg.go_away:
                    self.note(f"[Live] Server is closing the session (time left: {msg.go_away.time_left}).")
            if not got_any:
                return

    def _play(self, pcm: bytes):
        now = time.monotonic()
        if self.awaiting_first_audio:
            if self.last_voice_time:
                self.note(f"[Live] Reply started {now - self.last_voice_time:.2f}s after the visitor stopped talking.")
            elif self.last_cue_time:
                self.note(f"[Live] Reply started {now - self.last_cue_time:.2f}s after cue.")
            self.last_voice_time = 0.0
            self.last_cue_time = 0.0
            self.turn_done = False
        self.awaiting_first_audio = False
        self.last_activity = now
        self.status("Speaking...")
        self.player.enqueue(self.fx.process(pcm) if self.fx else pcm)

    async def _handle_tools(self, session, tool_call):
        from skelly_ble import CHANNEL_HEAD, COLORS

        # If Bonesy is already speaking this turn, SILENT keeps the tool acknowledgement from scheduling
        # a second, duplicate speech turn once he finishes talking.
        scheduling = (types.FunctionResponseScheduling.WHEN_IDLE if self.awaiting_first_audio
                      else types.FunctionResponseScheduling.SILENT)
        responses = []
        for fc in tool_call.function_calls or []:
            args = fc.args or {}
            result = {"result": "ok"}
            try:
                if not self.skelly:
                    raise RuntimeError("body not connected")
                if fc.name == "change_eye_color":
                    command = self.skelly.set_light_color(CHANNEL_HEAD, COLORS[args["color"]])
                elif fc.name == "move":
                    part = args["part"]
                    if part != "all" and part not in config.SKELLY_MOVEMENT:
                        raise ValueError(f"{part} movement is switched off")
                    # The movement setting stays on the skeleton, so never narrow or widen it here: sending
                    # just ["arm"] would freeze the head, and the arm must stay still until a visitor talks.
                    # Re-sending the current setting makes the firmware do one movement with those parts.
                    current = config.SKELLY_MOVEMENT if self.talk_movement_on else config.SKELLY_IDLE_MOVEMENT
                    if not current:
                        raise ValueError("movement is switched off right now")
                    command = self.skelly.move(movement_mask(current))
                else:
                    raise ValueError(f"unknown tool {fc.name}")
                # Don't wait for Bluetooth here: this loop also receives Bonesy's voice.
                task = asyncio.create_task(self._run_tool(fc.name, dict(args), command))
                self._tool_tasks.add(task)
                task.add_done_callback(self._tool_tasks.discard)
            except Exception as e:
                print(f"[Skelly] {fc.name} failed: {e}")
                result = {"error": str(e)}
            self.rec.event("tool", name=fc.name, args=dict(args), result=result)
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result, scheduling=scheduling))
        if responses:
            await asyncio.wait_for(session.send_tool_response(function_responses=responses), SEND_TIMEOUT_SEC)

    async def _run_tool(self, name: str, args: dict, command):
        try:
            await asyncio.wait_for(command, 3)
            print(f"[Skelly] {name}({args})")
        except Exception as e:
            print(f"[Skelly] {name} failed: {e}")
