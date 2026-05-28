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
import time
import uuid
from datetime import datetime, timezone

import numpy as np
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
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
        "X-Beat-Profile",
        "X-Profile-Id",
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

FEEDBACK_DIR = BASE_DIR / "feedback"
FEEDBACK_DIR.mkdir(exist_ok=True)
FEEDBACK_PATH = FEEDBACK_DIR / "feedback.jsonl"

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


def _analyze_beat_audio(wav_path: Path) -> dict:
    """Analyse a beat/backing-track WAV for musical features.

    Returns estimated BPM, energy level, bass level, brightness, and a
    rule-based suggested style.  Does NOT use AI — pure signal processing.
    """
    import librosa

    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    duration_s = len(y) / sr

    # ---- estimated BPM -------------------------------------------------------
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        estimated_bpm = round(float(tempo))
    except Exception:
        logging.exception("BPM detection failed")
        estimated_bpm = 0

    # ---- RMS energy ----------------------------------------------------------
    rms = float(np.sqrt(np.mean(y ** 2)))
    if rms < 0.03:
        energy_level = "low"
    elif rms < 0.12:
        energy_level = "medium"
    else:
        energy_level = "high"

    # ---- bass level (energy below 250 Hz / total) ----------------------------
    try:
        stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        bass_mask = freqs <= 250
        bass_energy = float(np.sum(stft[bass_mask]))
        total_energy = float(np.sum(stft))
        bass_ratio = bass_energy / (total_energy + 1e-9)
        if bass_ratio > 0.45:
            bass_level = "high"
        elif bass_ratio > 0.25:
            bass_level = "medium"
        else:
            bass_level = "low"
    except Exception:
        logging.exception("Bass-level detection failed")
        bass_level = "medium"

    # ---- brightness (spectral centroid) --------------------------------------
    try:
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=2048, hop_length=512)[0]
        avg_centroid = float(np.mean(centroid))
        if avg_centroid > 2800:
            brightness = "high"
        elif avg_centroid > 1400:
            brightness = "medium"
        else:
            brightness = "low"
    except Exception:
        logging.exception("Brightness detection failed")
        brightness = "medium"

    # ---- suggested style (rule-based) ----------------------------------------
    if estimated_bpm >= 105 and energy_level == "high" and brightness in ("medium", "high"):
        suggested_style = "清爽电子"
    elif estimated_bpm >= 70 and estimated_bpm <= 95 and bass_level == "high":
        suggested_style = "沉浸 Trap"
    elif estimated_bpm >= 85 and estimated_bpm <= 110 and energy_level in ("medium", "high"):
        suggested_style = "流行节奏"
    elif estimated_bpm >= 60 and estimated_bpm <= 100 and energy_level in ("low", "medium") and brightness in ("low", "medium"):
        suggested_style = "未来 R&B"
    elif bass_level == "high":
        suggested_style = "沉浸 Trap"
    elif energy_level == "high":
        suggested_style = "清爽电子"
    else:
        suggested_style = "流行节奏"

    return {
        "duration_seconds": round(duration_s, 2),
        "estimated_bpm": estimated_bpm,
        "energy_level": energy_level,
        "bass_level": bass_level,
        "brightness": brightness,
        "suggested_style": suggested_style,
        "suggested_key": "unknown",
    }


def _analyze_backing_track(wav_path: Path) -> dict:
    """Analyse a backing track WAV for musical features (v2.8 dual-input).

    Returns estimated BPM, energy level, bass level, brightness using the
    ``dark`` / ``balanced`` / ``bright`` convention, a suggested style using
    English labels (``pop`` / ``trap`` / ``rnb`` / ``electronic`` /
    ``unknown``), and a confidence score (0–100).  Pure signal processing —
    no AI.
    """
    import librosa

    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    duration_s = len(y) / sr

    # ---- estimated BPM -------------------------------------------------------
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        estimated_bpm = round(float(tempo))
    except Exception:
        logging.exception("Backing BPM detection failed")
        estimated_bpm = 0

    # ---- RMS energy ----------------------------------------------------------
    rms = float(np.sqrt(np.mean(y ** 2)))
    if rms < 0.03:
        energy_level = "low"
    elif rms < 0.12:
        energy_level = "medium"
    else:
        energy_level = "high"

    # ---- bass level (energy below 250 Hz / total) ----------------------------
    try:
        stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        bass_mask = freqs <= 250
        bass_energy = float(np.sum(stft[bass_mask]))
        total_energy = float(np.sum(stft))
        bass_ratio = bass_energy / (total_energy + 1e-9)
        if bass_ratio > 0.45:
            bass_level = "high"
        elif bass_ratio > 0.25:
            bass_level = "medium"
        else:
            bass_level = "low"
    except Exception:
        logging.exception("Backing bass-level detection failed")
        bass_level = "medium"

    # ---- brightness: dark / balanced / bright --------------------------------
    try:
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=2048, hop_length=512)[0]
        avg_centroid = float(np.mean(centroid))
        if avg_centroid > 2800:
            brightness = "bright"
        elif avg_centroid > 1400:
            brightness = "balanced"
        else:
            brightness = "dark"
    except Exception:
        logging.exception("Backing brightness detection failed")
        brightness = "balanced"

    # ---- suggested style (English labels) + confidence -----------------------
    confidence = 50
    if estimated_bpm >= 105 and energy_level == "high" and brightness in ("balanced", "bright"):
        suggested_style = "electronic"
        confidence = 78
    elif estimated_bpm >= 70 and estimated_bpm <= 95 and bass_level == "high":
        suggested_style = "trap"
        confidence = 82
    elif estimated_bpm >= 85 and estimated_bpm <= 110 and energy_level in ("medium", "high"):
        suggested_style = "pop"
        confidence = 75
    elif estimated_bpm >= 60 and estimated_bpm <= 100 and energy_level in ("low", "medium") and brightness in ("dark", "balanced"):
        suggested_style = "rnb"
        confidence = 72
    elif bass_level == "high":
        suggested_style = "trap"
        confidence = 55
    elif energy_level == "high":
        suggested_style = "electronic"
        confidence = 55
    else:
        suggested_style = "unknown"
        confidence = 40

    if estimated_bpm == 0:
        confidence = max(25, confidence - 20)

    return {
        "duration_seconds": round(duration_s, 2),
        "estimated_bpm": estimated_bpm,
        "energy_level": energy_level,
        "bass_level": bass_level,
        "brightness": brightness,
        "suggested_style": suggested_style,
        "suggested_key": "unknown",
        "confidence": confidence,
    }


# ── mainstream Auto-Tune preset library (v2.6.2) ─────────────────────────────

MAINSTREAM_AUTOTUNE_PRESETS = {
    "natural_vocal": {
        "preset_name": "natural_vocal",
        "preset_label": "自然修音",
        "retune_speed": 30,
        "correction_amount": 35,
        "humanize": 85,
        "formant_preserve": 85,
        "vibrato_preserve": 90,
        "description": "极慢修正速度，高保留人声质感与颤音。适合民谣、唱作人、不插电。",
        "suitable_for": ["民谣", "唱作人", "不插电", "Acoustic"],
    },
    "mainstream_pop": {
        "preset_name": "mainstream_pop",
        "preset_label": "主流流行",
        "retune_speed": 52,
        "correction_amount": 55,
        "humanize": 60,
        "formant_preserve": 72,
        "vibrato_preserve": 65,
        "description": "适中修正速度，平衡自然感与稳定性。适合流行、电子、舞曲。",
        "suitable_for": ["流行", "电子", "舞曲", "Pop", "EDM"],
    },
    "rnb_smooth": {
        "preset_name": "rnb_smooth",
        "preset_label": "R&B 顺滑",
        "retune_speed": 42,
        "correction_amount": 45,
        "humanize": 80,
        "formant_preserve": 82,
        "vibrato_preserve": 88,
        "description": "慢中速修正，保留转音与即兴细节。适合 R&B、Soul、慢节奏情歌。",
        "suitable_for": ["R&B", "Soul", "慢节奏情歌", "Ballad"],
    },
    "melodic_rap": {
        "preset_name": "melodic_rap",
        "preset_label": "旋律说唱",
        "retune_speed": 65,
        "correction_amount": 70,
        "humanize": 42,
        "formant_preserve": 58,
        "vibrato_preserve": 48,
        "description": "中快速修正，兼顾旋律稳定与说唱节奏感。适合旋律说唱、Hip-Hop。",
        "suitable_for": ["旋律说唱", "Hip-Hop", "Melodic Rap"],
    },
    "trap_hard": {
        "preset_name": "trap_hard",
        "preset_label": "Trap 强修",
        "retune_speed": 82,
        "correction_amount": 85,
        "humanize": 25,
        "formant_preserve": 45,
        "vibrato_preserve": 28,
        "description": "快速修正 + 高修量，颤音大幅压制。适合 Trap、Drill、重电子。",
        "suitable_for": ["Trap", "Drill", "重电子", "Hard Bass"],
    },
    "robotic_hyperpop": {
        "preset_name": "robotic_hyperpop",
        "preset_label": "电音硬修",
        "retune_speed": 96,
        "correction_amount": 96,
        "humanize": 8,
        "formant_preserve": 20,
        "vibrato_preserve": 10,
        "description": "极速修正 + 最大修量，完全电子感。适合 Hyperpop、实验电子、未来感。",
        "suitable_for": ["Hyperpop", "实验电子", "未来感", "Experimental"],
    },
}


# Map preset names to legacy processing-style modes for the pitch-correction engine.
PRESET_TO_STYLE = {
    "natural_vocal": "natural",
    "mainstream_pop": "pop",
    "rnb_smooth": "natural",   # smooth processing like natural
    "melodic_rap": "trap",
    "trap_hard": "trap",
    "robotic_hyperpop": "robotic",
}


def _match_autotune_preset(
    autotune_strength: str,
    beat_style: str,
    scale: str,
    analysis: dict,
) -> dict:
    """Select the best mainstream Auto-Tune preset based on user input + audio quality.

    Returns a dict with all preset fields plus ``confidence`` and ``preset_source``.
    Audio-quality adjustments (too_quiet / clipping_risk / minor scale) are
    applied on top of the preset base values.
    """
    strength = int(autotune_strength)
    too_quiet = analysis.get("too_quiet", False)
    clipped_risk = analysis.get("clipped_risk", False)

    # ---- rule-based matching (ordered by priority) ---------------------------
    if strength > 85:
        name = "robotic_hyperpop"
        confidence = min(100, 70 + (strength - 85))
    elif "Trap" in beat_style and strength >= 60:
        name = "trap_hard"
        confidence = 78 if strength >= 72 else 62
    elif "Trap" in beat_style:
        name = "melodic_rap"
        confidence = 68
    elif "R&B" in beat_style:
        name = "rnb_smooth"
        confidence = 82 if strength < 60 else 62
    elif strength >= 60:
        name = "melodic_rap"
        confidence = 58
    elif strength >= 30:
        name = "mainstream_pop"
        confidence = 80
    else:
        name = "natural_vocal"
        confidence = 92

    preset = MAINSTREAM_AUTOTUNE_PRESETS[name].copy()

    # ---- audio-quality adjustments -------------------------------------------
    quality_reasons = []

    if too_quiet:
        preset["retune_speed"] = max(18, preset["retune_speed"] - 10)
        preset["correction_amount"] = max(15, preset["correction_amount"] - 20)
        quality_reasons.append("输入音量过低（< −30 dBFS），已降低修正强度以避免伪影")
        confidence = max(30, confidence - 20)

    if clipped_risk:
        preset["correction_amount"] = max(15, preset["correction_amount"] - 15)
        quality_reasons.append("峰值接近 0 dBFS，存在爆音风险，已降低修正量")
        confidence = max(30, confidence - 15)

    # ---- scale-based fine-tuning ---------------------------------------------
    if scale == "minor":
        preset["humanize"] = min(100, preset["humanize"] + 8)
        preset["vibrato_preserve"] = min(100, preset["vibrato_preserve"] + 8)
        # Boost confidence for minor-scale presets like R&B, Trap
        if name in ("rnb_smooth", "trap_hard", "melodic_rap"):
            confidence = min(100, confidence + 5)

    preset["confidence"] = confidence
    preset["preset_source"] = "mainstream_rule_preset"

    # Stash quality reasons for the main reason builder.
    preset["_quality_reasons"] = quality_reasons
    preset["_source_note"] = "手动强度模式 — 以强度滑塊为主要匹配依据"

    return preset


# Map English backing-track style labels to Chinese equivalents for matching.
_BACKING_STYLE_MAP = {
    "pop": "流行节奏",
    "trap": "沉浸 Trap",
    "rnb": "未来 R&B",
    "electronic": "清爽电子",
}


def _match_autotune_preset_auto(
    beat_style: str,
    scale: str,
    analysis: dict,
    strength_preference: int = 50,
    beat_analysis: dict | None = None,
    backing: dict | None = None,
) -> dict:
    """Auto-adaptation preset matching — beat-style and audio-quality driven.

    The user's strength preference is used as a nudge, not as the primary
    matching key.  Beat style + scale determine the base preset, then audio
    quality flags and duration fine-tune the result.

    When ``beat_analysis`` is provided (from /analyze-beat) or ``backing``
    (from /analyze-backing-track), the detected features (BPM, energy, bass,
    brightness, suggested_style) are used to further refine the Auto-Tune
    parameters.
    """
    too_quiet = analysis.get("too_quiet", False)
    clipped_risk = analysis.get("clipped_risk", False)
    duration_s = analysis.get("duration_seconds", 0)
    is_short = duration_s < 5.0

    # Use backing or beat-analysis suggested_style if available.
    effective_style = beat_style
    if backing and backing.get("style"):
        mapped = _BACKING_STYLE_MAP.get(backing["style"])
        if mapped:
            effective_style = mapped
    elif beat_analysis:
        suggested = beat_analysis.get("suggested_style", "")
        if suggested:
            effective_style = suggested

    quality_reasons: list[str] = []

    # ---- Step 1: quality-first overrides -------------------------------------
    if too_quiet:
        name = "natural_vocal"
        confidence = 45
        source_note = "人声过低（< −30 dBFS），自动选择自然修音避免伪影放大"
    elif clipped_risk:
        name = "mainstream_pop"
        confidence = 50
        source_note = "爆音风险（峰值 > −0.3 dBFS），自动降低修正强度保护音质"
    else:
        # ---- Step 2: beat-style + scale matching ----------------------------
        if "Trap" in effective_style and scale == "minor":
            name = "trap_hard"
            confidence = 85
            source_note = "小调 + Trap → 强修预设（trap_hard），保留低频压迫感与暗黑色彩"
        elif "Trap" in effective_style:
            name = "melodic_rap"
            confidence = 72
            source_note = "大调 + Trap → 旋律说唱预设（melodic_rap），兼顾节奏与旋律稳定"
        elif "R&B" in effective_style and scale == "minor":
            name = "rnb_smooth"
            confidence = 88
            source_note = "小调 + R&B → R&B 顺滑预设（rnb_smooth），保留转音与即兴情绪"
        elif "R&B" in effective_style:
            name = "mainstream_pop"
            confidence = 68
            source_note = "大调 + 未来 R&B → 流行修音预设（mainstream_pop），保留自然感"
        elif "电子" in effective_style and strength_preference >= 75:
            name = "robotic_hyperpop"
            confidence = 60
            source_note = f"清爽电子 + 强度偏好较高（{strength_preference}%）→ 倾向电音硬修（robotic_hyperpop）"
        elif "电子" in effective_style and scale == "minor":
            name = "melodic_rap"
            confidence = 60
            source_note = "小调 + 清爽电子 → 旋律说唱预设（melodic_rap），保持暗色律动"
        elif "电子" in effective_style:
            name = "mainstream_pop"
            confidence = 78
            source_note = "清爽电子 → 主流流行预设（mainstream_pop），清亮通透"
        elif "流行" in effective_style and strength_preference >= 80:
            name = "robotic_hyperpop"
            confidence = 55
            source_note = f"流行节奏 + 高强度偏好（{strength_preference}%）→ 可尝试电音硬修"
        elif "流行" in effective_style:
            name = "mainstream_pop"
            confidence = 82
            source_note = "流行节奏 → 标准流行修音预设（mainstream_pop）"
        elif strength_preference >= 70:
            name = "melodic_rap"
            confidence = 55
            source_note = f"默认匹配（强度偏好 {strength_preference}%）→ 旋律说唱预设"
        elif strength_preference >= 35:
            name = "mainstream_pop"
            confidence = 70
            source_note = f"默认匹配（强度偏好 {strength_preference}%）→ 主流流行预设"
        else:
            name = "natural_vocal"
            confidence = 78
            source_note = f"默认匹配（强度偏好 {strength_preference}%）→ 自然修音预设"

    preset = MAINSTREAM_AUTOTUNE_PRESETS[name].copy()

    # ---- Step 3: audio-quality adjustments (same scaling as manual) ----------
    if too_quiet:
        preset["retune_speed"] = max(18, preset["retune_speed"] - 10)
        preset["correction_amount"] = max(15, preset["correction_amount"] - 20)
        quality_reasons.append("输入音量过低（< −30 dBFS），已降低修正强度以避免伪影")
        confidence = max(30, confidence - 20)

    if clipped_risk:
        preset["correction_amount"] = max(15, preset["correction_amount"] - 15)
        quality_reasons.append("峰值接近 0 dBFS，存在爆音风险，已降低修正量")
        confidence = max(30, confidence - 15)

    # ---- Step 4: scale-based fine-tuning -------------------------------------
    if scale == "minor":
        preset["humanize"] = min(100, preset["humanize"] + 8)
        preset["vibrato_preserve"] = min(100, preset["vibrato_preserve"] + 8)
        if name in ("rnb_smooth", "trap_hard", "melodic_rap"):
            confidence = min(100, confidence + 5)

    # ---- Step 5: beat / backing-driven refinement ----------------------------
    beat_note_parts: list[str] = []
    refine_source = backing or beat_analysis
    if refine_source and not too_quiet and not clipped_risk:
        bass_lvl = refine_source.get("bass_level", "medium")
        energy_lvl = refine_source.get("energy_level", "medium")
        bright_raw = refine_source.get("brightness", "medium")
        beat_bpm = refine_source.get("estimated_bpm", 0)

        # Normalise brightness: backing uses dark/balanced/bright,
        # beat_analysis uses low/medium/high.
        if bright_raw in ("dark", "low"):
            bright = "low"
        elif bright_raw in ("bright", "high"):
            bright = "high"
        else:
            bright = "medium"

        source_label = "伴奏" if backing else "伴奏"

        # Bass-heavy → faster retune + higher correction
        if bass_lvl == "high":
            preset["retune_speed"] = min(98, preset["retune_speed"] + 8)
            preset["correction_amount"] = min(98, preset["correction_amount"] + 6)
            beat_note_parts.append("伴奏低频强劲 → retune +8, correction +6")
        elif bass_lvl == "low":
            preset["retune_speed"] = max(20, preset["retune_speed"] - 5)
            beat_note_parts.append("伴奏低频轻柔 → retune −5")

        # High energy → can push harder
        if energy_lvl == "high":
            preset["correction_amount"] = min(98, preset["correction_amount"] + 5)
            beat_note_parts.append("伴奏能量高 → correction +5，可承受更强修音")
        elif energy_lvl == "low":
            preset["humanize"] = min(100, preset["humanize"] + 10)
            preset["vibrato_preserve"] = min(100, preset["vibrato_preserve"] + 8)
            beat_note_parts.append("伴奏能量低 → humanize +10, vibrato +8，保留自然情绪")

        # Brightness → formant adjustment
        if bright == "high":
            preset["formant_preserve"] = min(95, preset["formant_preserve"] + 8)
            beat_note_parts.append("伴奏明亮 → formant_preserve +8")
        elif bright == "low":
            preset["formant_preserve"] = max(15, preset["formant_preserve"] - 10)
            beat_note_parts.append("伴奏暗沉 → formant_preserve −10")

        # BPM integration
        if beat_bpm > 0:
            beat_note_parts.append(f"伴奏 {beat_bpm} BPM")
            if beat_bpm >= 120:
                preset["retune_speed"] = min(98, preset["retune_speed"] + 3)
            elif beat_bpm <= 80:
                preset["humanize"] = min(100, preset["humanize"] + 5)

    if beat_note_parts:
        quality_reasons.append("伴奏驱动适配：" + "；".join(beat_note_parts))
        source_note += "（伴奏特征已融入适配）"

    # ---- Step 6: short-audio penalty -----------------------------------------
    if is_short:
        confidence = max(25, confidence - 20)
        quality_reasons.append("音频较短（< 5 秒），置信度降低，建议上传完整段落以获得精准参数")

    preset["confidence"] = confidence
    preset["preset_source"] = "auto_adaptation"
    preset["_quality_reasons"] = quality_reasons
    preset["_source_note"] = source_note

    return preset


def _generate_autotune_profile(
    analysis: dict,
    autotune_strength: str,
    key: str,
    scale: str,
    beat_style: str,
    autotune_mode: str = "manual",
    beat_analysis: dict | None = None,
    backing: dict | None = None,
) -> dict:
    """Generate engine-ready Auto-Tune parameters using the mainstream preset library.

    In ``manual`` mode the slider strength is the primary matching key.
    In ``auto`` mode the system analyses audio quality, beat style, scale, and
    duration to pick the best preset autonomously — the slider is only a
    preference nudge.

    When ``beat_analysis`` (from /analyze-beat) or ``backing`` (from
    /analyze-backing-track) is provided, their features refine the
    auto-selected preset.  The profile also includes ``adaptation_inputs``
    and ``adaptation_summary`` (v2.8 dual-input) describing what drove the
    parameter selection.
    """
    strength_int = int(autotune_strength)
    is_auto = autotune_mode == "auto"

    # ---- 1. match preset -----------------------------------------------------
    if is_auto:
        preset = _match_autotune_preset_auto(
            beat_style, scale, analysis, strength_int, beat_analysis, backing,
        )
    else:
        preset = _match_autotune_preset(autotune_strength, beat_style, scale, analysis)

    retune_speed = preset["retune_speed"]
    correction_amount = preset["correction_amount"]
    humanize = preset["humanize"]
    formant_preserve = preset["formant_preserve"]
    vibrato_preserve = preset["vibrato_preserve"]
    preset_name = preset["preset_name"]
    preset_label = preset["preset_label"]
    suitable_for = preset.get("suitable_for", [])
    confidence = preset["confidence"]
    quality_reasons = preset.get("_quality_reasons", [])
    source_note = preset.get("_source_note", "")
    preset_source = preset["preset_source"]

    # ---- 2. legacy style_mode (for pitch-correction engine) ------------------
    style_mode = PRESET_TO_STYLE.get(preset_name, "natural")

    style_labels = {
        "natural": "自然", "pop": "流行", "rnb": "R&B",
        "trap": "Trap", "robotic": "电子感",
    }

    # ---- 3. vocal_quality ----------------------------------------------------
    quality_parts = []
    if analysis.get("too_quiet"):
        quality_parts.append("too_quiet")
    if analysis.get("clipped_risk"):
        quality_parts.append("clipping_risk")
    if not quality_parts:
        quality_parts.append("normal")
    vocal_quality = " | ".join(quality_parts)

    # ---- 4. reason -----------------------------------------------------------
    scale_label = "小调" if scale == "minor" else "大调"
    reasons = list(quality_reasons)

    mode_tag = "自动适配" if is_auto else "手动强度"
    reasons.append(
        f"[{mode_tag}] {source_note}"
    )
    reasons.append(
        f"匹配预设「{preset_label}」— {preset['description']} "
        f"(置信度 {confidence}%)"
    )
    reasons.append(
        f"参数：retune {retune_speed} / correction {correction_amount}% / "
        f"humanize {humanize} / formant {formant_preserve} / vibrato {vibrato_preserve}"
    )
    if scale == "minor":
        reasons.append(f"{scale_label} → humanize +8, vibrato_preserve +8 以保留情绪感")
    if "Trap" in beat_style:
        reasons.append(f"Beat 风格「{beat_style}」→ 倾向 Trap/说唱类预设")
    elif "R&B" in beat_style:
        reasons.append(f"Beat 风格「{beat_style}」→ 倾向 R&B 顺滑预设")

    # ---- 5. next_step --------------------------------------------------------
    duration_s = analysis.get("duration_seconds", 0)
    if analysis.get("too_quiet") or analysis.get("clipped_risk"):
        next_step = "音频质量存在问题，建议先改善录音条件（输入音量/爆音），再重新上传分析"
    elif duration_s < 5.0:
        next_step = "音频较短（< 5 秒），当前参数为初步判断，建议上传完整段落获得更精准的适配"
    elif preset_name == "natural_vocal":
        next_step = "人声自然稳定，参数保守。可直接进入 Beat 匹配阶段"
    elif preset_name == "mainstream_pop":
        next_step = "已生成流行修音参数（retune 适中），建议匹配流行/电子风格 Beat"
    elif preset_name in ("melodic_rap", "trap_hard"):
        next_step = "已生成强修参数（retune 快 + correction 高），建议匹配 Trap/电子 Beat"
    elif preset_name == "robotic_hyperpop":
        next_step = "已生成电子感参数（retune 极快），建议匹配未来感/电子 Beat"
    else:
        next_step = "已生成 R&B 顺滑参数，建议匹配 R&B/Soul 风格 Beat"

    # ---- 6. adaptation metadata (v2.8 dual-input) ---------------------------
    if backing:
        style_source = "伴奏分析"
        adaptation_summary = "人声 + 伴奏分析"
    else:
        style_source = "手动选择"
        adaptation_summary = "人声 + 手动曲风"

    adaptation_inputs = {
        "vocal": "已上传人声",
        "style_source": style_source,
        "backing": backing,
    }

    return {
        "mode": autotune_mode,
        "preset_name": preset_name,
        "preset_label": preset_label,
        "suitable_for": suitable_for,
        "preset_source": preset_source,
        "confidence": confidence,
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
        "adaptation_inputs": adaptation_inputs,
        "adaptation_summary": adaptation_summary,
    }



def _generate_beat_profile(
    analysis: dict,
    autotune_profile: dict,
    beat_style: str,
) -> dict:
    """Generate intelligent Beat-generation parameters from audio analysis
    and Auto-Tune profile.  v2.7 — refined BPM ranges, vocal_match, short-audio
    detection, and tighter style integration.

    This does NOT generate actual Beat audio — it produces a parameter
    blueprint that a future Beat engine can consume.
    """
    style_mode = autotune_profile.get("style_mode", "natural")
    style_mode_label = autotune_profile.get("style_mode_label", "自然")
    key = autotune_profile.get("target_key", "C")
    scale = autotune_profile.get("target_scale", "major")
    scale_label = autotune_profile.get("target_scale_label", "大调")
    too_quiet = analysis.get("too_quiet", False)
    clipped_risk = analysis.get("clipped_risk", False)
    correction_amount = autotune_profile.get("correction_amount", 40)
    duration_s = analysis.get("duration_seconds", 0)
    is_short = duration_s < 5.0

    # ---- 1. target_bpm (v2.7 per-style ranges) -------------------------------
    BPM_MAP = {
        "清爽电子": 115,    # range 105–124
        "沉浸 Trap": 78,    # range 70–88
        "流行节奏": 100,    # range 90–112
        "未来 R&B": 84,     # range 72–96
    }
    target_bpm = BPM_MAP.get(beat_style, 100)

    # ---- 2. groove_type ------------------------------------------------------
    if "Trap" in beat_style:
        groove_type = "triplet_hihat"
    elif "R&B" in beat_style:
        groove_type = "swing"
    else:
        groove_type = "straight"

    # ---- 3. drum_density (0–100) ---------------------------------------------
    # Base: robotic/trap → harder drums; natural/pop → cleaner, more restrained
    if style_mode in ("robotic", "trap"):
        drum_density = 75
    elif style_mode == "pop":
        drum_density = 60
    else:
        drum_density = 50

    if clipped_risk:
        drum_density = max(30, drum_density - 20)
    if too_quiet:
        drum_density = max(25, drum_density - 15)
    if scale == "minor" and "Trap" in beat_style:
        drum_density = min(90, drum_density + 5)
    if scale == "minor" and "R&B" in beat_style:
        drum_density = min(85, drum_density + 3)
    if correction_amount > 80:
        drum_density = min(95, drum_density + 10)

    # ---- 4. bass_intensity (0–100) -------------------------------------------
    # robotic/trap → heavy bass; natural/pop → restrained bass
    if style_mode in ("robotic", "trap"):
        bass_intensity = 80
    elif "Trap" in beat_style:
        bass_intensity = 75
    elif style_mode == "pop":
        bass_intensity = 55
    else:
        bass_intensity = 45

    if clipped_risk:
        bass_intensity = max(30, bass_intensity - 15)
    if scale == "minor" and ("Trap" in beat_style or style_mode == "trap"):
        bass_intensity = min(95, bass_intensity + 10)
    if correction_amount > 80:
        bass_intensity = min(95, bass_intensity + 8)

    # ---- 5. chord_progression ------------------------------------------------
    if scale == "major":
        if style_mode in ("robotic", "trap"):
            chords = f"I–V–vi–IV in {key} major（电子/强修和声）"
        elif style_mode == "pop":
            chords = f"I–V–vi–IV in {key} major（流行万能进行）"
        else:
            chords = f"I–IV–V–I in {key} major（经典终止式）"
    else:
        if style_mode in ("robotic", "trap"):
            chords = f"i–VI–III–VII in {key} minor（Trap/电子色彩）"
        elif "R&B" in beat_style:
            chords = f"i–iv–VII–III in {key} minor（R&B 色彩进行）"
        else:
            chords = f"i–iv–v–i in {key} minor（小调经典）"

    # ---- 6. arrangement_hint -------------------------------------------------
    if style_mode in ("robotic", "trap"):
        arrangement = "前奏8 → 主歌16(稀疏) → 副歌16(全编) → 尾奏8"
    elif style_mode == "pop":
        arrangement = "前奏4 → 主歌8 → 预副歌4 → 副歌16 → 桥段8 → 尾奏"
    else:
        arrangement = "前奏4 → 主歌16 → 副歌16 → 尾奏8"

    # ---- 7. vocal_match ------------------------------------------------------
    # Describes how well the vocal profile matches this beat style.
    match_parts = []
    if scale == "minor" and ("Trap" in beat_style or style_mode == "trap"):
        match_parts.append(f"小调人声 + {style_mode_label}修音 → 与 {beat_style} 高度契合")
    elif scale == "minor" and "R&B" in beat_style:
        match_parts.append(f"小调人声 → 自然适配 R&B 情绪色彩")
    elif scale == "major" and "电子" in beat_style:
        match_parts.append(f"大调明亮人声 → 与电子风格能量匹配")
    elif scale == "major" and "R&B" in beat_style:
        match_parts.append("大调人声搭配 R&B 风格，可尝试加入色彩和弦")
    elif correction_amount > 80:
        match_parts.append(f"强修人声（{correction_amount}%）→ 适配现代电子/Trap 风格")
    elif correction_amount < 40:
        match_parts.append("自然修音人声 → 适合保留人声细节的编曲")

    if not match_parts:
        match_parts.append(f"{scale_label}人声 + {style_mode_label}修音 → 常规适配 {beat_style}")

    if too_quiet:
        match_parts.append("注意：人声偏低，成品中人声可能被伴奏覆盖")
    if clipped_risk:
        match_parts.append("注意：人声有爆音风险，混音时需额外留 headroom")
    if is_short:
        match_parts.append("注意：音频较短（< 5 秒），当前仅适合生成草稿 Beat Profile")

    vocal_match = "；".join(match_parts)

    # ---- 8. match_reason -----------------------------------------------------
    reasons = []
    if "Trap" in beat_style:
        reasons.append(f"{beat_style} → {target_bpm} BPM + triplet hi-hat + 808 bass")
    elif "R&B" in beat_style:
        reasons.append(f"{beat_style} → {target_bpm} BPM + swing feel + 柔和鼓组")
    elif "电子" in beat_style:
        reasons.append(f"{beat_style} → {target_bpm} BPM + 电子底鼓 + 合成器铺底")
    else:
        reasons.append(f"{beat_style} → {target_bpm} BPM + 稳定节奏")

    if style_mode == "robotic":
        reasons.append("Auto-Tune 电子感 → 强鼓点 + 重低频 + 合成器主导")
    elif style_mode == "trap":
        reasons.append("Auto-Tune 强修 → Trap 鼓组 + 808 bass 推进")
    elif style_mode == "pop":
        reasons.append("Auto-Tune 流行 → 干净节奏 + 适度低频")
    else:
        reasons.append("Auto-Tune 自然 → 简约编曲，不遮盖人声细节")

    if too_quiet:
        reasons.append("人声偏低 → 建议先提升录音音量")
    if clipped_risk:
        reasons.append("人声有爆音风险 → 鼓组密度已降低")

    # ---- 9. next_step --------------------------------------------------------
    if is_short:
        next_step = "音频过短（< 5 秒），当前 Beat Profile 为草稿级——建议上传完整段落以获得精准参数"
    elif too_quiet or clipped_risk:
        next_step = "先改善录音质量，再进入 Beat 生成阶段"
    else:
        next_step = "参数已就绪，可进入 Beat 音频生成阶段（下一版本实现）"

    return {
        "target_bpm": target_bpm,
        "beat_style": beat_style,
        "groove_type": groove_type,
        "drum_density": drum_density,
        "bass_intensity": bass_intensity,
        "chord_progression": chords,
        "arrangement_hint": arrangement,
        "vocal_match": vocal_match,
        "match_reason": "；".join(reasons) if reasons else "—",
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


@app.post("/analyze-beat")
async def analyze_beat(
    file: UploadFile = File(...),
):
    """Upload a beat/backing track for musical-feature analysis.

    Returns a JSON object with estimated BPM, energy level, bass level,
    brightness, and a rule-based suggested style.  No AI — pure signal
    processing via librosa.

    The result can be passed as ``beat_analysis`` to ``/process-vocal``
    for beat-driven Auto-Tune profile generation.
    """
    # --- Validate Content-Type -------------------------------------------------
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported audio type: {file.content_type}. "
                "Please upload WAV, MP3, MP4, or M4A audio."
            ),
        )

    # --- Read & validate file contents -----------------------------------------
    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > MAX_SIZE_BYTES:
        size_mb = len(contents) / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File size {size_mb:.1f} MB exceeds the 25 MB limit.",
        )

    # --- Save raw & convert to WAV ---------------------------------------------
    suffix = Path(file.filename or "beat.wav").suffix or ".wav"
    beat_id = uuid.uuid4().hex
    raw_name = f"beat_raw_{beat_id}{suffix}"
    raw_path = UPLOAD_DIR / raw_name
    raw_path.write_bytes(contents)

    wav_name = f"beat_processed_{beat_id}.wav"
    wav_path = PROCESSED_DIR / wav_name

    try:
        _convert_to_wav(raw_path, wav_path)
    except CouldntDecodeError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Could not decode the uploaded beat. The file may be "
                   "corrupted or in an unsupported codec.",
        )
    except FileNotFoundError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is not installed or not on your PATH. "
                   "Please install ffmpeg and restart the backend.",
        )

    # --- Analyse ---------------------------------------------------------------
    try:
        result = _analyze_beat_audio(wav_path)
    except Exception:
        logging.exception("Beat analysis failed")
        raise HTTPException(
            status_code=500,
            detail="Beat analysis failed. The file may be too short or silent.",
        )

    return result


@app.post("/analyze-backing-track")
async def analyze_backing_track(
    file: UploadFile = File(...),
):
    """Upload a backing track for musical-feature analysis (v2.8 dual-input).

    Returns a JSON object with estimated BPM, energy level, bass level,
    brightness (``dark`` / ``balanced`` / ``bright``), a suggested style
    (English: ``pop`` / ``trap`` / ``rnb`` / ``electronic`` / ``unknown``),
    and a confidence score (0–100).

    The result fields can be passed as separate form fields to
    ``/process-vocal`` for backing-driven Auto-Tune profile generation.
    """
    # --- Validate Content-Type -------------------------------------------------
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported audio type: {file.content_type}. "
                "Please upload WAV, MP3, MP4, or M4A audio."
            ),
        )

    # --- Read & validate file contents -----------------------------------------
    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > MAX_SIZE_BYTES:
        size_mb = len(contents) / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File size {size_mb:.1f} MB exceeds the 25 MB limit.",
        )

    # --- Save raw & convert to WAV ---------------------------------------------
    suffix = Path(file.filename or "backing.wav").suffix or ".wav"
    track_id = uuid.uuid4().hex
    raw_name = f"backing_raw_{track_id}{suffix}"
    raw_path = UPLOAD_DIR / raw_name
    raw_path.write_bytes(contents)

    wav_name = f"backing_processed_{track_id}.wav"
    wav_path = PROCESSED_DIR / wav_name

    try:
        _convert_to_wav(raw_path, wav_path)
    except CouldntDecodeError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Could not decode the uploaded backing track. The file may "
                   "be corrupted or in an unsupported codec.",
        )
    except FileNotFoundError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is not installed or not on your PATH. "
                   "Please install ffmpeg and restart the backend.",
        )

    # --- Analyse ---------------------------------------------------------------
    try:
        result = _analyze_backing_track(wav_path)
    except Exception:
        logging.exception("Backing-track analysis failed")
        raise HTTPException(
            status_code=500,
            detail="Backing-track analysis failed. The file may be too short "
                   "or silent.",
        )

    return result


@app.post("/process-vocal")
async def process_vocal(
    file: UploadFile = File(...),
    autotune_strength: str = Form("40"),
    key: str = Form("C"),
    scale: str = Form("major"),
    beat_style: str = Form("清爽电子"),
    autotune_mode: str = Form("manual"),
    beat_analysis: str = Form(""),
    backing_style: str = Form(""),
    backing_energy: str = Form(""),
    backing_bass: str = Form(""),
    backing_brightness: str = Form(""),
):
    """Accept a vocal file, convert to WAV, apply real Auto-Tune pitch
    correction (F0 detection + pitch_shift toward target key/scale),
    and return the processed WAV with analysis and profile headers.

    ``autotune_mode`` — ``"manual"`` (default, slider-driven) or ``"auto"``
    (system selects the best preset from audio quality + beat style + scale).

    ``beat_analysis`` — optional JSON string from ``/analyze-beat``.  When
    provided in auto mode, beat features refine the Auto-Tune profile.

    ``backing_style`` / ``backing_energy`` / ``backing_bass`` /
    ``backing_brightness`` — optional fields from ``/analyze-backing-track``
    (v2.8 dual-input).  When provided, they drive Auto-Tune adaptation
    alongside vocal analysis."""
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
    beat_analysis_dict: dict | None = None
    if beat_analysis and beat_analysis.strip():
        try:
            beat_analysis_dict = json.loads(beat_analysis)
        except json.JSONDecodeError:
            logging.warning("Invalid beat_analysis JSON, ignoring")

    # Build backing dict from separate form fields (v2.8 dual-input).
    backing: dict | None = None
    if any([backing_style.strip(), backing_energy.strip(),
            backing_bass.strip(), backing_brightness.strip()]):
        backing = {
            "style": backing_style.strip() or None,
            "energy": backing_energy.strip() or None,
            "bass": backing_bass.strip() or None,
            "brightness": backing_brightness.strip() or None,
        }

    autotune_profile = _generate_autotune_profile(
        analysis, autotune_strength, key, scale, beat_style, autotune_mode,
        beat_analysis_dict, backing,
    )

    # --- 5b. Generate Beat-generation profile ---------------------------------
    beat_profile = _generate_beat_profile(analysis, autotune_profile, beat_style)

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
        "autotune_mode": autotune_mode,
        "autotune_strength": autotune_strength,
        "key": key,
        "scale": scale,
        "beat_style": beat_style,
    }
    if backing:
        settings["backing_style"] = backing.get("style")
        settings["backing_energy"] = backing.get("energy")
        settings["backing_bass"] = backing.get("bass")
        settings["backing_brightness"] = backing.get("brightness")
    headers["X-Processing-Settings"] = quote(
        json.dumps(settings, ensure_ascii=False), safe=""
    )

    headers["X-Autotune-Profile"] = quote(
        json.dumps(autotune_profile, ensure_ascii=False), safe=""
    )

    headers["X-Beat-Profile"] = quote(
        json.dumps(beat_profile, ensure_ascii=False), safe=""
    )

    headers["X-Profile-Id"] = unique_id

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


# ── feedback learning loop (v2.6.3) ──────────────────────────────────────────

VALID_FEEDBACK_LABELS = {"too_light", "good", "too_heavy", "too_fake", "more_natural"}


class FeedbackRequest(BaseModel):
    profile_id: str
    feedback: str
    note: str | None = None


@app.post("/feedback")
async def submit_feedback(body: FeedbackRequest):
    """Record user feedback for a previously generated Auto-Tune profile.

    Accepts a JSON body with ``profile_id``, ``feedback`` (English key), and
    an optional ``note``.  Saves one JSONL line to
    ``backend/feedback/feedback.jsonl``.  No audio data is stored.

    This data will power future personalised Auto-Tune recommendation models.
    """
    if body.feedback not in VALID_FEEDBACK_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid feedback label: {body.feedback!r}. "
                   f"Must be one of {sorted(VALID_FEEDBACK_LABELS)}.",
        )

    record: dict = {
        "profile_id": body.profile_id,
        "feedback": body.feedback,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    if body.note is not None:
        record["note"] = body.note

    try:
        with open(FEEDBACK_PATH, "a", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        logging.exception("Failed to write feedback record")
        raise HTTPException(
            status_code=500,
            detail=f"Could not save feedback: {exc}",
        )

    logging.info("Feedback recorded: profile_id=%s feedback=%s", body.profile_id, body.feedback)
    return {"status": "recorded", "profile_id": body.profile_id, "feedback": body.feedback}
