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
On startup Bonesy connects to the skeleton over BLE, turns on live mode, finds the `Skelly` audio output (falling back to the system default), and says an awakening line. When the mic hears a voice he opens a Live session, including the second of audio from just before the trigger. The session closes after `LIVE_IDLE_TIMEOUT_SEC` of quiet, so each visitor gets a fresh conversation.

### Hardware controls from the terminal
```bash
python3 skelly_ble.py volume 200     # 0-255
python3 skelly_ble.py eyes red       # head light: red/orange/yellow/green/blue/cyan/purple/pink/white or #RRGGBB
python3 skelly_ble.py torso purple
python3 skelly_ble.py move all       # head / arm / torso / all
```
While talking, Gemini can also call `change_eye_color` and `move` itself for dramatic effect.

### Live tuning (`.env`)
* `LIVE_END_SILENCE_MS` (default 800): how long a pause ends the visitor's turn. Lower is snappier but may cut kids off.
* `LIVE_ECHO_TAIL_SEC` (default 0.6): the mic stays muted this long after Bonesy stops, so he doesn't hear himself through the Bluetooth delay.
* `LIVE_BARGE_IN=true` lets visitors interrupt him. Only enable it if the mic can't hear the skeleton's speaker.
* `LIVE_KEEPALIVE_LEVEL` (default 0.001): faint noise between replies so the speaker doesn't clip his first word. Set it to 0 if you can hear it.
* Stage tags in `prompt.md` (`<cackle>` etc.) are acted out by the Live voice directly. They still show up in the printed transcript, but he doesn't say them.
* Use a wired or USB mic, not a Bluetooth one. Bluetooth mics switch macOS into low-quality headset mode.

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

## 🎭 Customizing the Skeleton Persona & Stage Directions

The skeleton's personality, costume observation instructions, jokes, and stage directions are in **[`prompt.md`](prompt.md)**.
* **Stage Direction Cues**: Bonesy uses inline theatrical tags that the voice engine acts out:
  * `<gasp>`: A sharp, audible gasp upon seeing a visitor or costume.
  * `<whisper> ... </whisper>`: Leaning in close to whisper secrets or riddles.
  * `<cackle>` or `<laugh>`: Wicked cackling and raspy skeleton giggles.
  * `<pause>`: Theatrical comedic timing before punchlines.
* **Live Reload**: `skeleton.py` re-reads `prompt.md` on every interaction, so you can edit the prompt while the script is running without restarting!

---

## 🎛️ Customizing Models & Voice

You can adjust all model and sound parameters in `.env`:
* **Brain Model**: `GEMINI_MODEL=gemini-3.8-flash` (or `gemini-3.5-flash-lite` for ultra-fast token replies).
* **TTS Engine**: `TTS_ENGINE=gemini` (default, uses native expressive acting) or `edge` (offline Microsoft neural voice).
* **Gemini Voice Model**: `GEMINI_TTS_MODEL=gemini-3.8-flash-lite-tts` (fast & economical) or `gemini-3.8-flash-tts` (studio grade).
* **Spooky FX**: `ENABLE_SPOOKY_FX=true` applies FFmpeg's `aecho` and low-frequency resonance filters on top of the voice.
* **Edge-TTS Fallback**: `TTS_VOICE=en-US-ChristopherNeural`, `TTS_PITCH=-15Hz`, `TTS_RATE=-5%`.
* **Sensitivity**: Adjust `VAD_ENERGY_THRESHOLD=0.03` if your environment is noisy (traffic, wind).
