# 💀 Botween: The Talking Halloween Skeleton 🎃

An AI-powered, interactive talking skeleton prop designed for Halloween trick-or-treaters. 

Botween uses your webcam to inspect trick-or-treaters' costumes, listens to what they say via microphone with Voice Activity Detection (VAD), sends the image and audio directly into **Gemini** (`gemini-3.8-flash` by default), and speaks back in a spooky, kid-friendly voice using **Gemini expressive TTS** (or **Edge-TTS** as a fallback) with crypt reverb from **FFmpeg**.

---

## 🚀 Quick Setup

### 1. Configure Gemini API Key
Copy `.env.example` to `.env` and add your Google Gemini API key:
```bash
cp .env.example .env
```
Open `.env` and set:
```ini
GEMINI_API_KEY=AIzaSy...
```
*(You can get a free API key at [Google AI Studio](https://aistudio.google.com/)).*

---

## 🧪 Testing Your Hardware

Run the diagnostic script to test your Mac's speakers, microphone, and camera independently:
```bash
python3 test_devices.py
```
This will:
1. Play a sample spooky greeting through your speakers with crypt reverb.
2. Calibrate and record a 3-second test from your microphone.
3. Capture a snapshot from your webcam and save it to `test_snapshot.jpg`.

### Choosing the camera and microphone

Both default to the **USB Camera** (its mic shows up as "USB 2.0 Camera", which also matches). List what's connected:
```bash
python3 devices.py
```
Change the defaults in `.env` with `CAMERA_DEVICE` and `MIC_DEVICE`. Each takes a name (case-insensitive word match) or an index. A blank `MIC_DEVICE` uses the system default mic. To override them for one run, add `--camera` or `--mic`:
```bash
python3 skeleton.py --camera "MacBook Air Camera" --mic "MacBook Air Microphone"
```
If the chosen device isn't connected, Bonesy prints a warning and falls back to camera 0 and the system default mic.

If the camera is mounted upside down, set `CAMERA_ROTATE_180=true` in `.env` (or add `--rotate-180`). The preview, motion detection and Gemini then all see the picture right side up.

---

## 💀 Ultra Skelly + Gemini Live (default engine)

By default Bonesy uses the **Gemini Live API** (`gemini-3.8-live`): mic audio and camera frames stream in, and his voice streams back about a second after a visitor stops talking. The voice goes to the Home Depot 6.5 ft **Ultra Skelly**'s own speaker, which moves the jaw in time with the audio.

The skeleton has two Bluetooth radios:
* **Control link (BLE)**, advertised as `Ultra Skelly v2` (or `Animated Skelly` on old firmware). `skelly_ble.py` uses it to turn on live mode, set the volume, change the eye/torso light color and trigger movements.
* **Live speaker (classic Bluetooth)**, `Ultra Skelly v2(Live)`, PIN `1234`. It only appears after live mode is turned on.

### One-time setup
1. `pip3 install -r requirements.txt` (adds `bleak`).
2. Power on the skeleton, switch it to Bluetooth mode, and **close the phone app** (only one controller can connect at a time).
3. From your own Terminal, run `python3 skelly_ble.py scan`. macOS will ask for Bluetooth permission the first time; allow it.
4. `python3 skelly_ble.py live` turns on the live speaker. Then go to **System Settings → Bluetooth** and connect `Ultra Skelly v2(Live)` (PIN `1234`).
   *Optional:* `brew install blueutil` lets Bonesy reconnect the speaker automatically on later runs.

### Run
```bash
python3 skeleton.py              # live engine, hands-free
python3 skeleton.py --preview    # plus the camera preview window
python3 skeleton.py --engine classic   # the original record → generate → TTS pipeline
```
On startup Bonesy connects to the skeleton over BLE, turns on live mode, and finds the `Skelly` audio output (falling back to the system default). He stays quiet until someone shows up; set `STARTUP_GREETING=true` if you want an awakening line at startup (handy for checking the speaker). When the mic hears a voice he opens a Live session, including the second of audio from just before the trigger. The session closes after `LIVE_IDLE_TIMEOUT_SEC` of quiet, so each visitor gets a fresh conversation.

### Hardware controls from the terminal
```bash
python3 skelly_ble.py volume 200     # 0-255
python3 skelly_ble.py eyes red       # head light: red/orange/yellow/green/blue/cyan/purple/pink/white or #RRGGBB
python3 skelly_ble.py torso purple
python3 skelly_ble.py move all       # head / arm / torso / all
python3 skelly_ble.py move none      # switch all body movement off
```
While talking, Gemini can also call `change_eye_color` itself for dramatic effect.

Gemini can call `move` too, but only for the parts in `SKELLY_MOVEMENT` (default `head,arm`). The skeleton moves every enabled part whenever it plays sound, and the setting stays on it between runs, so Bonesy sets it himself: `SKELLY_IDLE_MOVEMENT` (default `head`) until a visitor speaks, and `SKELLY_MOVEMENT` once they do. So the arm stays still between visitors, during the startup greeting and while greeting someone who walked up, and only starts moving when a kid talks to him. Anything that moves when nobody is talking can set off motion detection, which is why the torso is off entirely. Each takes `head`, `arm`, `torso`, a comma-separated list, `all` or `none`.

### Recording and replay
Run with `--record` (or `RECORD_SESSIONS=true`) to keep each conversation in `recordings/<date>/<time>_<trigger>/`: a timeline (`events.jsonl`) of what woke Bonesy, the cues he got, what the visitor and Bonesy said (Gemini's transcripts; the audio itself isn't kept), his eye and body moves and why the conversation ended, plus every camera frame sent to Gemini and the prompt he had.
```bash
python3 replay.py                    # list recordings
python3 replay.py latest             # print the newest one's timeline
python3 replay.py latest --show      # play it back in real time with the frames (Q to stop)
```
The frames are pictures of visitors, often kids, so recording is off by default and `recordings/` is git-ignored. Delete it when you're done.

### Live tuning (`.env`)
* `LIVE_END_SILENCE_MS` (default 800): how long a pause ends the visitor's turn. Lower is snappier but may cut kids off.
* `LIVE_ECHO_TAIL_SEC` (default 1.2): the mic stays muted this long after Bonesy stops, so he doesn't hear himself through the Bluetooth delay.
* `LIVE_LANGUAGE` (default `en-US`): Bonesy's default language. He switches when a visitor speaks a full sentence in another language (and stays in it with that visitor), but a stray word or his own echo won't flip him into Spanish.
* `LIVE_BARGE_IN=true` lets visitors interrupt him. Only enable it if the mic can't hear the skeleton's speaker.
* `LIVE_VIDEO_INTERVAL_SEC` (default 5): during a conversation Bonesy sends Gemini one camera frame at most this often. He also sends a fresh frame when someone walks up or starts talking, so a longer gap mostly saves tokens.
* `LIVE_KEEPALIVE_LEVEL` (default 0.001): faint noise between replies so the speaker doesn't clip his first word. Set it to 0 if you can hear it.
* **Voice:** `LIVE_VOICE_NAME` (default `Algenib`, a gravelly voice that measured among the deepest of Gemini's 30 voices; `Alnilam`, `Gacrux` and `Umbriel` are similar).
* **Voice FX** (`ENABLE_SPOOKY_FX=true`): `voice_fx.py` lowers the pitch by `LIVE_PITCH_SEMITONES` (default -3), adds the crypt reverb, and adds a repeating echo (`LIVE_ECHO_DELAY_MS`, `LIVE_ECHO_FEEDBACK`, `LIVE_ECHO_MIX`). It runs on the live stream and adds about 30 ms of delay.
* `LIVE_OUTPUT_GAIN` (default 1.0): makes the voice louder before it goes to the speaker (2.0 is about twice as loud). Loud peaks are softly limited so they don't crackle. For the skeleton's own speaker volume, set `SKELLY_VOLUME` (0-255).
* Stage tags in `prompt.md` (`<cackle>` etc.) are acted out by the Live voice directly. They still show up in the printed transcript, but he doesn't say them.
* Use a wired or USB mic, not a Bluetooth one. Bluetooth mics switch macOS into low-quality headset mode.

### Motion trigger (greets visitors as they walk up)
In auto mode the camera also wakes Bonesy, so kids who just grab candy without a word still get a costume comment. `motion.py` watches the porch locally (nothing is sent to Gemini until someone shows up). When a person-sized area keeps moving for half a second, it opens a Live session, sends a fresh frame and tells Bonesy that someone walked up. If the visitor starts talking first, their words lead instead. If they never say anything, the session closes `MOTION_SILENT_TIMEOUT_SEC` after his line.

If a visitor stays quiet for `MOTION_JOKE_AFTER_SEC` (default 6) after Bonesy speaks and the camera still sees them moving, he tells them one joke from the joke bag to get them talking. This happens at most once per conversation, and works the same whether the conversation started with motion or with voice. He gets a fresh frame with the cue, so if they've already left he just chuckles. Set it to `0` to turn it off.

After each visitor, motion waits until the porch has been still for `MOTION_REARM_QUIET_SEC` (or `MOTION_REARM_MAX_SEC` have passed) so the same kid doesn't set him off twice. Voice still works as before.

Tune it with `--preview`: moving areas are boxed (green once they count as a visitor) and the footer shows how much of the view is moving.
* `MOTION_MIN_AREA` (default 0.03): share of the frame that must move. Raise it if people on the sidewalk trigger him; lower it if kids get close before he notices.
* `MOTION_MAX_AREA` (default 0.5): bigger changes are treated as headlights, a porch light or camera shake, and ignored.
* `MOTION_SUSTAIN_SEC` (default 0.5): how long motion must last.
* `MOTION_ROI`: optional `x,y,width,height` area to watch, as fractions of the frame (for example `0.2,0.3,0.6,0.7` to skip the street at the top). Shown as a blue box in the preview.
* `MOTION_ENABLED=false` or `--no-motion` turns it off. Manual mode and the classic engine don't use it.

---

## 🏃‍♂️ Running the Skeleton

### Option 1: Manual / Indoor Testing Mode (Recommended First)
Press `[ENTER]` whenever you want to speak. This is great for testing inside without ambient TV or keyboard sounds triggering the skeleton:
```bash
python3 skeleton.py --mode manual
```

### Option 2: Autonomous Front-Yard Mode
The skeleton continuously monitors the microphone with automated Voice Activity Detection (VAD) and silence detection:
```bash
python3 skeleton.py --mode auto
```

### Extra Flags
* `--preview`: Opens a window with the **live camera feed on the left** and the **exact JPEG last sent to Gemini on the right** (with its resolution, size, and age), plus a status line (Listening / Thinking / Speaking). Press `Q` or `ESC` in the window to close it; Bonesy keeps running. Ctrl+C in the terminal quits everything.
* `--no-camera`: Runs audio-only (no webcam snapshot). Cannot be combined with `--preview`.
* `--no-motion`: Only voice wakes Bonesy; camera motion is ignored.
* `--camera NAME|INDEX` / `--mic NAME|INDEX`: Pick the camera or microphone for this run (overrides `CAMERA_DEVICE` / `MIC_DEVICE`).
* `--list-devices`: Lists cameras and microphones, marks the selected ones, and exits.
* `--rotate-180`: Rotates the camera picture 180° for an upside-down mount (same as `CAMERA_ROTATE_180=true`).

## 🎭 Customizing the Skeleton Persona & Stage Directions

The skeleton's personality, costume observation instructions, and stage directions are in **[`prompt.md`](prompt.md)**.
* **Jokes**: Bonesy's jokes live in **[`jokes.json`](jokes.json)**, grouped by category (a plain list works too). Each visitor session picks `JOKES_PER_SESSION` (default 5) at random and drops them into the `{{JOKES}}` spot in `prompt.md`, so Bonesy doesn't tell everyone the same one. Jokes can carry square-bracket delivery cues like `[short pause]`, `[sighs]` or `[excited]`; the prompt tells Bonesy to act these out rather than say them, and the edge-tts fallback strips them.
* **Stage Direction Cues**: Bonesy uses inline theatrical tags that the voice engine acts out:
  * `<gasp>`: A sharp, audible gasp upon seeing a visitor or costume.
  * `<whisper> ... </whisper>`: Leaning in close to whisper secrets or riddles.
  * `<cackle>` or `<laugh>`: Wicked cackling and raspy skeleton giggles.
  * `<pause>`: Theatrical comedic timing before punchlines.
* **Live Reload**: Both engines re-read `prompt.md` and `jokes.json` for every visitor, so you can edit them while the script is running without restarting!

---

## 🎛️ Customizing Models & Voice

You can adjust all model and sound parameters in `.env`:
* **Brain Model**: `GEMINI_MODEL=gemini-3.8-flash` (or `gemini-3.5-flash-lite` for ultra-fast token replies).
* **TTS Engine**: `TTS_ENGINE=gemini` (default, uses native expressive acting) or `edge` (offline Microsoft neural voice).
* **Gemini Voice Model**: `GEMINI_TTS_MODEL=gemini-3.8-flash-lite-tts` (fast & economical) or `gemini-3.8-flash-tts` (studio grade).
* **Spooky FX**: `ENABLE_SPOOKY_FX=true` applies FFmpeg's `aecho` and low-frequency resonance filters on top of the voice.
* **Edge-TTS Fallback**: `TTS_VOICE=en-US-ChristopherNeural`, `TTS_PITCH=-15Hz`, `TTS_RATE=-5%`.
* **Sensitivity**: Adjust `VAD_ENERGY_THRESHOLD=0.03` if your environment is noisy (traffic, wind).
