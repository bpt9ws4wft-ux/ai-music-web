"""FastAPI backend for AI Music Web v2.6.

Converts any accepted audio upload into a normalised WAV file, applies
real Auto-Tune pitch correction (librosa F0 detection + per-segment
pitch shifting toward target key/scale), and returns audio analysis,
parameter sync, and an engine-ready Auto-Tune profile.

Pitch correction is a segment-based phase-vocoder MVP — not commercial
grade, but genuinely changes pitch toward the target scale.
"""

import json
import logging
import uuid

import numpy as np
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


# ── pitch-correction helpers ────────────────────────────────────────────────

NOTE_MAP = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3,
    "E": 4, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8,
    "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11,
}

SCALE_INTERVALS = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
}


def _compute_target_notes(key: str, scale: str) -> set:
    """Build the set of MIDI note numbers that belong to *key* + *scale*.

    Covers MIDI 36 (C2) – 96 (C7), the practical vocal range.
    """
    root = NOTE_MAP.get(key.upper(), 0)
    intervals = SCALE_INTERVALS.get(scale, SCALE_INTERVALS["major"])
    notes: set[int] = set()
    for octave in range(1, 8):
        for i in intervals:
            note = root + i + octave * 12
            if 36 <= note <= 96:
                notes.add(note)
    return notes


def _pitch_correct(
    samples: np.ndarray,
    sr: int,
    target_notes: set,
    correction_amount: float,
    retune_speed: float,
    style_mode: str = "natural",
) -> np.ndarray:
    """Real pitch correction via F0 detection + per-segment phase-vocoder shift.

    Parameters
    ----------
    samples : float32 [-1, 1]
    sr : sample rate (Hz)
    target_notes : set of allowed MIDI notes
    correction_amount : 0–100  (blend factor: 0 = dry, 100 = full snap)
    retune_speed : 0–100  (higher = less smoothing → faster snap)
    style_mode : str  (natural / pop / trap / robotic)
    """
    import librosa
    from scipy.ndimage import median_filter

    # Quick guard: skip near-silent inputs.
    if np.sqrt(np.mean(samples ** 2)) < 0.002:
        logging.info("Pitch correction skipped: near-silent input")
        return samples

    # ---- F0 detection --------------------------------------------------------
    try:
        f0, voiced_flag, _ = librosa.pyin(
            samples.astype(np.float64),
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sr,
            hop_length=512,
        )
    except Exception:
        logging.exception("librosa.pyin failed — returning dry signal")
        return samples

    if f0 is None or not np.any(voiced_flag):
        logging.info("Pitch correction skipped: no voiced frames detected")
        return samples

    n_frames = len(f0)
    strength = correction_amount / 100.0          # 0.0–1.0
    HARD_TUNE = 0.70                               # threshold for quantize behaviour

    # ---- per-frame semitone correction ---------------------------------------
    correction_st = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        if voiced_flag[i] and f0[i] > 0:
            midi = librosa.hz_to_midi(f0[i])
            nearest = min(target_notes, key=lambda n: abs(n - midi))
            raw_diff = nearest - midi               # full semitone gap

            if strength >= HARD_TUNE:
                # Hard-tune region: accelerate toward full-quantize snap.
                # At 0.70 strength → 70 % of raw_diff
                # At 1.00 strength → 100 % of raw_diff (flat target, no vibrato)
                t = (strength - HARD_TUNE) / (1.0 - HARD_TUNE)  # 0 → 1
                effective = HARD_TUNE + t * (1.0 - HARD_TUNE)
                correction_st[i] = raw_diff * effective
            else:
                correction_st[i] = raw_diff * strength

    # ---- stair-step quantize for robotic mode --------------------------------
    if style_mode == "robotic" and strength >= 0.90:
        # Round corrections to 0.5-st discrete steps — characteristic
        # "stair-step" pitch contour of hard Auto-Tune.
        correction_st = np.round(correction_st * 2.0) / 2.0

    # ---- retune_speed → smoothing filter size --------------------------------
    # More aggressive mode-dependent mapping than v2.6.0.
    if style_mode == "robotic":
        filter_size = 1
    elif style_mode == "trap":
        filter_size = max(1, int(8 - retune_speed * 0.10))
    elif style_mode == "pop":
        filter_size = max(1, int(21 - retune_speed * 0.25))
    else:  # natural
        filter_size = max(1, int(25 - retune_speed * 0.32))

    if filter_size > 1:
        correction_st = median_filter(correction_st, size=filter_size)

    # ---- adaptive segment sizing ---------------------------------------------
    hop = 512
    if style_mode == "robotic":
        seg_samples = 2048          # ~46 ms — fast-tracking, hard snap
        step = seg_samples // 2     # 50 % overlap
        use_median = True
    elif style_mode == "trap":
        seg_samples = 3072          # ~70 ms — responsive
        step = seg_samples // 2
        use_median = True
    elif style_mode == "pop":
        seg_samples = 4096          # ~93 ms — balanced
        step = seg_samples // 3
        use_median = False
    else:  # natural
        seg_samples = 5120          # ~116 ms — slow, smooth
        step = seg_samples // 3
        use_median = False

    output = np.zeros(len(samples) + seg_samples, dtype=np.float64)
    weight = np.zeros_like(output)

    for start in range(0, len(samples), step):
        end = min(start + seg_samples, len(samples))
        if end - start < hop:
            continue

        chunk = samples[start:end].astype(np.float64).copy()
        chunk_len = len(chunk)

        f_start = start // hop
        f_end = min(end // hop + 1, n_frames)
        if f_end > f_start:
            seg_corrections = correction_st[f_start:f_end]
            # Median for hard-tune → snappier; mean for natural → smoother.
            semitones = float(np.median(seg_corrections) if use_median else np.mean(seg_corrections))
        else:
            semitones = 0.0

        # Wider clamp for hard-tune mode.
        max_shift = 8.0 if strength >= HARD_TUNE else 6.0
        semitones = max(-max_shift, min(max_shift, semitones))

        # Threshold: 1 cent for hard-tune, 3 cents for natural.
        threshold = 0.01 if strength >= HARD_TUNE else 0.03
        if abs(semitones) > threshold:
            try:
                shifted = librosa.effects.pitch_shift(
                    y=chunk, sr=sr, n_steps=semitones,
                )
            except Exception:
                shifted = chunk
        else:
            shifted = chunk.copy()

        # Overlap-add envelope (simple trapezoid).
        env = np.ones(chunk_len, dtype=np.float64)
        if start > 0:
            r = min(step, chunk_len)
            env[:r] = np.linspace(0.0, 1.0, r)
        if end < len(samples):
            r = min(step, chunk_len)
            env[-r:] = np.linspace(1.0, 0.0, r)

        out_len = min(chunk_len, len(output) - start)
        output[start:start + out_len] += shifted[:out_len] * env[:out_len]
        weight[start:start + out_len] += env[:out_len]

    # Normalise overlap region.
    mask = weight > 0
    output[mask] /= weight[mask]
    output = output[:len(samples)]

    # Final peak protection.
    peak = np.max(np.abs(output))
    if peak > 0.95:
        output *= 0.95 / peak

    return output.astype(np.float32)


# ── main processing entry-point ─────────────────────────────────────────────

def _apply_autotune_preview(
    audio: AudioSegment,
    profile: dict,
    analysis: dict,
) -> AudioSegment:
    """Apply real Auto-Tune pitch correction + gain staging to a WAV.

    Processing chain:
    1. Clipping protection (gain reduction in numpy)
    2. Loudness normalisation (RMS toward -17 dBFS)
    3. Pitch correction (librosa F0 + per-segment pitch_shift → target scale)
    4. Style-specific tonal shaping (80 Hz low-cut for trap/robotic)
    5. Final peak limiting
    """
    sr = audio.frame_rate
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    samples /= 32768.0              # 16-bit → [-1, 1]

    style_mode = profile.get("style_mode", "natural")
    correction_amount = float(profile.get("correction_amount", 40))
    retune_speed = float(profile.get("retune_speed", 50))
    key = profile.get("target_key", "C")
    scale = profile.get("target_scale", "major")

    # ---- 1. clipping protection (numpy) --------------------------------------
    if analysis.get("clipped_risk"):
        samples *= 0.562            # ≈ -5 dB
        logging.info("Clipping protection: -5 dB")

    # ---- 2. loudness normalisation (numpy) -----------------------------------
    rms = float(np.sqrt(np.mean(samples ** 2)))
    TARGET_RMS = 10.0 ** (-17.0 / 20.0)  # -17 dBFS linear

    if analysis.get("too_quiet") or rms < 10.0 ** (-22.0 / 20.0):
        if rms > 1e-8:
            boost = TARGET_RMS / rms
            boost = min(boost, 16.0)        # max +24 dB
            samples *= boost
            logging.info("Loudness boost: %.1f dB", 20.0 * np.log10(boost))
    elif rms > 10.0 ** (-14.0 / 20.0):
        cut = TARGET_RMS / rms
        samples *= cut
        logging.info("Loudness cut: %.1f dB", 20.0 * np.log10(cut))

    # ---- 3. pitch correction (librosa) ---------------------------------------
    target_notes = _compute_target_notes(key, scale)
    logging.info(
        "Pitch correction: key=%s scale=%s target_notes=%d mode=%s "
        "correction=%.0f%% retune_speed=%.0f",
        key, scale, len(target_notes), style_mode, correction_amount, retune_speed,
    )
    samples = _pitch_correct(
        samples, sr, target_notes, correction_amount, retune_speed, style_mode
    )

    # Convert back to pydub for the remaining pydub-native steps.
    samples_int16 = (samples * 32767.0).astype(np.int16)
    audio = AudioSegment(
        samples_int16.tobytes(),
        frame_rate=sr,
        sample_width=2,
        channels=1,
    )

    # ---- 4. style-specific tonal shaping -------------------------------------
    if style_mode in ("trap", "robotic"):
        audio = audio.high_pass_filter(80)
        logging.info("Applied low cut (80 Hz) for %s mode", style_mode)

    # ---- 5. final peak protection --------------------------------------------
    final_peak = audio.max_dBFS
    if final_peak > -0.5:
        audio = audio.apply_gain(-0.5 - final_peak)
        logging.info("Final peak limiter applied (ceiling -0.5 dBFS)")

    return audio


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
    """Accept a vocal file, convert to WAV, apply real Auto-Tune pitch
    correction (F0 detection + pitch_shift toward target key/scale),
    and return the processed WAV with analysis and profile headers."""
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

    # --- 5. Generate Auto-Tune profile --------------------------------------
    autotune_profile = _generate_autotune_profile(
        analysis, autotune_strength, key, scale, beat_style
    )

    # --- 6. Apply Auto-Tune preview effects ---------------------------------
    processing_status = "converted-wav"
    try:
        audio = AudioSegment.from_file(wav_path)
        audio = _apply_autotune_preview(audio, autotune_profile, analysis)
        audio.export(wav_path, format="wav")
        processing_status = "autotune-preview"
    except Exception:
        logging.exception(
            "Auto-Tune preview processing failed — returning normalised WAV"
        )

    # --- 7. Return processed WAV --------------------------------------------
    headers = {
        "X-Processing-Status": processing_status,
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
