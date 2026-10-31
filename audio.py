import io
import time
import wave
from typing import Optional
import numpy as np
import sounddevice as sd

import config
import tts
from devices import DeviceSpec, find_microphone, parse_spec

MIC_WARMUP_SEC = 0.5  # USB mics pop for about this long after the stream opens


def create_wav_bytes(audio_data: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> bytes:
    """Encodes float32 or int16 numpy array into standard WAV file bytes in memory."""
    if audio_data.dtype == np.float32 or audio_data.dtype == np.float64:
        # Normalize and convert to 16-bit PCM
        audio_int16 = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16)
    else:
        audio_int16 = audio_data.astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(config.CHANNELS)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())

    return buffer.getvalue()


class AudioListener:
    """
    Microphone listener with energy-based Voice Activity Detection (VAD),
    silence detection, and half-duplex mute locking while the skeleton speaks.
    """

    def __init__(
        self,
        energy_threshold: float = config.VAD_ENERGY_THRESHOLD,
        silence_duration: float = config.SILENCE_DURATION_SEC,
        sample_rate: int = config.SAMPLE_RATE,
        device: DeviceSpec = None,
    ):
        self.energy_threshold = energy_threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate

        # Name or index; None uses config.MIC_DEVICE. A blank setting means the system default.
        wanted = parse_spec(config.MIC_DEVICE if device is None else device)
        device = find_microphone(wanted)
        if wanted is not None and device is None:
            print(f"[Audio] Warning: no microphone matching '{wanted}'. Using the system default.")

        if device is None:
            # Never listen through the skeleton's Bluetooth "(Live)" link: macOS treats it as a
            # headset and may make it the default input, which drops audio to phone-call quality.
            skelly_hint = (config.SKELLY_AUDIO_OUTPUT or "Skelly").lower()
            devices = sd.query_devices()

            def _usable(idx):
                return (idx is not None and 0 <= idx < len(devices)
                        and devices[idx]["max_input_channels"] > 0
                        and skelly_hint not in devices[idx]["name"].lower())

            default_in = sd.default.device[0]
            if _usable(default_in):
                device = default_in
            else:
                # Fallback to first available input that isn't the skeleton
                for idx in range(len(devices)):
                    if _usable(idx):
                        device = idx
                        break

        self.device = device
        try:
            dev_info = sd.query_devices(self.device)
            print(f"[Audio] Selected input device: [{self.device}] {dev_info['name']}")
        except Exception:
            pass

        self.block_size = int(sample_rate * 0.05)  # 50ms chunks

    def calibrate_noise_floor(self, duration_sec: float = 1.0) -> float:
        """Measures ambient room noise to establish a baseline threshold."""
        print("[Audio] Calibrating ambient noise floor (please stay quiet for 1s)...")
        readings = []

        def _cal_callback(indata, frames, time_info, status):
            rms = np.sqrt(np.mean(indata**2))
            readings.append(rms)

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=config.CHANNELS,
            blocksize=self.block_size,
            device=self.device,
            callback=_cal_callback,
        ):
            sd.sleep(int((MIC_WARMUP_SEC + duration_sec) * 1000))

        # USB mics pop loudly for a moment when the stream opens; leave that out of the measurement.
        readings = readings[int(MIC_WARMUP_SEC * self.sample_rate / self.block_size):]
        if readings:
            # Median, so a stray click or bump doesn't raise the floor
            ambient = float(np.median(readings))
            # Set threshold dynamically at 2.5x ambient noise or minimum config default
            dynamic_threshold = max(config.VAD_ENERGY_THRESHOLD, ambient * 2.5)
            self.energy_threshold = dynamic_threshold
            print(f"[Audio] Ambient noise: {ambient:.4f} -> Dynamic threshold set to: {self.energy_threshold:.4f}")
        return self.energy_threshold

    def listen_for_speech(self) -> Optional[bytes]:
        """
        Blocks and listens to the microphone until a complete utterance is detected:
        1. Waits for sound energy > threshold (half-duplex muted if skeleton is speaking).
        2. Gathers audio chunks while speech continues.
        3. Finalizes when silence is maintained for `silence_duration` seconds.
        4. Returns WAV bytes, or None if speech was too brief.
        """
        recorded_chunks = []
        is_recording = False
        silence_start = None
        speech_start_time = None

        print("[Audio] Listening for trick-or-treaters...")

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=config.CHANNELS,
            blocksize=self.block_size,
            device=self.device,
        ) as stream:
            while True:
                # Half-duplex check: If skeleton is currently speaking, do not listen
                if tts.is_speaking():
                    if is_recording:
                        # Reset if speech started during playback
                        recorded_chunks.clear()
                        is_recording = False
                    time.sleep(0.05)
                    continue

                data, overflowed = stream.read(self.block_size)
                rms = float(np.sqrt(np.mean(data**2)))

                if not is_recording:
                    # Listening state
                    if rms > self.energy_threshold:
                        is_recording = True
                        speech_start_time = time.time()
                        silence_start = None
                        recorded_chunks.append(data.copy())
                        print(f"[Audio] Speech detected (RMS: {rms:.4f})! Recording...")
                else:
                    # Recording state
                    recorded_chunks.append(data.copy())
                    now = time.time()

                    # Check for silence
                    if rms < self.energy_threshold:
                        if silence_start is None:
                            silence_start = now
                        elif (now - silence_start) >= self.silence_duration:
                            # Silence timeout reached!
                            print("[Audio] Silence detected. Utterance completed.")
                            break
                    else:
                        # Voice resumed
                        silence_start = None

                    # Check max duration safety limit
                    if (now - speech_start_time) >= config.MAX_RECORDING_SEC:
                        print("[Audio] Max recording limit reached.")
                        break

        # Process recorded audio
        if not recorded_chunks:
            return None

        total_duration = time.time() - speech_start_time if speech_start_time else 0
        if total_duration < config.MIN_SPEECH_DURATION_SEC:
            print(f"[Audio] Discarding short audio ({total_duration:.2f}s, cough/click).")
            return None

        audio_full = np.concatenate(recorded_chunks, axis=0)
        return create_wav_bytes(audio_full, sample_rate=self.sample_rate)
