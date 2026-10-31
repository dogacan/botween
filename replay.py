"""
Replay conversations saved with --record (or RECORD_SESSIONS=true).

  python3 replay.py                    list recorded conversations
  python3 replay.py latest             print the newest conversation's timeline
  python3 replay.py <folder>           print a conversation's timeline
  python3 replay.py latest --show      play it back in real time, showing each frame Gemini got
  python3 replay.py latest --show --speed 2   ...twice as fast

<folder> is a path, or the part after the recordings folder (e.g. 2026-10-31/19-42-07_motion).
In the --show window, press Q or ESC to stop.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import config

WINDOW = "Bonesy replay  -  frame Gemini got  (Q to stop)"


def find_sessions(root: Path):
    return sorted(p.parent for p in root.glob("*/*/events.jsonl"))


def load_events(folder: Path):
    events = []
    for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # a conversation cut off mid-write
    return events


def describe(ev) -> str:
    kind = ev.get("event")
    if kind == "start":
        return f"--- conversation started by {ev.get('trigger')} at {ev.get('time')} ---"
    if kind == "end":
        return "--- end ---"
    cut = " (cut off)" if ev.get("cut_off") else ""
    if kind == "visitor":
        return f"Visitor: {ev.get('text')}{cut}"
    if kind == "bonesy":
        return f"Bonesy:  {ev.get('text')}{cut}"
    if kind == "cue":
        with_frame = f" (with {ev['frame']})" if ev.get("frame") else ""
        return f"[cue{with_frame}] {ev.get('text')}"
    if kind == "frame":
        return f"[frame] {ev.get('file')} ({ev.get('how')})"
    if kind == "tool":
        return f"[body] {ev.get('name')}({ev.get('args')}) -> {ev.get('result')}"
    if kind == "note":
        return f"[note] {ev.get('text')}"
    return json.dumps(ev, ensure_ascii=False)


def list_sessions(root: Path):
    sessions = find_sessions(root)
    if not sessions:
        print(f"No recordings in {root}. Run Bonesy with --record (or RECORD_SESSIONS=true).")
        return
    for folder in sessions:
        events = load_events(folder)
        length = events[-1]["t"] if events else 0
        frames = sum(1 for e in events if e.get("event") == "frame")
        first = next((describe(e) for e in events if e.get("event") in ("visitor", "bonesy")), "(nothing said)")
        print(f"{folder.relative_to(root)}  {length:5.0f}s  {frames:2d} frames  {first[:70]}")


def resolve(root: Path, name: str) -> Path:
    if name == "latest":
        sessions = find_sessions(root)
        if not sessions:
            sys.exit(f"No recordings in {root}.")
        return sessions[-1]
    for candidate in (Path(name), root / name):
        if (candidate / "events.jsonl").exists():
            return candidate
    sys.exit(f"No recording at '{name}'. Run 'python3 replay.py' to list them.")


def print_timeline(folder: Path):
    print(folder)
    for ev in load_events(folder):
        print(f"{ev.get('t', 0):7.2f}s  {describe(ev)}")


def show(folder: Path, speed: float):
    import cv2

    start = time.monotonic()
    for ev in load_events(folder):
        due = start + ev.get("t", 0) / speed
        while time.monotonic() < due:  # keep the window responsive while waiting
            if cv2.waitKey(30) & 0xFF in (ord("q"), 27):
                cv2.destroyAllWindows()
                return
        print(f"{ev.get('t', 0):7.2f}s  {describe(ev)}", flush=True)
        if ev.get("event") == "frame":
            img = cv2.imread(str(folder / ev["file"]))
            if img is not None:
                cv2.imshow(WINDOW, img)
                cv2.waitKey(1)
    print("(done - press any key in the window to close)")
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="Replay Bonesy conversations saved with --record.")
    parser.add_argument("session", nargs="?", help="'latest', a folder, or omit to list recordings")
    parser.add_argument("--show", action="store_true", help="play back in real time with the frames")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed for --show (default 1)")
    args = parser.parse_args()

    root = config.RECORD_DIR
    if not args.session:
        list_sessions(root)
        return
    folder = resolve(root, args.session)
    if args.show:
        show(folder, max(0.1, args.speed))
    else:
        print_timeline(folder)


if __name__ == "__main__":
    main()
