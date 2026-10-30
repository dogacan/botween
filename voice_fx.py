"""
Streaming voice effects for the Live engine: pitch shift down + crypt reverb + echo.

Everything works on small chunks of 16-bit mono PCM as they arrive from the model, with
about 50 ms of added delay. Lowering the pitch this way also lowers the formants, which makes
the voice sound like a bigger creature (exactly what a 6.5 ft skeleton wants).
"""

import numpy as np

import config


class PitchShifter:
    """
    Classic two-tap delay-line pitch shifter (as in hardware harmonizers): two read heads
    sweep through a short delay line at the new speed and crossfade with sin^2/cos^2 windows,
    so each head jumps back only while it is silent.
    """

    def __init__(self, semitones: float, rate: int, window_sec: float = 0.05):
        self.ratio = 2.0 ** (semitones / 12.0)
        self.window = int(rate * window_sec)
        self.min_delay = 2
        self.history = np.zeros(self.window + self.min_delay + 2, dtype=np.float32)
        self.pos = 0  # global sample index of the next input sample

    def process(self, x: np.ndarray) -> np.ndarray:
        if abs(self.ratio - 1.0) < 1e-6 or len(x) == 0:
            return x
        ext = np.concatenate([self.history, x])
        base = self.pos - len(self.history)              # global index of ext[0]
        n = self.pos + np.arange(len(x), dtype=np.float64)  # global output indices
        out = np.zeros(len(x), dtype=np.float64)
        drift = (1.0 - self.ratio) / self.window          # phase change per sample
        for offset in (0.0, 0.5):
            phase = np.mod(n * drift + offset, 1.0)
            delay = self.min_delay + phase * self.window
            read = n - delay - base
            i0 = np.floor(read).astype(np.int64)
            frac = read - i0
            sample = ext[i0] * (1.0 - frac) + ext[np.minimum(i0 + 1, len(ext) - 1)] * frac
            out += sample * np.sin(np.pi * phase) ** 2
        self.history = ext[-len(self.history):]
        self.pos += len(x)
        return out.astype(np.float32)


class FeedbackEcho:
    """Repeating echo: each repeat comes back `delay_sec` later, `feedback` times quieter."""

    def __init__(self, delay_sec: float, feedback: float, mix: float, rate: int):
        self.delay = max(1, int(rate * delay_sec))
        self.feedback = feedback
        self.mix = mix
        self.line = np.zeros(self.delay, dtype=np.float32)  # last `delay` samples of the echo bus

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        for start in range(0, len(x), self.delay):          # blocks <= delay keep it vectorized
            blk = x[start:start + self.delay]
            delayed = self.line[:len(blk)]
            bus = blk + self.feedback * delayed
            out[start:start + len(blk)] = blk + self.mix * delayed
            self.line = np.concatenate([self.line[len(blk):], bus])
        return out


class CryptReverb:
    """Short multi-tap slapback (ffmpeg 'aecho=0.8:0.75:60|120:0.3|0.15') for a hollow chamber feel."""

    def __init__(self, rate: int):
        self.d1 = int(rate * 0.060)
        self.d2 = int(rate * 0.120)
        self.history = np.zeros(self.d2, dtype=np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        ext = np.concatenate([self.history, x])
        h, n = len(self.history), len(x)
        y = 0.8 * x + 0.3 * ext[h - self.d1:h - self.d1 + n] + 0.15 * ext[h - self.d2:h - self.d2 + n]
        self.history = ext[-self.d2:]
        return y * 0.75


class VoiceFX:
    """Pitch shift -> crypt reverb -> echo, on int16 PCM bytes."""

    def __init__(self, rate: int = 24000):
        self.rate = rate
        self.stages = []
        if config.LIVE_PITCH_SEMITONES:
            self.stages.append(PitchShifter(config.LIVE_PITCH_SEMITONES, rate))
        self.stages.append(CryptReverb(rate))
        if config.LIVE_ECHO_MIX > 0:
            self.stages.append(FeedbackEcho(config.LIVE_ECHO_DELAY_MS / 1000.0, config.LIVE_ECHO_FEEDBACK,
                                            config.LIVE_ECHO_MIX, rate))

    def _run(self, x: np.ndarray) -> np.ndarray:
        for stage in self.stages:
            x = stage.process(x)
        return x

    def process(self, pcm: bytes) -> bytes:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        return np.clip(self._run(x), -32768, 32767).astype("<i2").tobytes()

    def flush(self, max_sec: float = 1.5) -> bytes:
        """Lets the echo ring out after a reply ends (otherwise the last repeats are cut off)."""
        y = self._run(np.zeros(int(self.rate * max_sec), dtype=np.float32))
        loud = np.nonzero(np.abs(y) > 30)[0]
        y = y[:loud[-1] + 1] if len(loud) else y[:0]
        return np.clip(y, -32768, 32767).astype("<i2").tobytes()
