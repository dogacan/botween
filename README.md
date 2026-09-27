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
