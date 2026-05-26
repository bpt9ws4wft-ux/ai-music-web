# AI Music Web Backend v2.0

This backend is the first real audio-processing layer for AI Music Web.

Current goal:

- Receive an uploaded vocal audio file
- Save it to a temporary local folder
- Return the same audio file unchanged

This is not Auto-Tune yet. It is the minimum backend loop that future real Auto-Tune, beat generation, mixing, and export features will build on.

## Start Locally

From the project root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- Health check: http://127.0.0.1:8000/health
- API docs: http://127.0.0.1:8000/docs

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Check that the backend is running |
| `POST` | `/process-vocal` | Upload an audio file and receive the same file back |
| `DELETE` | `/uploads/{filename}` | Delete a temporary uploaded file |

## Upload Rules

- Max file size: 25 MB
- Allowed audio types:
  - WAV
  - MP3
  - MP4 audio
  - M4A

## Current Limits

This version does not:

- Perform real Auto-Tune
- Change pitch
- Generate a real Beat
- Mix vocal and Beat
- Store user accounts or project history

## Next Real Product Steps

1. Convert uploads to a stable internal WAV format.
2. Add reliable pitch tracking.
3. Add real pitch correction with scale/key constraints.
4. Add high-quality Beat generation or MIDI rendering.
5. Mix vocal and Beat into a downloadable result.
