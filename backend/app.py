"""FastAPI backend for AI Music Web v2.2.

This version converts any accepted audio upload into a normalised WAV file
and returns it.  Future versions can add real Auto-Tune, beat generation,
mixing, and export on top of this pipeline.
"""

import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError

app = FastAPI(title="AI Music Web Backend", version="2.2.0")

# Development setting: allow the local frontend to call the API.
# For production, replace "*" with your real frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

PROCESSED_DIR = BASE_DIR / "processed"
PROCESSED_DIR.mkdir(exist_ok=True)

ALLOWED_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
}

MAX_SIZE_BYTES = 25 * 1024 * 1024

# WAV export parameters — 16-bit PCM, 44.1 kHz mono for the vocal pipeline.
WAV_SAMPLE_WIDTH = 2      # bytes → 16-bit
WAV_FRAME_RATE = 44100    # Hz
WAV_CHANNELS = 1          # mono


def _convert_to_wav(source_path: Path, dest_path: Path) -> None:
    """Convert an audio file to 16-bit 44.1 kHz mono WAV.

    Raises ``CouldntDecodeError`` when ffmpeg cannot decode the file.
    Raises ``OSError`` when ffmpeg is not installed or not on PATH.
    """
    audio: AudioSegment = AudioSegment.from_file(source_path)
    audio = audio.set_sample_width(WAV_SAMPLE_WIDTH)
    audio = audio.set_frame_rate(WAV_FRAME_RATE)
    audio = audio.set_channels(WAV_CHANNELS)
    audio.export(dest_path, format="wav")


@app.get("/health")
def health():
    """Return a simple health check result."""
    return {"status": "ok"}


@app.post("/process-vocal")
async def process_vocal(file: UploadFile = File(...)):
    """Accept a vocal file, convert it to WAV, and return the WAV."""
    # --- 1. Validate Content-Type -------------------------------------------
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported audio type: {file.content_type}. "
                "Please upload WAV, MP3, MP4, or M4A audio."
            ),
        )

    # --- 2. Read & validate file contents -----------------------------------
    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > MAX_SIZE_BYTES:
        size_mb = len(contents) / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File size {size_mb:.1f} MB exceeds the 25 MB limit.",
        )

    # --- 3. Save raw upload -------------------------------------------------
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    unique_id = uuid.uuid4().hex
    raw_name = f"raw_{unique_id}{suffix}"
    raw_path = UPLOAD_DIR / raw_name
    raw_path.write_bytes(contents)

    # --- 4. Convert to WAV --------------------------------------------------
    wav_name = f"processed_{unique_id}.wav"
    wav_path = PROCESSED_DIR / wav_name

    try:
        _convert_to_wav(raw_path, wav_path)
    except CouldntDecodeError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Could not decode the uploaded audio. The file may be "
                   "corrupted or in an unsupported codec.",
        )
    except FileNotFoundError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is not installed or not on your PATH. "
                   "Please install ffmpeg and restart the backend.",
        )

    # --- 5. Return converted WAV --------------------------------------------
    from urllib.parse import quote

    headers = {"X-Processing-Status": "converted-wav"}
    original = file.filename
    if original:
        headers["X-Original-Filename"] = quote(original, safe="")

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=wav_name,
        headers=headers,
    )


@app.delete("/uploads/{filename}")
def delete_upload(filename: str):
    """Delete a temporary uploaded or processed file."""
    file_path = (UPLOAD_DIR / filename).resolve()
    processed_path = (PROCESSED_DIR / filename).resolve()

    if UPLOAD_DIR.resolve() not in file_path.parents and \
       PROCESSED_DIR.resolve() not in processed_path.parents:
        raise HTTPException(status_code=403, detail="Invalid path.")

    target = file_path if file_path.exists() else processed_path

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    target.unlink()
    return {"status": "deleted", "filename": filename}
