import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
import motion
import recorder
import skelly_ble
import voice_fx


class ConfigAndAssetsTest(unittest.TestCase):
    def test_jokes_json_is_valid_and_non_empty(self):
        raw = json.loads(config.JOKES_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)
        jokes = config.load_jokes()
        self.assertGreater(len(jokes), 10)
        for joke in jokes:
            self.assertIsInstance(joke, str)
            self.assertTrue(joke.strip())

    def test_pick_jokes_rotates_and_formats_bullets(self):
        first = config.pick_jokes()
        second = config.pick_jokes()
        lines = [line for line in first.splitlines() if line.strip()]
        self.assertEqual(len(lines), config.JOKES_PER_SESSION)
        self.assertTrue(all(line.startswith("- ") for line in lines))
        self.assertNotEqual(first, second)

    def test_system_instruction_replaces_jokes_placeholder(self):
        prompt = config.load_system_instruction()
        self.assertTrue(len(prompt) > 100)
        self.assertNotIn(config.JOKES_PLACEHOLDER, prompt)

    def test_env_helpers(self):
        with patch.dict(os.environ, {"TEST_ROI": "0.1,0.2,0.5,0.6", "TEST_PARTS": "head, torso"}):
            self.assertEqual(config._env_roi("TEST_ROI"), (0.1, 0.2, 0.5, 0.6))
            self.assertEqual(config._env_parts("TEST_PARTS", "none"), ["head", "torso"])

        with patch.dict(os.environ, {"TEST_ROI": "invalid", "TEST_PARTS": "none"}):
            self.assertIsNone(config._env_roi("TEST_ROI"))
            self.assertEqual(config._env_parts("TEST_PARTS", "all"), [])


class VoiceFXTest(unittest.TestCase):
    def test_apply_gain_soft_limits_without_clipping(self):
        samples = np.array([0, 4000, -4000, 30000, -30000], dtype="<i2")
        pcm = samples.tobytes()
        boosted = voice_fx.apply_gain(pcm, 2.5)
        out = np.frombuffer(boosted, dtype="<i2")
        self.assertEqual(len(out), len(samples))
        self.assertEqual(out[0], 0)
        self.assertGreater(out[1], samples[1])
        self.assertLessEqual(int(np.max(out)), 32767)
        self.assertGreaterEqual(int(np.min(out)), -32768)

    def test_voice_fx_pipeline_and_echo_flush(self):
        rate = 24000
        t = np.linspace(0, 0.2, int(rate * 0.2), endpoint=False, dtype=np.float32)
        pcm = (np.sin(2 * np.pi * 220.0 * t) * 16000).astype("<i2").tobytes()

        fx = voice_fx.VoiceFX(rate=rate)
        processed = fx.process(pcm)
        self.assertEqual(len(processed), len(pcm))

        tail = fx.flush(max_sec=0.8)
        self.assertGreater(len(tail), 0)
        self.assertEqual(len(tail) % 2, 0)


class MotionDetectorTest(unittest.TestCase):
    def test_warmup_then_person_sized_motion_and_headlight_ignore(self):
        detector = motion.MotionDetector(camera=None, roi=None)
        bg = np.zeros((240, 320, 3), dtype=np.uint8)

        now = 100.0
        for _ in range(motion.WARMUP_FRAMES + 5):
            now += 0.1
            state = detector.process(bg, now)
        self.assertFalse(state.visitor)
        self.assertFalse(state.ignored)

        # Person-sized moving box (~10% of frame) sustained across MOTION_SUSTAIN_SEC
        person_frame = bg.copy()
        person_frame[60:140, 100:200] = 255
        now += 0.1
        state = detector.process(person_frame, now)
        self.assertFalse(state.visitor)  # not sustained long enough on first frame

        now += config.MOTION_SUSTAIN_SEC + 0.1
        state = detector.process(person_frame, now)
        self.assertTrue(state.visitor)
        self.assertFalse(state.ignored)
        self.assertGreater(len(state.boxes), 0)

        # Full-frame flash (e.g. porch light / headlights) should be ignored
        flash_frame = np.full((240, 320, 3), 255, dtype=np.uint8)
        now += 0.1
        state = detector.process(flash_frame, now)
        self.assertTrue(state.ignored)
        self.assertFalse(state.visitor)


class SkellyBLEProtocolTest(unittest.TestCase):
    def test_crc8_maxim_and_frame_builder(self):
        frame = skelly_ble.build_frame(skelly_ble.CMD_ENABLE_LIVE_MODE, b"\x01")
        self.assertEqual(frame[0], 0xAA)
        self.assertEqual(frame[1], skelly_ble.CMD_ENABLE_LIVE_MODE)
        self.assertEqual(len(frame), 11)  # 0xAA + cmd + 8-byte padded payload + CRC
        self.assertEqual(frame[-1], skelly_ble.crc8_maxim(frame[:-1]))

    def test_parse_color(self):
        self.assertEqual(skelly_ble.parse_color("red"), (255, 0, 0))
        self.assertEqual(skelly_ble.parse_color("#00ff80"), (0, 255, 128))
        with self.assertRaises(ValueError):
            skelly_ble.parse_color("octarine")

    def test_is_live_speaker(self):
        self.assertTrue(skelly_ble._is_live_speaker("Ultra Skelly v2(Live)", "Ultra Skelly v2(Live)"))
        self.assertTrue(skelly_ble._is_live_speaker("Animated Skelly (Live)", "Ultra Skelly v2(Live)"))
        self.assertFalse(skelly_ble._is_live_speaker("MacBook Pro Speakers", "Ultra Skelly v2(Live)"))


class SessionRecorderTest(unittest.TestCase):
    def test_session_recorder_writes_events_and_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = recorder.SessionRecorder(Path(tmp), "motion")
            rec.save_text("system_prompt.md", "You are Bonesy.")
            frame_name = rec.frame(b"\xff\xd8\xff\xe0dummy", "open")
            rec.event("visitor", text="Trick or treat!")
            rec.close()

            self.assertEqual(frame_name, "frame_001.jpg")
            self.assertTrue((rec.dir / "frame_001.jpg").exists())
            self.assertEqual((rec.dir / "system_prompt.md").read_text(encoding="utf-8"), "You are Bonesy.")
            events = [
                json.loads(line)
                for line in (rec.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            kinds = [e["event"] for e in events]
            self.assertEqual(kinds, ["start", "frame", "visitor", "end"])


if __name__ == "__main__":
    unittest.main()
