"""
Low-latency conversation engine using the Gemini Live API.

Mic (16 kHz PCM) + camera (JPEG, <= 1 fps) stream into a Live session, and the model's
24 kHz voice streams straight to the Skelly's "(Live)" Bluetooth speaker (which moves the jaw).

One Live session per visitor: local voice detection opens a session (with ~1 s of pre-roll
so the first words aren't lost), and it closes after LIVE_IDLE_TIMEOUT_SEC of quiet.
That keeps memory per trick-or-treater and stays well inside the Live API session limits.
"""

import asyncio
import collections
import threading
import time
from typing import List, Optional

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types

import config
from audio import AudioListener
from skelly_ble import find_output_device

IN_RATE = 16000
OUT_RATE = 24000
IN_BLOCK = IN_RATE // 20      # 50 ms
OUT_BLOCK = OUT_RATE // 20    # 50 ms
PREROLL_BLOCKS = 20           # 1 s of audio kept from before the trigger
TRIGGER_BLOCKS = 3            # 150 ms above threshold opens a session

LIVE_ADDENDUM = """

---

## 🔊 Live Voice Mode (overrides the stage-direction rules above)
You are speaking in real time with your own voice; there is no separate voice actor.
- Never write or say angle-bracket tags like <gasp>, <whisper> or <cackle>. Perform them with your voice instead: actually gasp, whisper, cackle and pause.
- You see a live camera feed from your eye sockets. Only describe what you can actually see.
- Keep every reply to 1-2 short sentences, then let the visitor talk.
"""

TOOLS_ADDENDUM = """
## 💀 Your Body
You can control your animatronic body with tools: `change_eye_color` makes your eyes glow a color (red when spooking someone, green for a witch, etc.) and `move` wiggles your head, arm or torso. Use them now and then for dramatic effect while you talk. Never mention the tools out loud.
"""

GREETING_CUE = "(Stage cue: you just woke up on the porch. Say one short, spooky awakening line.)"


class CryptEcho:
    """Streaming version of the ffmpeg 'aecho=0.8:0.75:60|120:0.3|0.15' crypt reverb."""

    def __init__(self, rate: int = OUT_RATE):
        self.d1 = int(rate * 0.060)
        self.d2 = int(rate * 0.120)
        self.history = np.zeros(self.d2, dtype=np.float32)

    def process(self, pcm: bytes) -> bytes:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        ext = np.concatenate([self.history, x])
        h, n = len(self.history), len(x)
        y = 0.8 * x + 0.3 * ext[h - self.d1:h - self.d1 + n] + 0.15 * ext[h - self.d2:h - self.d2 + n]
        self.history = ext[-self.d2:]
        return np.clip(y * 0.75, -32768, 32767).astype("<i2").tobytes()


class VoicePlayer:
    """Plays streamed 24 kHz PCM on the chosen output device with a jitter buffer."""

    def __init__(self, device: Optional[int]):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._last_voice = 0.0
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
            chunk = bytes(self._buf[:nbytes])
            del self._buf[:nbytes]
        if chunk:
            self._last_voice = time.monotonic()
        if len(chunk) < nbytes:
            chunk += self._filler(nbytes - len(chunk))
        outdata[:] = chunk

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    def enqueue(self, pcm: bytes):
        with self._lock:
            self._buf += pcm

    def clear(self):
        with self._lock:
            self._buf.clear()

    @property
    def is_speaking(self) -> bool:
        """True while voice is buffered or played recently (covers the Bluetooth output delay)."""
        with self._lock:
            if self._buf:
                return True
        return (time.monotonic() - self._last_voice) < config.LIVE_ECHO_TAIL_SEC


class LiveSkeleton:
    def __init__(self, mode: str, cam, preview, stop_event: threading.Event):
        self.mode = mode
        self.cam = cam
        self.preview = preview
        self.stop_event = stop_event
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.skelly = None
        self.player: Optional[VoicePlayer] = None
        self.fx = CryptEcho() if config.ENABLE_SPOOKY_FX else None

        self.session = None
        self.connecting = False
        self.pending: List[bytes] = []
        self.preroll = collections.deque(maxlen=PREROLL_BLOCKS)
        self.trigger = None  # asyncio.Event, created inside the loop
        self.last_activity = 0.0
        self.last_voice_time = 0.0
        self.awaiting_first_audio = False
        self.turn_done = False
        self.threshold = config.VAD_ENERGY_THRESHOLD

    # ---- helpers ---------------------------------------------------------

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
        instruction = config.load_system_instruction() + LIVE_ADDENDUM
        tools = None
        if self.skelly:
            from skelly_ble import COLORS, MOVEMENTS

            instruction += TOOLS_ADDENDUM
            tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name="change_eye_color",
                    description="Make the skeleton's glowing eyes change color.",
                    parameters=types.Schema(type="OBJECT", required=["color"], properties={
                        "color": types.Schema(type="STRING", enum=list(COLORS)),
                    }),
                ),
                types.FunctionDeclaration(
                    name="move",
                    description="Make the skeleton do a short movement with part of its body.",
                    parameters=types.Schema(type="OBJECT", required=["part"], properties={
                        "part": types.Schema(type="STRING", enum=list(MOVEMENTS)),
                    }),
                ),
            ])]

        handling = (types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS if config.LIVE_BARGE_IN
                    else types.ActivityHandling.NO_INTERRUPTION)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.GEMINI_VOICE_NAME))),
            system_instruction=instruction,
            tools=tools,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    silence_duration_ms=config.LIVE_END_SILENCE_MS),
                activity_handling=handling,
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
        pump = asyncio.create_task(self._mic_pump(mic_queue))
        if self.mode == "manual":
            self._start_enter_thread(loop)

        try:
            print("\n[Skeleton] Bonesy is awakening...")
            await self._conversation(greeting=True)
            print(f"[Skeleton] Ready for trick-or-treaters! (Live model: {config.GEMINI_LIVE_MODEL})\n")

            while not self.stop_event.is_set():
                self.status("Waiting for ENTER (manual mode)" if self.mode == "manual"
                            else "Listening for trick-or-treaters...")
                try:
                    await asyncio.wait_for(self.trigger.wait(), 0.5)
                except asyncio.TimeoutError:
                    continue
                self.trigger.clear()
                await self._conversation()
                if self.mode == "manual":
                    self._start_enter_thread(loop)
        finally:
            pump.cancel()
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
            loop.call_soon_threadsafe(self.trigger.set)

        threading.Thread(target=_wait_enter, daemon=True).start()

    async def _mic_pump(self, queue: asyncio.Queue):
        """Single consumer of mic audio: pre-roll while idle, stream while a session is open."""
        loud_blocks = 0
        silence = bytes(IN_BLOCK * 2)
        while True:
            block = await queue.get()
            rms = float(np.sqrt(np.mean(block ** 2)))
            pcm = (np.clip(block[:, 0], -1.0, 1.0) * 32767).astype("<i2").tobytes()
            speaking = self.player.is_speaking
            voice = rms > self.threshold and not speaking

            if voice:
                self.last_voice_time = time.monotonic()

            if self.session is not None:
                if voice:
                    self.last_activity = time.monotonic()
                # Half-duplex: send silence while Bonesy talks so he doesn't hear (and interrupt) himself.
                data = pcm if (config.LIVE_BARGE_IN or not speaking) else silence
                try:
                    await self.session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={IN_RATE}"))
                except Exception:
                    pass
            elif self.connecting:
                self.pending.append(pcm)
            else:
                self.preroll.append(pcm)
                loud_blocks = loud_blocks + 1 if voice else 0
                if self.mode == "auto" and loud_blocks >= TRIGGER_BLOCKS and not self.trigger.is_set():
                    print(f"[Audio] Voice detected (RMS {rms:.4f}). Waking Bonesy...")
                    self.trigger.set()
                    loud_blocks = 0

    async def _conversation(self, greeting: bool = False):
        """Opens one Live session and keeps it until the visitor goes quiet."""
        self.pending = [] if greeting else list(self.preroll)
        self.preroll.clear()
        self.connecting = True
        self.turn_done = False
        self.status("Connecting...")
        t0 = time.monotonic()
        try:
            async with self.client.aio.live.connect(model=config.GEMINI_LIVE_MODEL, config=self._live_config()) as session:
                print(f"[Live] Session open ({time.monotonic() - t0:.2f}s).")
                await self._send_frame(session)
                if greeting:
                    await session.send_realtime_input(text=GREETING_CUE)
                while self.pending:
                    await session.send_realtime_input(
                        audio=types.Blob(data=self.pending.pop(0), mime_type=f"audio/pcm;rate={IN_RATE}"))
                self.session = session
                self.connecting = False
                self.awaiting_first_audio = True
                self.last_activity = time.monotonic()
                self.status("Live: listening")

                receiver = asyncio.create_task(self._receive(session))
                video = asyncio.create_task(self._video_loop(session))
                try:
                    while not self.stop_event.is_set() and not receiver.done():
                        await asyncio.sleep(0.25)
                        if self.player.is_speaking:
                            continue
                        if greeting and self.turn_done:
                            break
                        if time.monotonic() - self.last_activity > config.LIVE_IDLE_TIMEOUT_SEC:
                            print("[Live] Visitor moved on. Closing session for the next trick-or-treater! 🎃")
                            break
                finally:
                    self.session = None
                    if receiver.done() and not receiver.cancelled() and receiver.exception():
                        print(f"[Live] Session error: {receiver.exception()}")
                    video.cancel()
                    receiver.cancel()
        except Exception as e:
            print(f"[Live] Error: {e}")
            await asyncio.sleep(1.0)
        finally:
            self.session = None
            self.connecting = False
            self.pending = []

    async def _send_frame(self, session):
        if not self.cam:
            return
        jpeg = self.cam.get_latest_jpeg_bytes(max_dimension=config.LIVE_VIDEO_MAX_DIM)
        if jpeg:
            await session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
            if self.preview:
                self.preview.update_sent(jpeg)

    async def _video_loop(self, session):
        interval = 1.0 / max(0.1, min(1.0, config.LIVE_VIDEO_FPS))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._send_frame(session)
            except Exception:
                return

    async def _receive(self, session):
        heard, said = [], []
        while True:
            got_any = False
            async for msg in session.receive():  # yields one model turn, then returns
                got_any = True
                sc = msg.server_content
                if sc and sc.model_turn:
                    for part in sc.model_turn.parts or []:
                        if part.inline_data and part.inline_data.data:
                            self._play(part.inline_data.data)
                if sc:
                    if sc.input_transcription and sc.input_transcription.text:
                        heard.append(sc.input_transcription.text)
                    if sc.output_transcription and sc.output_transcription.text:
                        said.append(sc.output_transcription.text)
                    if sc.interrupted:
                        self.player.clear()
                    if sc.turn_complete:
                        if heard:
                            print(f"[Visitor] {''.join(heard).strip()}")
                        if said:
                            print(f"[Bonesy]  {''.join(said).strip()}")
                        heard, said = [], []
                        self.awaiting_first_audio = True
                        self.turn_done = True
                        self.status("Live: listening")
                if msg.tool_call:
                    await self._handle_tools(session, msg.tool_call)
                if msg.go_away:
                    print(f"[Live] Server is closing the session (time left: {msg.go_away.time_left}).")
            if not got_any:
                return

    def _play(self, pcm: bytes):
        if self.awaiting_first_audio and self.last_voice_time:
            print(f"[Live] Reply started {time.monotonic() - self.last_voice_time:.2f}s after the visitor stopped talking.")
        self.awaiting_first_audio = False
        self.last_activity = time.monotonic()
        self.status("Speaking...")
        self.player.enqueue(self.fx.process(pcm) if self.fx else pcm)

    async def _handle_tools(self, session, tool_call):
        from skelly_ble import CHANNEL_HEAD, COLORS, MOVEMENTS

        responses = []
        for fc in tool_call.function_calls or []:
            args = fc.args or {}
            result = {"result": "ok"}
            try:
                if not self.skelly:
                    raise RuntimeError("body not connected")
                if fc.name == "change_eye_color":
                    await asyncio.wait_for(self.skelly.set_light_color(CHANNEL_HEAD, COLORS[args["color"]]), 3)
                elif fc.name == "move":
                    await asyncio.wait_for(self.skelly.move(MOVEMENTS[args["part"]]), 3)
                else:
                    raise ValueError(f"unknown tool {fc.name}")
                print(f"[Skelly] {fc.name}({args})")
            except Exception as e:
                print(f"[Skelly] {fc.name} failed: {e}")
                result = {"error": str(e)}
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
        if responses:
            await session.send_tool_response(function_responses=responses)
