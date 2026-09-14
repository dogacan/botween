# 💀 Botween: The Talking Halloween Skeleton 🎃

An AI-powered, interactive talking skeleton prop designed for Halloween trick-or-treaters. 

Botween uses your webcam to inspect trick-or-treaters' costumes, listens to what they say via microphone with Voice Activity Detection (VAD), sends the image and audio directly into **Gemini 2.0 Flash**, and speaks back in a spooky, kid-friendly voice with crypt reverb using **Edge-TTS** and **FFmpeg**.

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
* `--preview`: Opens an OpenCV window showing the skeleton's live camera view.
* `--no-camera`: Runs audio-only (no webcam snapshot).

## 🎭 Customizing the Skeleton Persona & Prompt

The skeleton's personality, costume observation instructions, jokes, and response rules are in **[`prompt.md`](prompt.md)**.
* You can freely edit this file in your favorite text editor to tweak its personality, add custom jokes, or adjust costume reactions.
* **Live Reload**: `skeleton.py` re-reads `prompt.md` on every interaction, so you can edit the prompt while the script is running without restarting!

---

## 🎛️ Customizing Voice & Hardware

You can adjust all voice and sound parameters in `.env`:
* **Voice**: `TTS_VOICE=en-US-ChristopherNeural` (or try `en-US-GuyNeural`, `en-GB-RyanNeural`, `en-US-JennyNeural`).
* **Pitch**: `TTS_PITCH=-15Hz` or `-25Hz` for a deeper, more ominous crypt voice.
* **Speed**: `TTS_RATE=-5%` or `-10%` for slower, deliberate pacing.
* **Spooky FX**: `ENABLE_SPOOKY_FX=true` applies FFmpeg's `aecho` and low-frequency resonance filters.
* **Sensitivity**: Adjust `VAD_ENERGY_THRESHOLD=0.03` if your environment is noisy (traffic, wind).
