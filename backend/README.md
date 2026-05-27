# AI Music Web Backend v2.6

Converts any supported audio file to a normalised WAV (16-bit, 44.1 kHz,
mono), applies **real Auto-Tune pitch correction** (librosa F0 detection +
per-segment phase-vocoder pitch-shifting toward the target key/scale),
and returns audio analysis, parameter sync, and an engine-ready
Auto-Tune profile with retune_speed, humanize, formant_preserve, and
vibrato_preserve.  Pitch correction is an MVP — not commercial-grade,
but genuinely changes pitch.

## Prerequisites

### 1. Python 3.10+

> **Python 3.13 note**: Python 3.13 removed the built-in `audioop` module.
> pydub depends on it, so `audioop-lts` (a drop-in replacement) is included
> in `requirements.txt` for Python ≥ 3.13.  No extra steps needed — just
> `pip install -r backend\requirements.txt` as usual.

### 2. ffmpeg

**pydub** uses ffmpeg under the hood for decoding and encoding audio.

**Windows** — the easiest way:

```powershell
winget install ffmpeg
```

Or download from https://ffmpeg.org/download.html and add the `bin`
folder to your system `PATH`.  Restart your terminal after installing.

**macOS**:

```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**:

```bash
sudo apt install ffmpeg
```

Verify it works:

```bash
ffmpeg -version
```

## Start Locally

From the project root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

## Test the Backend

Open your browser and visit:

- Health check: http://127.0.0.1:8000/health
- Interactive API docs: http://127.0.0.1:8000/docs

### Upload a file with curl

```bash
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.mp3" \
  -o converted.wav
```

The downloaded `converted.wav` will be a 16-bit 44.1 kHz mono WAV.

### Audio Analysis Response Headers

The `/process-vocal` response includes these custom headers with analysis
data measured from the **original** uploaded audio (before conversion):

| Header | Example | Meaning |
|---|---|---|
| `X-Duration-Seconds` | `3.52` | Length in seconds |
| `X-Sample-Rate` | `44100` | Original sample rate (Hz) |
| `X-Channels` | `2` | Original channel count |
| `X-Peak-dBFS` | `-1.23` | Highest sample level |
| `X-Average-dBFS` | `-18.45` | RMS loudness |
| `X-Too-Quiet` | `false` | `true` when average < −30 dBFS |
| `X-Clipped-Risk` | `false` | `true` when peak > −0.3 dBFS |

To see them, use `curl -v`:

```bash
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.mp3" \
  -o converted.wav
```

### Processing Settings

The `/process-vocal` endpoint also accepts optional form fields that are
echoed back in the `X-Processing-Settings` header (URL-encoded JSON):

| Form Field | Default | Description |
|---|---|---|
| `autotune_strength` | `40` | Auto-Tune intensity (0–100) |
| `key` | `C` | Root key (C–B) |
| `scale` | `major` | Scale type (`major` or `minor`) |
| `beat_style` | `清爽电子` | Selected beat style |

Example with all parameters:

```bash
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.mp3" \
  -F "autotune_strength=60" \
  -F "key=D" \
  -F "scale=minor" \
  -F "beat_style=沉浸 Trap" \
  -o converted.wav
```

The `X-Processing-Settings` response header will contain a URL-encoded
JSON object mirroring the received parameters.

### Auto-Tune Profile Header

The response also includes `X-Autotune-Profile`, a URL-encoded JSON object
with engine-ready parameters derived from audio analysis and user settings:

| Field | Example | Description |
|---|---|---|
| `style_mode` | `pop` | `natural` / `pop` / `rnb` / `trap` / `robotic` |
| `style_mode_label` | `流行` | Chinese label for the style |
| `retune_speed` | `55` | 0–100, higher = faster pitch snap |
| `correction_amount` | `70` | 0–100, how much correction to apply |
| `humanize` | `50` | 0–100, timing jitter for natural feel |
| `formant_preserve` | `70` | 0–100, keep original vocal character |
| `vibrato_preserve` | `60` | 0–100, keep natural vibrato |
| `target_key` | `C` | Target root key |
| `target_scale` | `major` | Target scale |
| `target_scale_label` | `大调` | Chinese label for the scale |
| `vocal_quality` | `正常` | Quality assessment |
| `reason` | (string) | Why these parameters were chosen |
| `next_step` | (string) | Suggested next action for the user |

### Style Mode Mapping

| Strength | Default Mode | Characteristics |
|---|---|---|
| < 30 | `natural` | Slow retune, high humanize, max vibrato/formant preserve |
| 30–59 | `pop` | Medium retune, balanced humanize |
| 60–80 | `trap` | Fast retune, high correction, low humanize |
| > 80 | `robotic` | Very fast retune, minimal humanize/formant/vibrato |

Minor scale increases humanize (+10).  Minor + R&B/Trap beat further
increases vibrato preserve (+15).

Real pitch correction is applied — see **Processing Pipeline** below.
The profile parameters also serve as a bridge to a future pyworld +
formant shifter engine for higher-quality correction.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Check that the backend is running |
| `POST` | `/process-vocal` | Upload → receive a processed WAV with Auto-Tune preview effects |
| `DELETE` | `/uploads/{filename}` | Delete a temporary uploaded or processed file |

## Upload Rules

- Max file size: 25 MB
- Allowed input types: WAV, MP3, MP4 audio, M4A
- Output: always 16-bit 44.1 kHz mono WAV

## Processing Pipeline

```
Upload → [validate] → [save raw] → [convert to WAV] → [analyse]
                                                              ↓
                                          Auto-Tune profile generation
                                                              ↓
                                          Gain staging (clip prot / loudness)
                                                              ↓
                                          F0 detection (librosa.pyin)
                                                              ↓
                                          Per-segment pitch correction
                                          (phase vocoder → target scale)
                                                              ↓
                                          Tonal shaping + peak limiting
                                                              ↓
                                          return processed WAV + headers
```

### Pitch Correction Engine (v2.6)

The `/process-vocal` endpoint now applies **real pitch correction**:

| Step | Effect | Library |
|---|---|---|
| 1 | Gain staging | Clipping prot (−5 dB) + loudness norm (−17 dBFS) |
| 2 | F0 detection | `librosa.pyin` — probabilistic YIN, fmin=C2, fmax=C7 |
| 3 | Target scale mapping | Nearest MIDI note in key/scale (36–96) |
| 4 | Retune smoothing | Median filter: size 21 (natural) … 1 (robotic) |
| 5 | Per-segment pitch shift | ~93 ms segments, 66 % overlap, `librosa.effects.pitch_shift` |
| 6 | Tonal shaping + peak limit | 80 Hz low-cut (trap/robotic), ceiling −0.5 dBFS |

**How `autotune_strength` controls the result:**

| Strength | Style | correction_amount | retune_speed | Audible effect |
|---|---|---|---|---|
| 20 % | natural | 30 % | 28 | Very subtle — gentle nudge toward scale |
| 50 % | pop | 50 % | 50 | Moderate — audible correction, still natural |
| 70 % | trap | 75 % | 72 | Strong — fast snap, clearly tuned |
| 95 % | robotic | 100 % | 92 | Maximum — instant snap, hard-tuned sound |

- **correction_amount** = pitch blend (0 % = dry, 100 % = full snap)
- **retune_speed** = smoothing window size (higher = less smoothing = faster snap)

The `X-Processing-Status` response header is `"autotune-preview"` when
processing succeeds, or `"converted-wav"` on fallback.

### Testing Different Strength Levels

```bash
# Low strength (subtle, natural) — strength 20
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@vocal.wav" -F "autotune_strength=20" -o strength_20.wav

# Medium strength (audible correction) — strength 50
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@vocal.wav" -F "autotune_strength=50" -o strength_50.wav

# High strength (hard-tuned) — strength 95
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@vocal.wav" -F "autotune_strength=95" -o strength_95.wav
```

Compare the three files — they should sound progressively more tuned.

Future steps:

1. Higher-quality F0 tracking (pyworld / CREPE for better accuracy)
2. Real-time formant preservation during pitch shifting
3. Sample-level pitch correction (PSOLA / WSOLA) instead of segment-based
4. High-quality Beat generation or MIDI rendering
5. Vocal + Beat mixing into a final downloadable stereo file

## Project Structure

```
backend/
├── app.py              # FastAPI entry point
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── .gitignore
├── uploads/            # Raw uploaded files (auto-created)
├── processed/          # Converted WAV files (auto-created)
└── .venv/              # Virtual environment (not committed)
```

## Current Limits

This version does **not**:

- Use formant preservation (pitch shifting changes formants slightly)
- Perform sample-level pitch correction (segment-based only, ~93 ms windows)
- Generate a real Beat
- Mix vocal and Beat
- Store user accounts or project history

It **does** apply real pitch correction: librosa F0 detection → per-segment
phase-vocoder pitch shifting toward the target key/scale, with
`correction_amount` controlling blend and `retune_speed` controlling
snap speed.
