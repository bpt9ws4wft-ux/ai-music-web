"""FastAPI backend for AI Music Web v2.0.

This first backend version only proves the real audio-processing loop:
upload a vocal file, save it on the server, and return it unchanged.
Future versions can replace the passthrough step with real Auto-Tune,
beat generation, mixing, and export.
"""

import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="AI Music Web Backend", version="2.0.0")

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


@app.get("/health")
def health():
    """Return a simple health check result."""
    return {"status": "ok"}


@app.post("/process-vocal")
async def process_vocal(file: UploadFile = File(...)):
    """Accept a vocal file and return the same file unchanged."""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported audio type: {file.content_type}. "
                "Please upload WAV, MP3, MP4, or M4A audio."
            ),
        )

    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > MAX_SIZE_BYTES:
        size_mb = len(contents) / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File size {size_mb:.1f} MB exceeds the 25 MB limit.",
        )

    original_name = Path(file.filename or "audio").stem
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    unique_name = f"{original_name}_{uuid.uuid4().hex[:8]}{suffix}"
    save_path = UPLOAD_DIR / unique_name

    save_path.write_bytes(contents)

    return FileResponse(
        path=str(save_path),
        media_type=file.content_type,
        filename=unique_name,
        headers={
            "X-Original-Filename": file.filename or "unknown",
            "X-Processing-Status": "passthrough",
        },
    )


@app.delete("/uploads/{filename}")
def delete_upload(filename: str):
    """Delete a temporary uploaded file."""
    file_path = (UPLOAD_DIR / filename).resolve()

    if UPLOAD_DIR.resolve() not in file_path.parents:
        raise HTTPException(status_code=403, detail="Invalid upload path.")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")

    file_path.unlink()
    return {"status": "deleted", "filename": filename}
