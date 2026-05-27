# AI Music Web Backend v2.2

This backend accepts any supported audio file, converts it to a
normalised WAV (16-bit, 44.1 kHz, mono), and returns it.  It is the
foundation that future Auto-Tune, beat generation, mixing, and export
features will build on.

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

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Check that the backend is running |
| `POST` | `/process-vocal` | Upload an audio file → receive a normalised WAV |
| `DELETE` | `/uploads/{filename}` | Delete a temporary uploaded or processed file |

## Upload Rules

- Max file size: 25 MB
- Allowed input types: WAV, MP3, MP4 audio, M4A
- Output: always 16-bit 44.1 kHz mono WAV

## Processing Pipeline (current & planned)

```
Upload → [validate] → [save raw] → [convert to WAV] → return WAV
                                          ↑
                                   (pydub + ffmpeg)
```

Future steps that will plug in after WAV conversion:

1. Reliable F0 (pitch) tracking
2. Real pitch correction with scale/key constraints
3. High-quality Beat generation or MIDI rendering
4. Vocal + Beat mixing into a final downloadable stereo file

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

- Perform real Auto-Tune
- Change pitch
- Generate a real Beat
- Mix vocal and Beat
- Store user accounts or project history
