import threading
import time
import cv2
from typing import Optional
import config
from devices import DeviceSpec, resolve_camera


class Camera:
    """
    Background threaded camera reader.
    Continuously clears the webcam hardware buffer to ensure
    get_latest_frame() always retrieves the instantaneous real-time frame.
    """

    def __init__(self, camera: DeviceSpec = None, max_dimension: int = 1280, rotate_180: Optional[bool] = None):
        # Name or index; None uses config.CAMERA_DEVICE. Resolved now, since indexes can shift.
        self.camera_index, self.camera_name = resolve_camera(
            config.CAMERA_DEVICE if camera is None else camera)
        self.rotate_180 = config.CAMERA_ROTATE_180 if rotate_180 is None else rotate_180
        self.max_dimension = max_dimension
        self.cap: Optional[cv2.VideoCapture] = None
        self.latest_frame = None
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Starts the background frame polling thread."""
        if self.running:
            return

        print(f"[Camera] Initializing camera [{self.camera_index}] {self.camera_name}...")
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            print(f"[Camera] Warning: Could not open camera [{self.camera_index}] {self.camera_name}.")
            return

        # Request 1280x720 resolution for crisp costume recognition
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        print(f"[Camera] Background camera feed started{' (rotated 180°)' if self.rotate_180 else ''}.")

    def _capture_loop(self):
        while self.running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                # Rotate here so the preview, motion detection and Gemini all see it upright
                if self.rotate_180:
                    frame = cv2.rotate(frame, cv2.ROTATE_180)
                with self.lock:
                    self.latest_frame = frame
            time.sleep(0.03)  # ~30 FPS

    def get_latest_jpeg_bytes(self, quality: int = 85, max_dimension: Optional[int] = None) -> Optional[bytes]:
        """Returns the most recent camera frame encoded as JPEG bytes."""
        with self.lock:
            if self.latest_frame is None:
                return None
            frame = self.latest_frame.copy()

        # Resize if larger than max_dimension to optimize API latency
        max_dimension = max_dimension or self.max_dimension
        h, w = frame.shape[:2]
        if max(h, w) > max_dimension:
            scale = max_dimension / float(max(h, w))
            new_w, new_h = int(w * scale), int(h * scale)
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Encode to JPEG bytes
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        success, buffer = cv2.imencode(".jpg", frame, encode_params)
        if success:
            return buffer.tobytes()
        return None

    def stop(self):
        """Stops background reader and releases camera hardware."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap:
            self.cap.release()
            self.cap = None
        print("[Camera] Camera released.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
