"""FastAPI backend for AI Music Web v2.5.

Converts any accepted audio upload into a normalised WAV file and returns
audio analysis, parameter sync, and an engine-ready Auto-Tune profile
(retune_speed / humanize / formant_preserve / vibrato_preserve) derived
from measured audio quality and user-selected parameters.

No real pitch correction yet — the profile is ready to feed a future
pyworld + formant shifter engine.
"""

import json
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError

app = FastAPI(title="AI Music Web Backend", version="2.6.0")

# Development setting: allow the local frontend to call the API.
# For production, replace "*" with your real frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Audio-Analysis",
        "X-Processing-Settings",
        "X-Autotune-Profile",
        "X-Processing-Status",
        "X-Duration-Seconds",
        "X-Sample-Rate",
        "X-Channels",
        "X-Peak-dBFS",
        "X-Average-dBFS",
        "X-Too-Quiet",
        "X-Clipped-Risk",
        "X-Original-Filename",
    ],
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


def _convert_to_wav(source_path: Path, dest_path: Path) -> dict:
    """Convert an audio file to 16-bit 44.1 kHz mono WAV.

    Returns a dict of analysis data measured from the original audio before
    conversion: duration, sample rate, channels, peak/average dBFS, and
    quality flags (too quiet / clipping risk).

    Raises ``CouldntDecodeError`` when ffmpeg cannot decode the file.
    Raises ``OSError`` when ffmpeg is not installed or not on PATH.
    """
    audio: AudioSegment = AudioSegment.from_file(source_path)

    duration_s = round(len(audio) / 1000.0, 2)
    orig_rate = audio.frame_rate
    orig_channels = audio.channels
    peak = round(audio.max_dBFS, 2)
    avg = round(audio.dBFS, 2)

    audio = audio.set_sample_width(WAV_SAMPLE_WIDTH)
    audio = audio.set_frame_rate(WAV_FRAME_RATE)
    audio = audio.set_channels(WAV_CHANNELS)
    audio.export(dest_path, format="wav")

    return {
        "duration_seconds": duration_s,
        "sample_rate": orig_rate,
        "channels": orig_channels,
        "peak_dbfs": peak,
        "average_dbfs": avg,
        "too_quiet": avg < -30.0,
        "clipped_risk": peak > -0.3,
    }


def _generate_autotune_profile(
    analysis: dict,
    autotune_strength: str,
    key: str,
    scale: str,
    beat_style: str,
) -> dict:
    """Generate engine-ready Auto-Tune parameters from audio analysis and user input.

    The profile includes real tunable values (retune_speed, humanize,
    formant_preserve, vibrato_preserve) that a future pyworld + formant
    shifter pipeline can consume directly.  Parameters are chosen to match
    the user's style intent while respecting measured audio quality.
    """
    avg = analysis["average_dbfs"]
    peak = analysis["peak_dbfs"]
    strength = int(autotune_strength)

    # ── 1. style_mode (strength band primary, scale/beat secondary) ────
    if strength < 30:
        style_mode = "natural"
    elif strength < 60:
        style_mode = "pop"
    elif strength <= 80:
        style_mode = "trap"
    else:
        style_mode = "robotic"

    style_labels = {
        "natural": "自然", "pop": "流行", "rnb": "R&B",
        "trap": "Trap", "robotic": "电子感",
    }

    # ── 2. retune_speed (0–100, higher = faster snap to target) ────────
    base_speed = {
        "natural": 28, "pop": 50, "rnb": 38, "trap": 72, "robotic": 92,
    }[style_mode]
    if avg < -35:
        retune_speed = max(18, base_speed - 12)
    elif peak > -1:
        retune_speed = max(22, base_speed - 8)
    else:
        retune_speed = base_speed

    # ── 3. correction_amount (0–100) ───────────────────────────────────
    if strength > 80:
        correction_amount = min(100, strength + 10)
    elif strength >= 60:
        correction_amount = strength + 5
    elif strength >= 30:
        correction_amount = strength
    else:
        correction_amount = strength + 10
    if avg < -35:
        correction_amount = max(5, correction_amount - 25)
    elif peak > -1:
        correction_amount = max(10, correction_amount - 15)

    # ── 4. humanize (0–100, higher = more natural timing jitter) ───────
    humanize = {
        "natural": 85, "pop": 55, "rnb": 72, "trap": 30, "robotic": 10,
    }[style_mode]
    if scale == "minor":
        humanize = min(100, humanize + 10)

    # ── 5. formant_preserve (0–100, keep original vocal character) ─────
    formant_preserve = {
        "natural": 80, "pop": 65, "rnb": 78, "trap": 45, "robotic": 15,
    }[style_mode]

    # ── 6. vibrato_preserve (0–100, keep natural vibrato) ──────────────
    vibrato_preserve = {
        "natural": 90, "pop": 55, "rnb": 88, "trap": 25, "robotic": 5,
    }[style_mode]
    if scale == "minor" and ("R&B" in beat_style or "Trap" in beat_style):
        vibrato_preserve = min(100, vibrato_preserve + 15)

    # ── 7. vocal_quality ───────────────────────────────────────────────
    quality_parts = []
    if avg < -35:
        quality_parts.append("too_quiet")
    if peak > -1:
        quality_parts.append("clipping_risk")
    if not quality_parts:
        quality_parts.append("normal")
    vocal_quality = " | ".join(quality_parts)

    # ── 8. reason ──────────────────────────────────────────────────────
    scale_label = "小调" if scale == "minor" else "大调"
    reasons = []
    if avg < -35:
        reasons.append("输入音量过低（< −35 dBFS），不建议强修，先提高录制音量")
    if peak > -1:
        reasons.append("峰值接近 0 dBFS，存在爆音风险，建议降低输入增益")

    if style_mode == "robotic":
        reasons.append(
            f"强度 {strength}% 触发电子感模式 → retune {retune_speed} / "
            f"humanize {humanize} / formant {formant_preserve}"
        )
    elif style_mode == "trap":
        reasons.append(
            f"强度 {strength}% + {scale_label}匹配强修模式 → retune {retune_speed} / "
            f"correction {correction_amount}%"
        )
    elif style_mode == "pop":
        reasons.append(
            f"强度 {strength}% + {scale_label}匹配流行模式 → retune {retune_speed} / "
            f"humanize {humanize}（兼顾稳定与自然）"
        )
    else:
        reasons.append(
            f"强度 {strength}% 匹配自然模式 → 慢修正、高保留 "
            f"(humanize {humanize} / vibrato {vibrato_preserve})"
        )

    # ── 9. next_step ───────────────────────────────────────────────────
    if avg < -35 or peak > -1:
        next_step = "音频质量存在问题，建议先改善录音条件（输入音量/爆音），再重新上传分析"
    elif style_mode == "natural":
        next_step = "人声自然稳定，参数保守。可直接进入 Beat 匹配阶段"
    elif style_mode == "pop":
        next_step = "已生成流行修音参数（retune 适中），建议匹配流行/电子风格 Beat"
    elif style_mode == "trap":
        next_step = "已生成强修参数（retune 快 + correction 高），建议匹配 Trap/电子 Beat"
    else:
        next_step = "已生成电子感参数（retune 极快），建议匹配未来感/电子 Beat"

    return {
        "target_key": key,
        "target_scale": scale,
        "target_scale_label": scale_label,
        "retune_speed": retune_speed,
        "correction_amount": correction_amount,
        "humanize": humanize,
        "formant_preserve": formant_preserve,
        "vibrato_preserve": vibrato_preserve,
        "style_mode": style_mode,
        "style_mode_label": style_labels[style_mode],
        "vocal_quality": vocal_quality,
        "reason": "；".join(reasons),
        "next_step": next_step,
    }


@app.get("/health")
def health():
    """Return a simple health check result."""
    return {"status": "ok"}


@app.post("/process-vocal")
async def process_vocal(
    file: UploadFile = File(...),
    autotune_strength: str = Form("40"),
    key: str = Form("C"),
    scale: str = Form("major"),
    beat_style: str = Form("清爽电子"),
):
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
        analysis = _convert_to_wav(raw_path, wav_path)
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
    headers = {
        "X-Processing-Status": "converted-wav",
        "X-Duration-Seconds": str(analysis["duration_seconds"]),
        "X-Sample-Rate": str(analysis["sample_rate"]),
        "X-Channels": str(analysis["channels"]),
        "X-Peak-dBFS": str(analysis["peak_dbfs"]),
        "X-Average-dBFS": str(analysis["average_dbfs"]),
        "X-Too-Quiet": str(analysis["too_quiet"]).lower(),
        "X-Clipped-Risk": str(analysis["clipped_risk"]).lower(),
    }
    original = file.filename
    if original:
        headers["X-Original-Filename"] = quote(original, safe="")

    settings = {
        "autotune_strength": autotune_strength,
        "key": key,
        "scale": scale,
        "beat_style": beat_style,
    }
    headers["X-Processing-Settings"] = quote(
        json.dumps(settings, ensure_ascii=False), safe=""
    )

    autotune_profile = _generate_autotune_profile(
        analysis, autotune_strength, key, scale, beat_style
    )
    headers["X-Autotune-Profile"] = quote(
        json.dumps(autotune_profile, ensure_ascii=False), safe=""
    )

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
