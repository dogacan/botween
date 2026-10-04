import threading
import time
from typing import Optional

import cv2
import numpy as np

WINDOW_NAME = "Bonesy Eye View  (live | last sent to Gemini)  -  press Q to close"
PANE_HEIGHT = 360
LABEL_BAR_HEIGHT = 34


def _fit_to_height(img: np.ndarray, height: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = height / float(h)
    return cv2.resize(img, (max(1, int(w * scale)), height), interpolation=cv2.INTER_AREA)


def _placeholder(width: int, height: int, text: str) -> np.ndarray:
    pane = np.full((height, width, 3), 30, dtype=np.uint8)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
    cv2.putText(
        pane, text, (max(8, (width - tw) // 2), (height + th) // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 160, 160), 1, cv2.LINE_AA,
    )
    return pane


def _labeled(pane: np.ndarray, label: str, color) -> np.ndarray:
    bar = np.full((LABEL_BAR_HEIGHT, pane.shape[1], 3), 20, dtype=np.uint8)
    cv2.putText(bar, label, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
    return np.vstack([bar, pane])


def compose_canvas(
    live_frame: Optional[np.ndarray],
    sent_jpeg: Optional[bytes],
    sent_age_sec: Optional[float],
    status: str,
) -> np.ndarray:
    """Builds the side-by-side preview image (pure function, easy to test headlessly)."""
    # Left pane: live camera feed
    if live_frame is not None:
        live_pane = _fit_to_height(live_frame, PANE_HEIGHT)
    else:
        live_pane = _placeholder(int(PANE_HEIGHT * 16 / 9), PANE_HEIGHT, "No camera frame")

    # Right pane: decode the exact bytes that were sent to the model
    sent_img = None
    if sent_jpeg:
        sent_img = cv2.imdecode(np.frombuffer(sent_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if sent_img is not None:
        sent_pane = _fit_to_height(sent_img, PANE_HEIGHT)
        h, w = sent_img.shape[:2]
        kb = len(sent_jpeg) // 1024
        age = f"{sent_age_sec:.0f}s ago" if sent_age_sec is not None else ""
        sent_label = f"LAST SENT TO GEMINI  {w}x{h}  {kb} KB  {age}"
        sent_color = (80, 220, 120)
    else:
        sent_pane = _placeholder(live_pane.shape[1], PANE_HEIGHT, "Nothing sent yet")
        sent_label = "LAST SENT TO GEMINI"
        sent_color = (160, 160, 160)

    left = _labeled(live_pane, "LIVE", (230, 230, 230))
    right = _labeled(sent_pane, sent_label, sent_color)
    canvas = np.hstack([left, right])

    # Status footer
    footer = np.full((LABEL_BAR_HEIGHT, canvas.shape[1], 3), 20, dtype=np.uint8)
    cv2.putText(footer, f"Status: {status}", (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 180, 255), 1, cv2.LINE_AA)
    return np.vstack([canvas, footer])


class Preview:
    """
    Optional OpenCV window showing the live feed next to the exact frame last sent to Gemini.

    NOTE: On macOS, GUI calls must run on the main thread, so `run()` is meant to be
    called from the main thread while the conversation loop runs in a worker thread.
    The worker only touches the thread-safe `update_sent()` / `set_status()` methods.
    """

    def __init__(self, camera):
        self.camera = camera
        self._lock = threading.Lock()
        self._sent_jpeg: Optional[bytes] = None
        self._sent_time: Optional[float] = None
        self._status = "Starting..."

    def update_sent(self, jpeg_bytes: Optional[bytes]):
        """Records the exact JPEG bytes that were just sent to the model."""
        with self._lock:
            self._sent_jpeg = jpeg_bytes
            self._sent_time = time.time()

    def set_status(self, status: str):
        with self._lock:
            self._status = status

    def run(self, stop_event: threading.Event):
        """Blocks on the main thread, refreshing the window until stop_event is set or Q/ESC is pressed."""
        try:
            while not stop_event.is_set():
                with self.camera.lock:
                    live = None if self.camera.latest_frame is None else self.camera.latest_frame.copy()
                with self._lock:
                    sent, sent_time, status = self._sent_jpeg, self._sent_time, self._status
                age = (time.time() - sent_time) if sent_time else None

                cv2.imshow(WINDOW_NAME, compose_canvas(live, sent, age, status))
                key = cv2.waitKey(33) & 0xFF
                if key in (ord("q"), 27):  # Q or ESC closes the window only
                    print("[Preview] Window closed (Bonesy keeps running).")
                    break
        finally:
            cv2.destroyAllWindows()
            cv2.waitKey(1)  # lets macOS actually tear the window down
        # If the window was closed but Bonesy is still running, just wait for shutdown.
        while not stop_event.is_set():
            stop_event.wait(0.5)
