"""
Optional recording of each conversation for later replay (RECORD_SESSIONS=true or --record).

Every conversation gets its own folder under RECORD_DIR, e.g. recordings/2026-10-31/19-42-07_motion/:
  events.jsonl       one JSON object per line: {"t": seconds since start, "event": ..., ...}
  frame_001.jpg ...  every camera frame sent to Gemini, exactly as sent
  system_prompt.md   the instructions Gemini had for this conversation

Events: start, cue, frame, visitor, bonesy, tool, note, end. Audio isn't kept, only Gemini's
transcripts of it. Play one back with replay.py.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


class SessionRecorder:
    """Writes one conversation's timeline and frames. Recording problems never stop Bonesy."""

    def __init__(self, root: Path, trigger: str):
        now = datetime.now()
        self.dir = root / now.strftime("%Y-%m-%d") / f"{now.strftime('%H-%M-%S')}_{trigger}"
        self.t0 = time.monotonic()
        self.frames = 0
        self._file = None
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._file = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        except OSError as e:
            print(f"[Record] Can't record this conversation ({e}).")
        self.event("start", trigger=trigger, time=now.isoformat(timespec="seconds"))

    def event(self, kind: str, **data):
        if not self._file:
            return
        try:
            line = {"t": round(time.monotonic() - self.t0, 2), "event": kind, **data}
            self._file.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            self._file.flush()
        except OSError as e:
            print(f"[Record] Stopped recording this conversation ({e}).")
            self._file = None

    def frame(self, jpeg: bytes, how: str) -> Optional[str]:
        """Saves a frame sent to Gemini; returns its file name."""
        if not self._file:
            return None
        self.frames += 1
        name = f"frame_{self.frames:03d}.jpg"
        try:
            (self.dir / name).write_bytes(jpeg)
        except OSError as e:
            print(f"[Record] Couldn't save {name} ({e}).")
            return None
        self.event("frame", file=name, how=how)
        return name

    def save_text(self, name: str, text: str):
        if not self._file:
            return
        try:
            (self.dir / name).write_text(text, encoding="utf-8")
        except OSError:
            pass

    def close(self):
        if self._file:
            self.event("end")
            self._file.close()
            self._file = None
            print(f"[Record] Saved to {self.dir}")


class NoRecorder:
    """Stand-in when recording is off."""

    dir = None

    def event(self, kind: str, **data):
        pass

    def frame(self, jpeg: bytes, how: str) -> Optional[str]:
        return None if jpeg else ""

    def save_text(self, name: str, text: str):
        pass

    def close(self):
        pass
