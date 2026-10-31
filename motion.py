"""
Camera motion detection that wakes Bonesy when someone walks up, before they say a word.

Runs on its own thread at about 10 fps on small grayscale frames, entirely on this Mac
(nothing is sent to Gemini). It reports a visitor when a person-sized area of the picture
keeps moving for MOTION_SUSTAIN_SEC. A change across most of the frame at once (car
headlights, a porch light turning on, the camera shaking) is ignored.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

import config

WORK_WIDTH = 320       # frames are shrunk to this width before processing
WARMUP_FRAMES = 20     # let the background model learn the empty porch first
MIN_BLOB = 0.002       # ignore specks smaller than this share of the frame (noise, moths)
STILL_FACTOR = 0.3     # movement below MIN_AREA * this counts as "porch is still"
RELEARN_RATE = 0.1     # fast background update after a lighting change or camera shake

Box = Tuple[float, float, float, float]  # x, y, w, h as fractions of the frame


@dataclass
class MotionState:
    area: float = 0.0        # share of the watched area that is moving (0-1)
    boxes: List[Box] = field(default_factory=list)
    visitor: bool = False    # person-sized motion that lasted MOTION_SUSTAIN_SEC
    ignored: bool = False    # too much changed at once (lighting or shake)
    quiet_for: float = 0.0   # seconds since anything moved


class MotionDetector:
    def __init__(self, camera, fps: float = 10.0, roi: Optional[Box] = None):
        self.camera = camera
        self.interval = 1.0 / fps
        self.roi = roi if roi is not None else config.MOTION_ROI
        self._sub = cv2.createBackgroundSubtractorMOG2(history=150, varThreshold=32, detectShadows=True)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._roi_mask: Optional[np.ndarray] = None
        self._frames = 0
        self._relearn = False
        self._present_since: Optional[float] = None
        self._last_movement = time.monotonic()
        self._last_debug = 0.0
        self._was_moving = False
        self._state = MotionState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ---- thread ----------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        where = "area " + ",".join(f"{v:g}" for v in self.roi) if self.roi else "whole frame"
        print(f"[Motion] Watching the porch ({where}, visitor = {config.MOTION_MIN_AREA:.0%}+ of the frame moving).")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _loop(self):
        last = None
        warned = False
        while self._running:
            t = time.monotonic()
            with self.camera.lock:
                frame = self.camera.latest_frame  # the camera thread swaps in a new array per frame
            if frame is not None and frame is not last:
                last = frame
                try:
                    self.process(frame, t)
                except Exception as e:
                    if not warned:
                        print(f"[Motion] Error processing frame: {e}")
                        warned = True
            time.sleep(max(0.0, self.interval - (time.monotonic() - t)))

    # ---- detection -------------------------------------------------------

    def snapshot(self) -> MotionState:
        with self._lock:
            s = self._state
            quiet_for = time.monotonic() - self._last_movement
        return MotionState(s.area, list(s.boxes), s.visitor, s.ignored, quiet_for)

    def _watched_mask(self, shape) -> Optional[np.ndarray]:
        if not self.roi:
            return None
        if self._roi_mask is None or self._roi_mask.shape != shape:
            h, w = shape
            x, y, rw, rh = self.roi
            mask = np.zeros(shape, dtype=np.uint8)
            mask[int(y * h):int((y + rh) * h), int(x * w):int((x + rw) * w)] = 255
            self._roi_mask = mask
        return self._roi_mask

    def process(self, frame: np.ndarray, now: float) -> MotionState:
        """Feeds one camera frame through the detector (called by the thread; also handy for tests)."""
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (WORK_WIDTH, max(1, int(h * WORK_WIDTH / w))), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        gray = cv2.GaussianBlur(gray, (7, 7), 0)
        fg = self._sub.apply(gray, learningRate=RELEARN_RATE if self._relearn else -1)

        self._frames += 1
        if self._frames <= WARMUP_FRAMES:
            with self._lock:
                self._last_movement = now
                self._state = MotionState()
            return self._state

        _, mask = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)  # MOG2 marks shadows as 127; drop them
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=2)
        watched = self._watched_mask(mask.shape)
        watched_px = mask.size
        if watched is not None:
            mask = cv2.bitwise_and(mask, watched)
            watched_px = max(1, int(np.count_nonzero(watched)))

        mh, mw = mask.shape
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        boxes: List[Box] = []
        area = 0.0
        for i in range(1, n):  # 0 is the background
            x, y, bw, bh, px = (int(v) for v in stats[i])
            share = px / watched_px
            if share < MIN_BLOB:
                continue
            area += share
            boxes.append((x / mw, y / mh, bw / mw, bh / mh))

        ignored = area > config.MOTION_MAX_AREA
        self._relearn = ignored
        present = not ignored and area >= config.MOTION_MIN_AREA
        held_for = (now - self._present_since) if self._present_since is not None else 0.0
        if present:
            if self._present_since is None:
                self._present_since = now
        else:
            if config.MOTION_DEBUG and self._present_since is not None:
                print(f"[Motion debug] Lost it after {held_for:.1f}s of the {config.MOTION_SUSTAIN_SEC:g}s needed "
                      f"(moving area dropped to {area:.1%}{', ignored' if ignored else ''}).")
            self._present_since = None
        visitor = present and (now - self._present_since) >= config.MOTION_SUSTAIN_SEC

        if config.MOTION_DEBUG:
            moving = area >= MIN_BLOB
            if (moving and now - self._last_debug >= 0.5) or (self._was_moving and not moving):
                self._last_debug = now
                largest = max((bw * bh for _, _, bw, bh in boxes), default=0.0)
                held = f"held {now - self._present_since:.1f}/{config.MOTION_SUSTAIN_SEC:g}s" if present else "not held"
                print(f"[Motion debug] moving {area:5.1%} (need {config.MOTION_MIN_AREA:.1%}) | "
                      f"{len(boxes)} blob(s), biggest box {largest:.1%} | {held} | "
                      f"{'IGNORED (too much changed) | ' if ignored else ''}"
                      f"quiet {now - self._last_movement:.1f}s{' | VISITOR' if visitor else ''}")
            self._was_moving = moving

        with self._lock:
            if not ignored and area >= config.MOTION_MIN_AREA * STILL_FACTOR:
                self._last_movement = now
            self._state = MotionState(area, boxes, visitor, ignored, now - self._last_movement)
        return self._state
