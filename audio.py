import io
import time
import wave
from typing import Optional
import numpy as np
import sounddevice as sd

import config
import tts


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
        device=config.AUDIO_INPUT_DEVICE,
    ):
        self.energy_threshold = energy_threshold
        self.silence_duration = silence_duration
        self.sample_rate = sample_rate

        # Sanitize and resolve device to avoid matching empty strings against multiple devices
        if isinstance(device, str):
            device = device.strip()
            if not device:
                device = None
            elif device.isdigit():
                device = int(device)

        if device is None:
            default_in = sd.default.device[0]
            if default_in is not None and default_in >= 0:
                device = default_in
            else:
                # Fallback to first available device with input channels
                for idx, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0:
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
            sd.sleep(int(duration_sec * 1000))

        if readings:
            ambient_mean = float(np.mean(readings))
            # Set threshold dynamically at 2.5x ambient noise or minimum config default
            dynamic_threshold = max(config.VAD_ENERGY_THRESHOLD, ambient_mean * 2.5)
            self.energy_threshold = dynamic_threshold
            print(f"[Audio] Ambient noise: {ambient_mean:.4f} -> Dynamic threshold set to: {self.energy_threshold:.4f}")
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
