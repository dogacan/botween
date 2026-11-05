import numpy as np
import sounddevice as sd

import config
from devices import DeviceSpec, find_microphone, parse_spec

MIC_WARMUP_SEC = 0.5  # USB mics pop for about this long after the stream opens


class AudioListener:
    """
    Microphone device selector and ambient noise floor calibrator for Voice Activity Detection (VAD).
    """

    def __init__(
        self,
        energy_threshold: float = config.VAD_ENERGY_THRESHOLD,
        sample_rate: int = config.SAMPLE_RATE,
        device: DeviceSpec = None,
    ):
        self.energy_threshold = energy_threshold
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
