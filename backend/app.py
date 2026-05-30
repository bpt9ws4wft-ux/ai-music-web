"""FastAPI backend for AI Music Web v3.0.

Converts any accepted audio upload into a normalised WAV file, applies
real Auto-Tune pitch correction (librosa F0 detection + per-segment
pitch shifting toward target key/scale), and returns audio analysis,
parameter sync, and an engine-ready Auto-Tune profile.

v3.0: /process-vocal accepts an optional backing_track file.  When
provided the backend analyses the backing track (duration, sample rate,
peak/average dBFS, energy, low-frequency weight, brightness,
style hint) and feeds the result into the Auto-Tune profile generator —
all six core parameters (correction_amount, retune_speed, humanize,
formant_preserve, vibrato_preserve, style_mode) genuinely affect the
output audio.

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

app = FastAPI(title="AI Music Web Backend", version="3.0.0")

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
        "X-Backing-Analysis",
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

AGENT_INBOX_DIR = BASE_DIR / "agent_inbox"
AGENT_INBOX_DIR.mkdir(exist_ok=True)
AGENT_INBOX_PATH = AGENT_INBOX_DIR / "autotune_feedback_latest.md"

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
        low_frequency_weight = round(bass_ratio * 100, 1)
        if bass_ratio > 0.45:
            bass_level = "high"
        elif bass_ratio > 0.25:
            bass_level = "medium"
        else:
            bass_level = "low"
    except Exception:
        logging.exception("Backing bass-level detection failed")
        bass_level = "medium"
        low_frequency_weight = 50.0

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
        "low_frequency_weight": low_frequency_weight,
        "brightness": brightness,
        "suggested_style": suggested_style,
        "suggested_key": "unknown",
        "confidence": confidence,
    }


# ── mainstream Auto-Tune preset library (v3.2) ──────────────────────────────
# Each preset models a real-world Auto-Tune "sound" — not just parameter
# values, but a curated combination of retune curve, correction depth,
# formant blend, vibrato handling, and pitch-tracking aggression.
#
# retune_speed : 0–100  (internal: higher = faster snap → lower ms)
# retune_ms_equivalent : the approximate Antares-style "Retune Speed" in ms
#   (lower ms = faster correction; 0 ms = instant, 200 ms = very slow).
# flex_tune_like : informal description of the equivalent Antares Flex Tune
#   setting (0 % = hard snap, 100 % = natural glide).
# pitch_tracking : "relaxed" | "medium" | "fast" | "instant" —
#   describes how aggressively the detector follows pitch changes.

MAINSTREAM_AUTOTUNE_PRESETS = {
    "natural_pop": {
        "preset_name": "natural_pop",
        "preset_label": "自然流行",
        "retune_speed": 24,
        "retune_ms_equivalent": 90,
        "correction_amount": 28,
        "humanize": 92,
        "flex_tune_like": "Flex Tune ~75 % — gentle nudges, never snaps",
        "formant_preserve": 90,
        "vibrato_preserve": 92,
        "pitch_tracking": "relaxed",
        "best_for": "民谣、唱作人、不插电、播客人声",
        "risk": "低 — 几乎无修音痕迹，但跑调 > 50 cents 的音符不会被完全纠正",
        "description": "最轻量修正，保留全部人声细节。retune 约 90 ms，柔和平滑。",
        "suitable_for": ["民谣", "唱作人", "不插电", "Acoustic", "播客"],
    },
    "modern_pop": {
        "preset_name": "modern_pop",
        "preset_label": "现代流行",
        "retune_speed": 58,
        "retune_ms_equivalent": 26,
        "correction_amount": 60,
        "humanize": 55,
        "flex_tune_like": "Flex Tune ~35 % — balanced correction with natural decay",
        "formant_preserve": 70,
        "vibrato_preserve": 62,
        "pitch_tracking": "medium",
        "best_for": "流行、电子流行、K-Pop、舞曲、Top 40",
        "risk": "中低 — 标准现代流行修音，大多数商业人声适用",
        "description": "现代流行唱片标准修音 — 稳定、明亮、有控制但仍保留人声自然感。",
        "suitable_for": ["流行", "电子", "舞曲", "Pop", "EDM", "K-Pop"],
    },
    "melodic_trap": {
        "preset_name": "melodic_trap",
        "preset_label": "旋律 Trap",
        "retune_speed": 78,
        "retune_ms_equivalent": 8,
        "correction_amount": 78,
        "humanize": 30,
        "flex_tune_like": "Flex Tune ~15 % — fast snap, tight pitch lock",
        "formant_preserve": 48,
        "vibrato_preserve": 35,
        "pitch_tracking": "fast",
        "best_for": "旋律说唱、Trap、Drill、Hip-Hop",
        "risk": "中 — 快速修正 + 80Hz 低切可能让低频区人声变薄；确保输入人声本身音高稳定",
        "description": "紧凑快速的音高锁定 + 适度电子感。保留说唱节奏切分，同时稳定旋律线。",
        "suitable_for": ["旋律说唱", "Hip-Hop", "Trap", "Drill", "Melodic Rap"],
    },
    "trap_polished": {
        "preset_name": "trap_polished",
        "preset_label": "精修 Trap",
        "retune_speed": 86,
        "retune_ms_equivalent": 5,
        "correction_amount": 88,
        "humanize": 22,
        "flex_tune_like": "Flex Tune ~10 % — fast snap with smoother decay than hyperpop",
        "formant_preserve": 40,
        "vibrato_preserve": 23,
        "pitch_tracking": "fast",
        "best_for": "旋律说唱、强修音但保留质感的人声",
        "risk": "中高 — 输入质量差时可能产生伪影；依赖 voiced-only 保护和 soft limiter 防破音",
        "description": "melodic_trap 和 hyperpop 之间的精修方案：比 melodic_trap 更强，比 hyperpop 更顺滑不刺耳。",
        "suitable_for": ["旋律说唱", "Trap", "精修人声"],
    },
    "hyperpop": {
        "preset_name": "hyperpop",
        "preset_label": "Hyperpop 创意",
        "retune_speed": 99,
        "retune_ms_equivalent": 0,
        "correction_amount": 98,
        "humanize": 2,
        "flex_tune_like": "Flex Tune 0 % + Retune 0 ms — instant quantize, total lock",
        "formant_preserve": 10,
        "vibrato_preserve": 5,
        "pitch_tracking": "instant",
        "best_for": "Hyperpop、实验电子、未来感、创意人声效果",
        "risk": "高 — 这是创意效果，不是传统'修音'。声场扁平化、共振峰偏移、完全电子化。适合作为特殊音色使用。",
        "description": "极速离散量化 + 零自然人声残留 + Tanh 饱和 → 完全电子音色。",
        "suitable_for": ["Hyperpop", "实验电子", "未来感", "Experimental", "Creative FX"],
    },
    "emotional_rnb": {
        "preset_name": "emotional_rnb",
        "preset_label": "情绪 R&B",
        "retune_speed": 36,
        "retune_ms_equivalent": 58,
        "correction_amount": 42,
        "humanize": 84,
        "flex_tune_like": "Flex Tune ~65 % — gentle, preserves runs and melisma",
        "formant_preserve": 84,
        "vibrato_preserve": 90,
        "pitch_tracking": "relaxed",
        "best_for": "R&B、Soul、慢速情歌、转音密集型人声",
        "risk": "低 — 转音和滑音被保留，但跑调 > 50 cents 的音符不会完全纠正",
        "description": "中慢速修正，高保留转音、滑音与即兴细节。R&B 灵魂乐首选。",
        "suitable_for": ["R&B", "Soul", "慢节奏情歌", "Ballad"],
    },
    "live_tracking": {
        "preset_name": "live_tracking",
        "preset_label": "现场录音",
        "retune_speed": 14,
        "retune_ms_equivalent": 130,
        "correction_amount": 18,
        "humanize": 98,
        "flex_tune_like": "Flex Tune ~90 % — barely-there correction, zero artifacts",
        "formant_preserve": 96,
        "vibrato_preserve": 98,
        "pitch_tracking": "relaxed",
        "best_for": "现场录音、一镜到底、古典/美声、播客、采访",
        "risk": "极低 — 最保守参数，几乎不改变原始音色，零伪影风险",
        "description": "最保守修音 — 几乎不可感知，仅极轻微音高微调。适合不能'听出修音'的场景。",
        "suitable_for": ["现场录音", "古典", "美声", "播客", "采访"],
    },
}


def _retune_speed_to_ms(retune_speed: int) -> int:
    """Convert internal retune_speed (0-100, higher=faster) to approximate
    Antares-style Retune Speed in milliseconds (lower=faster).

    Uses piecewise linear interpolation anchored at the 6 curated preset
    values so the heuristic is consistent with the preset library.
    """
    if retune_speed >= 99:
        return 0
    if retune_speed >= 78:
        return round(0 + (99 - retune_speed) * 8 / 21)     # anchor: rs=99→0ms
    if retune_speed >= 58:
        return round(8 + (78 - retune_speed) * 18 / 20)    # anchor: rs=78→8ms
    if retune_speed >= 36:
        return round(26 + (58 - retune_speed) * 32 / 22)   # anchor: rs=58→26ms
    if retune_speed >= 24:
        return round(58 + (36 - retune_speed) * 32 / 12)   # anchor: rs=36→58ms
    if retune_speed >= 14:
        return round(90 + (24 - retune_speed) * 40 / 10)   # anchor: rs=24→90ms
    return max(80, round(130 + (14 - retune_speed) * 70 / 14))  # anchor: rs=14→130ms


# Map preset names to legacy processing-style modes for the pitch-correction engine.
PRESET_TO_STYLE = {
    "natural_pop": "natural",
    "modern_pop": "pop",
    "melodic_trap": "trap",
    "trap_polished": "trap",
    "hyperpop": "robotic",
    "emotional_rnb": "rnb",
    "live_tracking": "natural",
}


# ── v3.3 quality-check calibration profiles ─────────────────────────────────

# Legacy extreme-parameter profiles used for engine calibration.
# NOTE: /quality-check now uses MAINSTREAM_AUTOTUNE_PRESETS directly;
# these are kept for reference and backward-compatible parameter lookup.
# These are DELIBERATELY more extreme than the mainstream presets to
# guarantee audible contrast when fed through the same engine.

QUALITY_CHECK_PROFILES = {
    "natural": {
        "preset_name": "qc_natural",
        "preset_label": "轻修音（自然）",
        "style_mode": "natural",
        "retune_speed": 15,
        "correction_amount": 15,
        "humanize": 100,
        "formant_preserve": 100,
        "vibrato_preserve": 100,
        "description": "几乎不修音 — 最高自然人声保留，最大颤音/共振峰/时值抖动",
        "expected_character": "听感与原声几乎一致，仅极轻微的音高微调。适合验证 baseline。",
    },
    "pop": {
        "preset_name": "qc_pop",
        "preset_label": "中等修音（流行）",
        "style_mode": "pop",
        "retune_speed": 58,
        "correction_amount": 62,
        "humanize": 48,
        "formant_preserve": 62,
        "vibrato_preserve": 58,
        "description": "明显修音但不失真 — 中速音高修正，平衡自然感与稳定性",
        "expected_character": "明显可感知的音高校正，但仍有人声自然感。类似主流流行唱片效果。",
    },
    "robotic": {
        "preset_name": "qc_robotic",
        "preset_label": "强电音感（电子）",
        "style_mode": "robotic",
        "retune_speed": 100,
        "correction_amount": 100,
        "humanize": 0,
        "formant_preserve": 0,
        "vibrato_preserve": 0,
        "description": "极速修正 + 100% 修量 + 零自然人声残留 + 离散量化 + 二次残差消除",
        "expected_character": "强烈电子音色，音高瞬间跳变，无颤音，无自然人声质感。类似 T-Pain / Hyperpop 效果。",
    },
}


def _build_qc_profile(qc_def: dict, key: str, scale: str, analysis: dict) -> dict:
    """Build a minimal engine-ready profile dict from a quality-check preset
    definition.  Contains exactly the fields that ``_apply_autotune_preview``
    reads at runtime."""
    scale_label = "小调" if scale == "minor" else "大调"
    return {
        "preset_name": qc_def["preset_name"],
        "preset_label": qc_def["preset_label"],
        "style_mode": qc_def["style_mode"],
        "retune_speed": qc_def["retune_speed"],
        "correction_amount": qc_def["correction_amount"],
        "humanize": qc_def["humanize"],
        "formant_preserve": qc_def["formant_preserve"],
        "vibrato_preserve": qc_def["vibrato_preserve"],
        "target_key": key,
        "target_scale": scale,
        "target_scale_label": scale_label,
        "confidence": 100,  # fixed — these are reference profiles
        "preset_source": "quality_check",
        "vocal_quality": "normal",
    }


def _match_autotune_preset(
    autotune_strength: str,
    beat_style: str,
    scale: str,
    analysis: dict,
) -> dict:
    """Select the best v3.2 mainstream preset based on slider strength + context.

    Returns a dict with all preset fields plus ``confidence`` and ``preset_source``.
    Audio-quality and scale-based micro-tuning are applied on top.
    """
    strength = int(autotune_strength)
    too_quiet = analysis.get("too_quiet", False)
    clipped_risk = analysis.get("clipped_risk", False)

    # ---- Step 1: quality-first overrides -----------------------------------
    if too_quiet:
        name = "live_tracking"
        confidence = 40
        base_reason = "输入音量过低 → 自动选择最保守预设（live_tracking）避免伪影放大"
    elif clipped_risk:
        name = "natural_pop"
        confidence = 48
        base_reason = "爆音风险 → 自动降低修正强度（natural_pop）保护音质"
    elif strength > 88:
        name = "hyperpop"
        confidence = min(100, 68 + (strength - 88))
        base_reason = f"强度极高（{strength}%）→ Hyperpop 创意效果"
    elif strength > 75:
        name = "melodic_trap"
        confidence = 72 if strength >= 82 else 62
        base_reason = f"高强度偏好（{strength}%）→ 旋律 Trap 快速修正"
    elif "Trap" in beat_style and strength >= 55:
        name = "melodic_trap"
        confidence = 78
        base_reason = "Trap 曲风 + 中高强度 → 旋律 Trap 预设"
    elif "Trap" in beat_style:
        name = "melodic_trap"
        confidence = 65
        base_reason = "Trap 曲风 → 旋律 Trap（保守剂量）"
    elif "R&B" in beat_style and scale == "minor":
        name = "emotional_rnb"
        confidence = 88
        base_reason = "小调 + R&B 曲风 → 情绪 R&B 预设"
    elif "R&B" in beat_style:
        name = "emotional_rnb"
        confidence = 80
        base_reason = "R&B 曲风 → 情绪 R&B 预设"
    elif strength >= 35:
        name = "modern_pop"
        confidence = 82
        base_reason = f"中等强度（{strength}%）→ 现代流行预设"
    elif strength >= 15:
        name = "natural_pop"
        confidence = 88
        base_reason = f"低强度（{strength}%）→ 自然流行预设"
    else:
        name = "live_tracking"
        confidence = 92
        base_reason = f"极低强度（{strength}%）→ 现场录音预设"

    preset = MAINSTREAM_AUTOTUNE_PRESETS[name].copy()

    # ---- Step 2: audio-quality micro-tuning --------------------------------
    quality_reasons: list[str] = []

    if too_quiet:
        preset["retune_speed"] = max(12, preset["retune_speed"] - 8)
        preset["correction_amount"] = max(10, preset["correction_amount"] - 18)
        quality_reasons.append("输入音量过低（< −30 dBFS），已降低修正强度以避免伪影")
        confidence = max(25, confidence - 20)

    if clipped_risk:
        preset["correction_amount"] = max(10, preset["correction_amount"] - 12)
        preset["formant_preserve"] = min(98, preset["formant_preserve"] + 15)
        quality_reasons.append("峰值接近 0 dBFS，存在爆音风险，已降低修正量并提高干声比例")
        confidence = max(25, confidence - 15)

    # ---- Step 3: scale-based fine-tuning -----------------------------------
    if scale == "minor":
        preset["humanize"] = min(100, preset["humanize"] + 8)
        preset["vibrato_preserve"] = min(100, preset["vibrato_preserve"] + 8)
        if name in ("emotional_rnb", "melodic_trap"):
            confidence = min(100, confidence + 5)

    # ---- Step 4: ensure retune_ms matches final retune_speed ---------------
    if "retune_ms_equivalent" not in preset:
        preset["retune_ms_equivalent"] = _retune_speed_to_ms(preset["retune_speed"])

    preset["confidence"] = confidence
    preset["preset_source"] = "mainstream_rule_preset"
    preset["_quality_reasons"] = quality_reasons
    preset["_source_note"] = f"手动强度模式 — {base_reason}"

    return preset


# Map English backing-track style labels to Chinese equivalents for matching.
_BACKING_STYLE_MAP = {
    "pop": "流行节奏",
    "trap": "沉浸 Trap",
    "rnb": "未来 R&B",
    "electronic": "清爽电子",
}


def _load_autotune_feedback_preferences() -> dict[str, dict]:
    """Load accumulated v3.4 A/B listening feedback and compute per-preset scores.

    Returns ``{preset_name: {score, count, best_count}}`` keyed by preset name.
    Empty dict if no feedback file exists or it cannot be parsed.

    Scoring rules:
    - label=best  or  rating≥5  → +3
    - label=good / natural  or  rating≥4  → +1
    - label=too_fake / too_heavy / harsh → −2
    - label=too_light → −1
    """
    if not QUALITY_FEEDBACK_PATH.exists():
        return {}

    scores: dict[str, dict] = {}
    try:
        with open(QUALITY_FEEDBACK_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                pname = rec.get("preset_name", "")
                if not pname or pname not in MAINSTREAM_AUTOTUNE_PRESETS:
                    continue

                if pname not in scores:
                    scores[pname] = {"score": 0, "count": 0, "best_count": 0,
                                     "too_light_count": 0, "too_fake_harsh_count": 0}

                label = rec.get("label", "")
                rating = rec.get("rating") or 0

                if label == "best" or (isinstance(rating, (int, float)) and rating >= 5):
                    scores[pname]["score"] += 3
                    scores[pname]["best_count"] += 1
                elif label in ("good", "natural") or (isinstance(rating, (int, float)) and rating >= 4):
                    scores[pname]["score"] += 1
                elif label in ("too_fake", "too_heavy", "harsh"):
                    scores[pname]["score"] -= 2
                    scores[pname]["too_fake_harsh_count"] += 1
                elif label == "too_light":
                    scores[pname]["score"] -= 1
                    scores[pname]["too_light_count"] += 1

                scores[pname]["count"] += 1
    except Exception:
        logging.exception("Failed to load feedback preferences")
        return {}

    return scores


def _match_autotune_preset_auto(
    beat_style: str,
    scale: str,
    analysis: dict,
    strength_preference: int = 50,
    beat_analysis: dict | None = None,
    backing: dict | None = None,
) -> dict:
    """v3.5 auto-adaptation — vocal-quality + backing-style + scale + feedback driven.

    Decision hierarchy:
    1. Audio-quality emergency overrides (too_quiet / clipped_risk)
    2. Backing rough_style_hint drives genre → preset mapping
    3. Scale (major/minor) + strength_preference fine-tune
    4. Beat/backing features (BPM, energy, bass, brightness) micro-adjust
    5. Short-audio penalty
    """
    too_quiet = analysis.get("too_quiet", False)
    clipped_risk = analysis.get("clipped_risk", False)
    duration_s = analysis.get("duration_seconds", 0)
    is_short = duration_s < 5.0

    # Resolve effective backing style hint.
    effective_hint: str | None = None
    if backing and backing.get("style") and backing["style"] != "unknown":
        effective_hint = backing["style"]  # pop / trap / rnb / electronic
    elif backing and backing.get("rough_style_hint"):
        effective_hint = backing["rough_style_hint"]

    quality_reasons: list[str] = []
    quality_override_active = False  # v3.5: guard — feedback never overrides safety

    # ---- Step 1: quality-first overrides -----------------------------------
    if too_quiet:
        name = "live_tracking"
        confidence = 35
        source_note = "人声过低（< −30 dBFS），自动选择现场录音预设避免伪影放大"
        quality_override_active = True
    elif clipped_risk:
        name = "natural_pop"
        confidence = 45
        source_note = "爆音风险（峰值 > −0.3 dBFS），自动降低修正强度保护音质"
        quality_override_active = True
    elif effective_hint == "trap":
        if scale == "minor":
            name = "melodic_trap"
            confidence = 88
            source_note = "伴奏识别为 Trap + 小调人声 → 旋律 Trap 预设（melodic_trap），紧凑低音适配"
        else:
            name = "melodic_trap"
            confidence = 75
            source_note = "伴奏识别为 Trap + 大调人声 → 旋律 Trap 预设，保留旋律稳定"
    elif effective_hint == "rnb":
        name = "emotional_rnb"
        confidence = 90 if scale == "minor" else 78
        source_note = (
            "伴奏识别为 R&B → 情绪 R&B 预设（emotional_rnb），"
            "保留转音与滑音"
        )
    elif effective_hint == "electronic":
        if strength_preference >= 80:
            name = "hyperpop"
            confidence = 62
            source_note = f"伴奏识别为电子 + 高偏好（{strength_preference}%）→ Hyperpop 创意效果"
        elif scale == "minor":
            name = "melodic_trap"
            confidence = 62
            source_note = "伴奏识别为电子 + 小调 → 旋律 Trap（暗色电子律动）"
        else:
            name = "modern_pop"
            confidence = 80
            source_note = "伴奏识别为电子 → 现代流行预设（modern_pop），清亮通透"
    elif effective_hint == "pop":
        name = "modern_pop"
        confidence = 84
        source_note = "伴奏识别为流行 → 现代流行预设（modern_pop），标准唱片修音"
    elif "Trap" in beat_style:
        name = "melodic_trap"
        confidence = 72
        source_note = "手动曲风「Trap」→ 旋律 Trap 预设"
    elif "R&B" in beat_style:
        name = "emotional_rnb"
        confidence = 82
        source_note = "手动曲风「R&B」→ 情绪 R&B 预设"
    elif strength_preference >= 80:
        name = "hyperpop"
        confidence = 55
        source_note = f"高强度偏好（{strength_preference}%）→ Hyperpop 创意效果"
    elif strength_preference >= 52:
        name = "modern_pop"
        confidence = 75
        source_note = f"中等偏好（{strength_preference}%）→ 现代流行预设"
    elif strength_preference >= 22:
        name = "natural_pop"
        confidence = 82
        source_note = f"中低偏好（{strength_preference}%）→ 自然流行预设"
    else:
        name = "live_tracking"
        confidence = 88
        source_note = f"极低偏好（{strength_preference}%）→ 现场录音预设"

    # ---- v3.5: feedback-driven nudge (auto mode only) ------------------------
    # Only applied when quality overrides are NOT active (too_quiet / clipped_risk
    # already bail out to safe presets above).  Feedback can nudge within the same
    # "intensity group" but never swaps natural → robotic or vice versa.
    preferences = _load_autotune_feedback_preferences()
    feedback_score = 0
    feedback_adjustment = ""
    personalization_source = "无历史反馈数据"
    nudge_applied = False

    if preferences and not quality_override_active:
        # v3.5: feedback nudge only fires when audio quality is normal.
        # too_quiet / clipped_risk already selected safe presets — do NOT override.
        # Adjacent-intensity groups: presets that can be swapped based on feedback.
        FEEDBACK_NUDGE_GROUPS = [
            ["live_tracking", "natural_pop"],
            ["natural_pop", "modern_pop", "emotional_rnb"],
            ["modern_pop", "emotional_rnb", "melodic_trap", "trap_polished"],
            ["melodic_trap", "trap_polished", "hyperpop"],
        ]
        current_group = None
        for grp in FEEDBACK_NUDGE_GROUPS:
            if name in grp:
                current_group = grp
                break

        if current_group:
            candidates = [
                (n, preferences.get(n, {}).get("score", 0),
                 preferences.get(n, {}).get("count", 0),
                 preferences.get(n, {}).get("best_count", 0))
                for n in current_group
                if n in MAINSTREAM_AUTOTUNE_PRESETS
            ]
            candidates.sort(key=lambda x: x[1], reverse=True)

            best_fb_name, best_fb_score, best_fb_count, _ = candidates[0]
            current_fb = preferences.get(name, {})
            current_score = current_fb.get("score", 0)
            current_count = current_fb.get("count", 0)

            # Nudge threshold: another preset in same group has ≥ 3 more points.
            if best_fb_name != name and best_fb_score >= current_score + 3:
                nudge_applied = True
                old_label = MAINSTREAM_AUTOTUNE_PRESETS[name]["preset_label"]
                new_label = MAINSTREAM_AUTOTUNE_PRESETS[best_fb_name]["preset_label"]
                feedback_score = best_fb_score
                feedback_adjustment = (
                    f"反馈偏好：'{old_label}' → '{new_label}'"
                    f"（历史评分 {current_score} → {best_fb_score}）"
                )
                personalization_source = (
                    f"基于 {best_fb_count} 条历史反馈"
                )
                name = best_fb_name
                confidence = min(100, confidence + 3)  # slight confidence boost
                source_note += f"（反馈偏好已调整）"
            else:
                feedback_score = current_score
                if current_count > 0:
                    feedback_adjustment = (
                        f"当前预设 '{MAINSTREAM_AUTOTUNE_PRESETS[name]['preset_label']}' "
                        f"反馈评分 {current_score}，保持选择"
                    )
                    personalization_source = f"基于 {current_count} 条历史反馈"
                else:
                    personalization_source = "无针对此预设的历史反馈"

    # ---- v3.7: feedback-gap detection ---------------------------------------
    # Pattern: melodic_trap labelled "too_light" AND hyperpop labelled
    # "too_fake"/"harsh" → user wants something between them → trap_polished.
    gap_detected = False
    if preferences and not quality_override_active:
        mt_pref = preferences.get("melodic_trap", {})
        hp_pref = preferences.get("hyperpop", {})
        mt_too_light = mt_pref.get("too_light_count", 0) > 0
        hp_too_fake = hp_pref.get("too_fake_harsh_count", 0) > 0

        if mt_too_light and hp_too_fake and name in ("melodic_trap", "hyperpop"):
            gap_detected = True
            old_name = name
            name = "trap_polished"
            feedback_adjustment = (
                f"反馈缺口检测：melodic_trap 被标记为'太轻'、hyperpop 被标记为'太假/刺耳'，"
                f"推荐中间方案 trap_polished（精修 Trap）"
            )
            personalization_source = (
                f"基于 {mt_pref.get('count',0)+hp_pref.get('count',0)} 条反馈发现缺口"
            )
            confidence = min(100, confidence + 5)
            source_note += "（反馈缺口已填补 → trap_polished）"

    preset = MAINSTREAM_AUTOTUNE_PRESETS[name].copy()
    if nudge_applied:
        preset["_feedback_nudge"] = True
    if gap_detected:
        preset["_gap_detected"] = True
    preset["_feedback_score"] = feedback_score
    preset["_feedback_adjustment"] = feedback_adjustment
    preset["_personalization_source"] = personalization_source

    # ---- Step 2: audio-quality micro-tuning (applies unless overridden) ----
    if too_quiet:
        preset["retune_speed"] = max(12, preset["retune_speed"] - 8)
        preset["correction_amount"] = max(10, preset["correction_amount"] - 18)
        quality_reasons.append("输入音量过低（< −30 dBFS），已降低修正强度以避免伪影")
        confidence = max(25, confidence - 20)

    if clipped_risk:
        preset["correction_amount"] = max(10, preset["correction_amount"] - 12)
        preset["formant_preserve"] = min(98, preset["formant_preserve"] + 15)
        quality_reasons.append("峰值接近 0 dBFS，存在爆音风险，已降低修正量并提高干声比例")
        confidence = max(25, confidence - 15)

    # ---- Step 3: scale-based fine-tuning -----------------------------------
    if scale == "minor":
        preset["humanize"] = min(100, preset["humanize"] + 8)
        preset["vibrato_preserve"] = min(100, preset["vibrato_preserve"] + 8)
        if name in ("emotional_rnb", "melodic_trap"):
            confidence = min(100, confidence + 5)

    # ---- Step 4: beat / backing-driven parameter refinement ----------------
    beat_note_parts: list[str] = []
    refine_source = backing or beat_analysis
    if refine_source and not too_quiet and not clipped_risk:
        bass_lvl = refine_source.get("bass_level", "medium")
        energy_lvl = refine_source.get("energy_level", "medium")
        bright_raw = refine_source.get("brightness", "medium")
        beat_bpm = refine_source.get("estimated_bpm", 0)

        # Normalise brightness label.
        if bright_raw in ("dark", "low"):
            bright = "low"
        elif bright_raw in ("bright", "high"):
            bright = "high"
        else:
            bright = "medium"

        # Bass-heavy → faster retune + higher correction
        if bass_lvl == "high":
            preset["retune_speed"] = min(98, preset["retune_speed"] + 8)
            preset["correction_amount"] = min(98, preset["correction_amount"] + 6)
            beat_note_parts.append("低频强劲 → retune +8, correction +6")
        elif bass_lvl == "low":
            preset["retune_speed"] = max(10, preset["retune_speed"] - 5)
            beat_note_parts.append("低频轻柔 → retune −5")

        # Energy-driven nudges
        if energy_lvl == "high":
            preset["correction_amount"] = min(98, preset["correction_amount"] + 5)
            beat_note_parts.append("能量高 → correction +5")
        elif energy_lvl == "low":
            preset["humanize"] = min(100, preset["humanize"] + 10)
            preset["vibrato_preserve"] = min(100, preset["vibrato_preserve"] + 8)
            beat_note_parts.append("能量低 → humanize +10, vibrato +8")

        # Brightness → formant
        if bright == "high":
            preset["formant_preserve"] = min(98, preset["formant_preserve"] + 8)
            beat_note_parts.append("明亮 → formant_preserve +8")
        elif bright == "low":
            preset["formant_preserve"] = max(10, preset["formant_preserve"] - 10)
            beat_note_parts.append("暗沉 → formant_preserve −10")

        # BPM
        if beat_bpm > 0:
            beat_note_parts.append(f"{beat_bpm} BPM")
            if beat_bpm >= 120:
                preset["retune_speed"] = min(98, preset["retune_speed"] + 3)
            elif beat_bpm <= 75:
                preset["humanize"] = min(100, preset["humanize"] + 5)

    if beat_note_parts:
        quality_reasons.append("伴奏驱动适配：" + "；".join(beat_note_parts))
        source_note += "（伴奏特征已融入适配）"

    # ---- v3.8: feedback-driven parameter tuning (Step 4b) --------------------
    # Before snapshot for X-Autotune-Profile reporting.
    before_params = {
        "correction_amount": preset["correction_amount"],
        "retune_speed": preset["retune_speed"],
        "retune_ms_equivalent": preset.get("retune_ms_equivalent",
                                           _retune_speed_to_ms(preset["retune_speed"])),
        "humanize": preset["humanize"],
        "formant_preserve": preset["formant_preserve"],
        "vibrato_preserve": preset["vibrato_preserve"],
    }
    tuning_applied = False
    tuning_reasons: list[str] = []

    if preferences and not quality_override_active:
        current_pref = preferences.get(name, {})
        too_light_n = current_pref.get("too_light_count", 0)
        too_fake_n = current_pref.get("too_fake_harsh_count", 0)

        # ── Gap sub-case (checked first, falls through if gap not present) ──
        if name in ("melodic_trap", "trap_polished"):
            mt_pref = preferences.get("melodic_trap", {})
            tp_pref = preferences.get("trap_polished", {})
            if mt_pref.get("too_light_count", 0) > 0 and tp_pref.get("too_fake_harsh_count", 0) > 0:
                tuning_applied = True
                preset["correction_amount"] = 82
                preset["retune_speed"] = 84
                preset["humanize"] = 28
                preset["formant_preserve"] = 48
                preset["vibrato_preserve"] = 32
                preset["retune_ms_equivalent"] = _retune_speed_to_ms(preset["retune_speed"])
                tuning_reasons.append(
                    "缺口微调：melodic_trap 太轻 + trap_polished 太假 "
                    "→ correction=82%, retune≈6ms, humanize=28, formant=48, vibrato=32"
                )

        if not tuning_applied and too_light_n > 0 and too_fake_n == 0:
            # User consistently wants stronger correction on this preset.
            tuning_applied = True
            preset["correction_amount"] = min(88, preset["correction_amount"] + 8)
            preset["retune_speed"] = min(96, preset["retune_speed"] + 6)
            preset["humanize"] = max(5, preset["humanize"] - 8)
            preset["retune_ms_equivalent"] = _retune_speed_to_ms(preset["retune_speed"])
            tuning_reasons.append(
                f"标记为 too_light（{too_light_n} 次）"
                f"→ correction +8, retune +6, humanize −8"
            )
        if not tuning_applied and too_fake_n > 0 and too_light_n == 0:
            # User wants more natural / less harsh sound on this preset.
            tuning_applied = True
            preset["correction_amount"] = max(10, preset["correction_amount"] - 8)
            preset["retune_speed"] = max(8, preset["retune_speed"] - 8)
            preset["humanize"] = min(95, preset["humanize"] + 8)
            preset["formant_preserve"] = min(95, preset["formant_preserve"] + 8)
            preset["vibrato_preserve"] = min(95, preset["vibrato_preserve"] + 5)
            preset["retune_ms_equivalent"] = _retune_speed_to_ms(preset["retune_speed"])
            tuning_reasons.append(
                f"标记为 too_fake/harsh（{too_fake_n} 次）"
                f"→ correction −8, humanize/formant +8, vibrato +5"
            )
        if not tuning_applied and too_light_n > 0 and too_fake_n > 0:
            # Mixed feedback — conservative balanced adjustment.
            tuning_applied = True
            preset["correction_amount"] = max(15, min(85, preset["correction_amount"] + 2))
            preset["humanize"] = min(85, preset["humanize"] + 3)
            preset["formant_preserve"] = min(90, preset["formant_preserve"] + 4)
            tuning_reasons.append(
                f"混合反馈（too_light ×{too_light_n} + too_fake ×{too_fake_n}）"
                f"→ 小幅折中微调"
            )

    after_params = {
        "correction_amount": preset["correction_amount"],
        "retune_speed": preset["retune_speed"],
        "retune_ms_equivalent": preset.get("retune_ms_equivalent",
                                           _retune_speed_to_ms(preset["retune_speed"])),
        "humanize": preset["humanize"],
        "formant_preserve": preset["formant_preserve"],
        "vibrato_preserve": preset["vibrato_preserve"],
    }

    preset["_feedback_parameter_adjustment"] = {
        "applied": tuning_applied,
        "before_params": before_params,
        "after_params": after_params,
        "personalization_reason": "；".join(tuning_reasons) if tuning_reasons else "无参数微调",
    }

    # ---- Step 5: short-audio penalty ---------------------------------------
    if is_short:
        confidence = max(25, confidence - 20)
        quality_reasons.append("音频较短（< 5 秒），置信度降低，建议上传完整段落以获得精准参数")

    # ---- Step 6: ensure retune_ms_equivalent -------------------------------
    if "retune_ms_equivalent" not in preset:
        preset["retune_ms_equivalent"] = _retune_speed_to_ms(preset["retune_speed"])

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
    retune_ms = preset.get("retune_ms_equivalent", _retune_speed_to_ms(retune_speed))
    flex_tune_like = preset.get("flex_tune_like", "")
    pitch_tracking = preset.get("pitch_tracking", "medium")
    best_for = preset.get("best_for", "")
    risk = preset.get("risk", "")
    preset_name = preset["preset_name"]
    preset_label = preset["preset_label"]
    suitable_for = preset.get("suitable_for", [])
    confidence = preset["confidence"]
    quality_reasons = preset.get("_quality_reasons", [])
    source_note = preset.get("_source_note", "")
    preset_source = preset["preset_source"]

    # v3.5 feedback-aware fields
    feedback_preference_score = preset.get("_feedback_score", 0)
    feedback_adjustment = preset.get("_feedback_adjustment", "")
    personalization_source = preset.get("_personalization_source", "无历史反馈数据")

    # v3.8: feedback-driven parameter tuning
    fb_param_adj = preset.get("_feedback_parameter_adjustment", {})
    feedback_parameter_adjustment = fb_param_adj

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
        f"命中预设「{preset_label}」— {preset['description']} "
        f"(置信度 {confidence}%)"
    )
    reasons.append(
        f"核心参数：retune {retune_speed} (~{retune_ms}ms) / "
        f"correction {correction_amount}% / humanize {humanize} / "
        f"formant {formant_preserve} / vibrato {vibrato_preserve}"
    )
    if flex_tune_like:
        reasons.append(f"Flex Tune 类比：{flex_tune_like}")
    if pitch_tracking:
        reasons.append(f"Pitch Tracking：{pitch_tracking}")
    if best_for:
        reasons.append(f"最适合：{best_for}")
    if scale == "minor":
        reasons.append(f"{scale_label} → humanize +8, vibrato_preserve +8 以保留情绪感")
    if "Trap" in beat_style:
        reasons.append(f"Beat 风格「{beat_style}」→ 倾向 Trap/说唱类预设")
    elif "R&B" in beat_style:
        reasons.append(f"Beat 风格「{beat_style}」→ 倾向 R&B/Soul 类预设")

    # ---- 5. next_step --------------------------------------------------------
    duration_s = analysis.get("duration_seconds", 0)
    if analysis.get("too_quiet") or analysis.get("clipped_risk"):
        next_step = "音频质量存在问题，建议先改善录音条件（输入音量/爆音），再重新上传分析"
    elif duration_s < 5.0:
        next_step = "音频较短（< 5 秒），当前参数为初步判断，建议上传完整段落获得更精准的适配"
    elif preset_name == "live_tracking":
        next_step = "最保守修音参数（几乎不可感知）。适合现场录音、播客、古典/美声。"
    elif preset_name == "natural_pop":
        next_step = "轻量自然修音参数（retune ~90 ms）。适合民谣、唱作人、不插电场景。"
    elif preset_name == "modern_pop":
        next_step = "现代流行修音参数（retune ~26 ms）。适合流行、K-Pop、电子流行。"
    elif preset_name == "emotional_rnb":
        next_step = "情绪 R&B 修音参数（retune ~58 ms）。保留转音与滑音，适合 R&B、Soul。"
    elif preset_name == "melodic_trap":
        next_step = "旋律 Trap 修音参数（retune ~8 ms）。快速锁定音高，适合说唱、Trap、Drill。"
    elif preset_name == "hyperpop":
        next_step = "Hyperpop 创意效果（retune 0 ms）。极速量化 + 电子音色。注意：这是创意效果，非传统修音。"
    else:
        next_step = "参数已生成，可试听效果并根据需要微调强度。"

    # ---- 6. processing metadata (v2.9) --------------------------------------
    if correction_amount >= 80 and retune_speed >= 75:
        processing_intensity = "high"
    elif correction_amount >= 50 or retune_speed >= 55:
        processing_intensity = "medium"
    else:
        processing_intensity = "low"

    intensity_labels = {"high": "重度处理", "medium": "中度处理", "low": "轻度处理"}

    style_summary_map = {
        "robotic": "极快音高修正 + 离散量化 + 干/湿混合最小",
        "trap": "快速音高修正 + 低切 80Hz + 低 humanize",
        "pop": "适中音高修正 + 标准平滑",
        "rnb": "中慢速音高修正 + 中高 humanize + 中高 formant/vibrato 保留",
        "natural": "慢速音高修正 + 高 humanize + 高 formant/vibrato 保留",
    }
    style_summary = style_summary_map.get(style_mode, "标准音高修正")

    processing_summary = (
        f"{intensity_labels[processing_intensity]}：{style_summary}。"
        f"retune={retune_speed} correction={correction_amount}% "
        f"humanize={humanize} formant={formant_preserve} vibrato={vibrato_preserve}"
    )
    if analysis.get("too_quiet"):
        processing_summary += " | 响度补偿已应用（修正上限 50%）"
    if analysis.get("clipped_risk"):
        processing_summary += " | 爆音保护已应用（−8 dB + 修正降低 25%）"

    # ---- 7. adaptation metadata (v2.8 dual-input) ---------------------------
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

    # ---- 8. backing_match (v3.0) ---------------------------------------------
    if backing:
        backing_style_label = backing.get("style", "unknown")
        backing_conf = backing.get("confidence", 0)
        backing_match = (
            f"伴奏「{backing_style_label}」× 人声预设「{preset_label}」"
            f" — 匹配置信度 {backing_conf}%"
        )
    else:
        backing_match = "无伴奏输入，仅基于人声特征适配"

    # ---- 9. adaptation_reason (v3.0) -----------------------------------------
    adaptation_reason = "；".join(reasons)

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
        "retune_ms_equivalent": retune_ms,
        "correction_amount": correction_amount,
        "humanize": humanize,
        "formant_preserve": formant_preserve,
        "vibrato_preserve": vibrato_preserve,
        "flex_tune_like": flex_tune_like,
        "pitch_tracking": pitch_tracking,
        "best_for": best_for,
        "risk": risk,
        "style_mode": style_mode,
        "style_mode_label": style_labels[style_mode],
        "vocal_quality": vocal_quality,
        "feedback_preference_score": feedback_preference_score,
        "feedback_adjustment": feedback_adjustment,
        "personalization_source": personalization_source,
        "feedback_parameter_adjustment": feedback_parameter_adjustment,
        "backing_match": backing_match,
        "adaptation_reason": adaptation_reason,
        "reason": "；".join(reasons),
        "next_step": next_step,
        "adaptation_inputs": adaptation_inputs,
        "adaptation_summary": adaptation_summary,
        "processing_intensity": processing_intensity,
        "final_used_params": {
            "correction_amount": correction_amount,
            "retune_speed": retune_speed,
            "retune_ms_equivalent": retune_ms,
            "humanize": humanize,
            "formant_preserve": formant_preserve,
            "vibrato_preserve": vibrato_preserve,
            "style_mode": style_mode,
            "target_key": key,
            "target_scale": scale,
        },
        "applied_pitch_correction": True,
        "processing_summary": processing_summary,
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
    humanize: float = 50.0,
    formant_preserve: float = 50.0,
    vibrato_preserve: float = 50.0,
    quality_override: str | None = None,
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
    humanize : 0–100  (higher = more timing/amplitude jitter for natural feel)
    formant_preserve : 0–100  (higher = more dry signal blended back)
    vibrato_preserve : 0–100  (higher = less correction on vibrato segments)
    quality_override : if ``\"clipped\"`` → reduce correction; if ``\"quiet\"`` → cap correction
    """
    import librosa
    from scipy.ndimage import median_filter

    original = samples.copy()

    # Quick guard: skip near-silent inputs.
    if np.sqrt(np.mean(samples ** 2)) < 0.002:
        logging.info("Pitch correction skipped: near-silent input")
        return samples

    # ---- quality overrides ---------------------------------------------------
    if quality_override == "quiet":
        correction_amount = min(correction_amount, 50.0)
        retune_speed = min(retune_speed, 45.0)
        logging.info("Quality override (too_quiet): correction capped at 50%%, retune at 45")
    elif quality_override == "clipped":
        correction_amount = max(15.0, correction_amount * 0.75)
        logging.info("Quality override (clipped): correction reduced by 25%%")

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

    # Per-style hard-tune threshold — lower = kicks in sooner at lower correction %.
    HARD_TUNE = {
        "robotic": 0.35,
        "trap": 0.55,
        "pop": 0.70,
        "rnb": 0.75,
        "natural": 0.85,
    }.get(style_mode, 0.70)

    # ---- vibrato detection ---------------------------------------------------
    # Compute local pitch variance over a sliding window; high variance
    # regions are likely vibrato.  Scale down correction there.
    vibrato_mask = np.ones(n_frames, dtype=np.float64)
    if vibrato_preserve > 20.0:
        vibrato_scale = vibrato_preserve / 100.0   # 0.2 → 1.0
        window = 7  # frames
        for i in range(n_frames):
            lo = max(0, i - window)
            hi = min(n_frames, i + window + 1)
            segment = f0[lo:hi]
            voiced_seg = voiced_flag[lo:hi]
            valid = segment[voiced_seg]
            if len(valid) >= 3:
                local_std = float(np.std(valid))
                local_mean = float(np.mean(valid)) + 1e-9
                cv = local_std / local_mean  # coefficient of variation
                if cv > 0.012:
                    vibrato_mask[i] = max(0.15, 1.0 - vibrato_scale * 0.85)
                elif cv > 0.008:
                    vibrato_mask[i] = max(0.35, 1.0 - vibrato_scale * 0.65)

    # ---- per-frame semitone correction ---------------------------------------
    correction_st = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        if voiced_flag[i] and f0[i] > 0:
            midi = librosa.hz_to_midi(f0[i])
            nearest = min(target_notes, key=lambda n: abs(n - midi))
            raw_diff = nearest - midi               # full semitone gap

            if strength >= HARD_TUNE:
                t = (strength - HARD_TUNE) / (1.0 - HARD_TUNE)
                effective = HARD_TUNE + t * (1.0 - HARD_TUNE)
                correction_st[i] = raw_diff * effective
            else:
                correction_st[i] = raw_diff * strength

            # Apply vibrato mask: reduce correction on vibrato frames.
            correction_st[i] *= vibrato_mask[i]

    # ---- stair-step quantize for robotic / trap modes ------------------------
    if style_mode == "robotic" and strength >= 0.80:
        correction_st = np.round(correction_st)  # 1.0 st steps — hard quantize
    elif style_mode == "trap" and strength >= 0.85:
        correction_st = np.round(correction_st * 2.0) / 2.0  # 0.5 st steps

    # ---- retune_speed → smoothing filter size (per-style ranges) -------------
    if style_mode == "robotic":
        filter_size = 1
    elif style_mode == "trap":
        filter_size = max(1, int(10 - retune_speed * 0.12))
    elif style_mode == "pop":
        filter_size = max(1, int(21 - retune_speed * 0.25))
    elif style_mode == "rnb":
        filter_size = max(1, int(25 - retune_speed * 0.30))
    else:  # natural
        filter_size = max(1, int(31 - retune_speed * 0.36))

    if filter_size > 1:
        correction_st = median_filter(correction_st, size=filter_size)

    # ---- humanize: timing & amplitude jitter --------------------------------
    do_time_jitter = humanize > 15.0
    do_amp_jitter = humanize > 25.0
    jitter_scale = max(0.0, (100.0 - humanize) / 100.0)

    # ---- adaptive segment sizing (wider 6× spread) ---------------------------
    hop = 256
    if style_mode == "robotic":
        seg_samples = 1024          # ~23 ms — ultra-fast tracking, hard snap
        base_step = seg_samples // 4     # 25 % overlap (aggressive)
        use_median = True
    elif style_mode == "trap":
        seg_samples = 2560          # ~58 ms — responsive
        base_step = seg_samples // 2
        use_median = True
    elif style_mode == "pop":
        seg_samples = 4096          # ~93 ms — balanced
        base_step = seg_samples // 3
        use_median = False
    elif style_mode == "rnb":
        seg_samples = 5120          # ~116 ms — smooth
        base_step = seg_samples // 3
        use_median = False
    else:  # natural
        seg_samples = 6144          # ~139 ms — slow, wide, smooth
        base_step = seg_samples // 3
        use_median = False

    # Pre-compute per-segment correction values for the envelope loop.
    output = np.zeros(len(samples) + seg_samples, dtype=np.float64)
    weight = np.zeros_like(output)

    rng = np.random.RandomState(42)

    use_rect_env = (style_mode == "robotic")

    seg_start = 0
    while seg_start < len(samples):
        # Apply humanize jitter to segment boundary.
        jitter = 0
        if do_time_jitter and jitter_scale < 0.95:
            max_jitter = int(base_step * 0.50 * (1.0 - jitter_scale))
            if max_jitter > 0:
                jitter = rng.randint(-max_jitter, max_jitter + 1)
        step = max(hop, base_step + jitter)

        end = min(seg_start + seg_samples, len(samples))
        if end - seg_start < hop:
            seg_start += step
            continue

        chunk = samples[seg_start:end].astype(np.float64).copy()
        chunk_len = len(chunk)

        f_start = seg_start // hop
        f_end = min(end // hop + 1, n_frames)
        if f_end > f_start:
            seg_corrections = correction_st[f_start:f_end]
            semitones = float(np.median(seg_corrections) if use_median else np.mean(seg_corrections))

            # v3.3: skip correction on mostly-unvoiced segments (breaths, consonants,
            # silence).  Prevents the engine from hard-pitching non-pitched material.
            voiced_in_seg = voiced_flag[f_start:f_end]
            voiced_ratio = float(np.mean(voiced_in_seg)) if len(voiced_in_seg) > 0 else 0.0
            MIN_VOICED_RATIO = 0.18
            if voiced_ratio < MIN_VOICED_RATIO:
                semitones = 0.0

            # Humanize amplitude: random variation of correction amount.
            if do_amp_jitter and jitter_scale < 0.9:
                amp_jitter = 1.0 + rng.uniform(-0.25, 0.25) * (1.0 - jitter_scale)
                semitones *= amp_jitter
        else:
            semitones = 0.0

        # Per-style max shift clamp.
        if style_mode in ("robotic", "trap") and strength >= HARD_TUNE:
            max_shift = 12.0
        elif strength >= HARD_TUNE:
            max_shift = 8.0
        else:
            max_shift = 6.0
        semitones = max(-max_shift, min(max_shift, semitones))

        # Per-style correction threshold (cents).
        if style_mode == "robotic":
            threshold = 0.005   # 0.5 cents — micro-corrections
        elif style_mode == "trap":
            threshold = 0.01    # 1 cent
        else:
            threshold = 0.03    # 3 cents
        if abs(semitones) > threshold:
            try:
                shifted = librosa.effects.pitch_shift(
                    y=chunk, sr=sr, n_steps=semitones,
                )
            except Exception:
                shifted = chunk
        else:
            shifted = chunk.copy()

        # Overlap-add envelope (v3.3: raised-cosine crossfade for non-robotic).
        env = np.ones(chunk_len, dtype=np.float64)
        if use_rect_env:
            # Rectangular — no crossfade, sharper transitions for robotic.
            # Add a 2-sample micro-fade to prevent DC clicks at boundaries.
            if seg_start > 0 and chunk_len >= 4:
                env[0] = 0.0
                env[1] = 0.5
            if end < len(samples) and chunk_len >= 4:
                env[-2] = 0.5
                env[-1] = 0.0
        else:
            if seg_start > 0:
                r = min(step, chunk_len)
                # Raised-cosine fade-in: 0→1, reduces phase-discontinuity clicks
                t = np.linspace(0.0, np.pi, r)
                env[:r] = 0.5 * (1.0 - np.cos(t))
            if end < len(samples):
                r = min(step, chunk_len)
                # Raised-cosine fade-out: 1→0
                t = np.linspace(0.0, np.pi, r)
                env[-r:] = 0.5 * (1.0 + np.cos(t))

        out_len = min(chunk_len, len(output) - seg_start)
        output[seg_start:seg_start + out_len] += shifted[:out_len] * env[:out_len]
        weight[seg_start:seg_start + out_len] += env[:out_len]

        seg_start += step

    # Normalise overlap region.
    mask = weight > 0
    output[mask] /= weight[mask]
    output = output[:len(samples)]

    # ---- robotic second pass: re-detect F0 on output & snap residuals ---------
    if style_mode == "robotic":
        try:
            f0_pass2, vf_pass2, _ = librosa.pyin(
                output.astype(np.float64),
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=sr,
                hop_length=256,
            )
            if f0_pass2 is not None and np.any(vf_pass2):
                n2 = len(f0_pass2)
                for i in range(n2):
                    if vf_pass2[i] and f0_pass2[i] > 0:
                        midi = librosa.hz_to_midi(f0_pass2[i])
                        nearest = min(target_notes, key=lambda n: abs(n - midi))
                        diff = nearest - midi
                        if abs(diff) > 0.10:  # >10 cents residual → snap hard
                            t_start = i * 256
                            t_end = min(t_start + 256, len(output))
                            seg = output[t_start:t_end].astype(np.float64)
                            try:
                                shifted = librosa.effects.pitch_shift(
                                    y=seg, sr=sr, n_steps=diff,
                                )
                                output[t_start:t_end] = shifted[:len(seg)]
                            except Exception:
                                pass
        except Exception:
            pass

        # Tanh saturation for robotic character.
        output = np.tanh(output * 1.3) / 1.3

    # ---- formant_preserve: dry/wet blend -------------------------------------
    # Higher formant_preserve → more original character blended back.
    # Max 75 % dry blend for extreme contrast with robotic.
    fp = formant_preserve / 100.0  # 0.0 → 1.0
    dry_mix = fp * 0.75            # 0 % → 75 % dry
    output = output * (1.0 - dry_mix) + original[:len(output)] * dry_mix

    # v3.3: soft peak limiter (lower ceiling + gentle curve vs hard clip).
    # Protects against intersample peaks and segment-boundary transients.
    peak = np.max(np.abs(output))
    if peak > 0.92:
        output *= 0.92 / peak
    # Gentle saturation for any remaining overs above 0.88
    over_mask = np.abs(output) > 0.88
    if np.any(over_mask):
        output[over_mask] = np.tanh(output[over_mask] * 1.08) / 1.08

    return output.astype(np.float32)


# ── main processing entry-point ─────────────────────────────────────────────

def _apply_autotune_preview(
    audio: AudioSegment,
    profile: dict,
    analysis: dict,
) -> AudioSegment:
    """Apply real Auto-Tune pitch correction + gain staging to a WAV.

    Uses ALL profile parameters (v2.9):
    - correction_amount, retune_speed, style_mode → pitch correction engine
    - humanize → timing/amplitude jitter for natural feel
    - formant_preserve → dry/wet blend to preserve vocal character
    - vibrato_preserve → reduced correction on vibrato segments

    Processing chain:
    1. Clipping protection (gain reduction, stronger for clipped risk)
    2. Loudness normalisation (RMS toward -17 dBFS)
    3. Pitch correction (F0 + per-segment pitch_shift → target scale,
       using retune_speed, correction_amount, humanize, formant_preserve,
       vibrato_preserve, style_mode)
    4. Style-specific tonal shaping (80 Hz low-cut for trap/robotic)
    5. Final peak limiting
    """
    sr = audio.frame_rate
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    samples /= 32768.0              # 16-bit → [-1, 1]

    style_mode = profile.get("style_mode", "natural")
    correction_amount = float(profile.get("correction_amount", 40))
    retune_speed = float(profile.get("retune_speed", 50))
    humanize = float(profile.get("humanize", 50))
    formant_preserve = float(profile.get("formant_preserve", 50))
    vibrato_preserve = float(profile.get("vibrato_preserve", 50))
    key = profile.get("target_key", "C")
    scale = profile.get("target_scale", "major")

    is_clipped = analysis.get("clipped_risk", False)
    is_quiet = analysis.get("too_quiet", False)

    # ---- quality override label ----------------------------------------------
    quality_override: str | None = None
    if is_clipped:
        quality_override = "clipped"
    elif is_quiet:
        quality_override = "quiet"

    # ---- 1. clipping protection (numpy) --------------------------------------
    if is_clipped:
        samples *= 0.398            # ≈ -8 dB (stronger than v2.8's -5 dB)
        logging.info("Clipping protection: -8 dB (v2.9 stronger reduction)")

    # ---- 2. loudness normalisation (numpy) -----------------------------------
    rms = float(np.sqrt(np.mean(samples ** 2)))
    TARGET_RMS = 10.0 ** (-17.0 / 20.0)  # -17 dBFS linear

    if is_quiet or rms < 10.0 ** (-22.0 / 20.0):
        if rms > 1e-8:
            boost = TARGET_RMS / rms
            boost = min(boost, 12.0)        # max +21.6 dB (conservative, was +24 dB)
            samples *= boost
            logging.info("Loudness boost: %.1f dB (v2.9 conservative)", 20.0 * np.log10(boost))
    elif rms > 10.0 ** (-14.0 / 20.0):
        cut = TARGET_RMS / rms
        samples *= cut
        logging.info("Loudness cut: %.1f dB", 20.0 * np.log10(cut))

    # ---- 3. pitch correction (librosa) ---------------------------------------
    target_notes = _compute_target_notes(key, scale)
    logging.info(
        "Pitch correction v2.9: key=%s scale=%s target_notes=%d mode=%s "
        "correction=%.0f%% retune=%.0f humanize=%.0f formant=%.0f vibrato=%.0f "
        "quality=%s",
        key, scale, len(target_notes), style_mode, correction_amount,
        retune_speed, humanize, formant_preserve, vibrato_preserve,
        quality_override or "normal",
    )
    samples = _pitch_correct(
        samples, sr, target_notes,
        correction_amount, retune_speed, style_mode,
        humanize, formant_preserve, vibrato_preserve,
        quality_override,
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
    backing_track: UploadFile | None = File(None),
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

    # --- 4b. Process optional backing_track file (v3.0) --------------------------
    backing_analysis: dict | None = None
    if backing_track is not None:
        bt_contents = await backing_track.read()
        if bt_contents and len(bt_contents) > 44:  # > WAV header
            bt_suffix = Path(backing_track.filename or "backing.wav").suffix or ".wav"
            bt_id = uuid.uuid4().hex
            bt_raw_name = f"bt_raw_{unique_id}_{bt_id}{bt_suffix}"
            bt_raw_path = UPLOAD_DIR / bt_raw_name
            bt_raw_path.write_bytes(bt_contents)

            bt_wav_name = f"bt_processed_{unique_id}_{bt_id}.wav"
            bt_wav_path = PROCESSED_DIR / bt_wav_name

            try:
                # Convert backing track to WAV and capture audio metrics.
                bt_wav_analysis = _convert_to_wav(bt_raw_path, bt_wav_path)
                # Analyse musical features from the converted WAV.
                bt_musical = _analyze_backing_track(bt_wav_path)

                backing_analysis = {
                    "duration_seconds": bt_wav_analysis["duration_seconds"],
                    "sample_rate": bt_wav_analysis["sample_rate"],
                    "channels": bt_wav_analysis["channels"],
                    "peak_dbfs": bt_wav_analysis["peak_dbfs"],
                    "average_dbfs": bt_wav_analysis["average_dbfs"],
                    "energy_level": bt_musical["energy_level"],
                    "low_frequency_weight": bt_musical["low_frequency_weight"],
                    "brightness": bt_musical["brightness"],
                    "rough_style_hint": bt_musical["suggested_style"],
                    "estimated_bpm": bt_musical["estimated_bpm"],
                    "confidence": bt_musical["confidence"],
                }
            except CouldntDecodeError:
                bt_raw_path.unlink(missing_ok=True)
                logging.warning("Could not decode backing_track — proceeding without")
            except FileNotFoundError:
                bt_raw_path.unlink(missing_ok=True)
                logging.warning("ffmpeg missing for backing_track — proceeding without")
            except Exception:
                logging.exception("Backing-track analysis failed — proceeding without")

    # Build backing dict for Auto-Tune profile generation.
    # backing_track file analysis takes priority; form fields fill gaps.
    backing: dict | None = None
    if backing_analysis is not None:
        backing = {
            "style": backing_analysis["rough_style_hint"],
            "energy": backing_analysis["energy_level"],
            "energy_level": backing_analysis["energy_level"],
            "bass": backing_analysis["low_frequency_weight"],
            "bass_level": "high" if backing_analysis["low_frequency_weight"] > 45
                         else "medium" if backing_analysis["low_frequency_weight"] > 25
                         else "low",
            "brightness": backing_analysis["brightness"],
            "estimated_bpm": backing_analysis["estimated_bpm"],
            "confidence": backing_analysis["confidence"],
            "low_frequency_weight": backing_analysis["low_frequency_weight"],
            "_backing_analysis": backing_analysis,  # stash for header
        }
    elif any([backing_style.strip(), backing_energy.strip(),
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

    if backing_analysis is not None:
        headers["X-Backing-Analysis"] = quote(
            json.dumps(backing_analysis, ensure_ascii=False), safe=""
        )

    headers["X-Profile-Id"] = unique_id

    return FileResponse(
        path=str(wav_path),
        media_type="audio/wav",
        filename=wav_name,
        headers=headers,
    )


# ── v3.1 quality-check: 3-version Auto-Tune calibration ────────────────────


@app.post("/quality-check")
async def quality_check(
    file: UploadFile = File(...),
    key: str = Form("C"),
    scale: str = Form("major"),
    beat_style: str = Form(""),
    backing_style: str = Form(""),
):
    """Generate five mainstream Auto-Tune versions of the same vocal for A/B comparison.

    **natural_pop**    — light correction (~90 ms retune, 28 % correction)
    **modern_pop**      — balanced pop (~26 ms retune, 60 % correction)
    **emotional_rnb**  — smooth R&B (~58 ms retune, 42 % correction, vibrato preserved)
    **melodic_trap**   — fast trap (~8 ms retune, 78 % correction)
    **hyperpop**        — creative electronic (~0 ms retune, 98 % correction)

    Uses the v3.2 Mainstream Auto-Tune Parameter Library directly.
    Saves five WAV files to ``backend/processed/`` and returns a JSON
    report with download URLs, per-preset profiles, and quantitative
    audio difference metrics (RMS delta, peak ratio, waveform correlation).

    Use ``GET /download/{filename}`` to retrieve each file.
    """
    # --- 1. Validate & read -------------------------------------------------
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type: {file.content_type}. "
                   "Please upload WAV, MP3, MP4, or M4A audio.",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File size {len(contents) / (1024 * 1024):.1f} MB exceeds the 25 MB limit.",
        )

    # --- 2. Save raw & convert to WAV ---------------------------------------
    suffix = Path(file.filename or "vocal.wav").suffix or ".wav"
    vocal_id = uuid.uuid4().hex
    raw_name = f"qc_raw_{vocal_id}{suffix}"
    raw_path = UPLOAD_DIR / raw_name
    raw_path.write_bytes(contents)

    wav_name = f"qc_source_{vocal_id}.wav"
    wav_path = PROCESSED_DIR / wav_name

    try:
        analysis = _convert_to_wav(raw_path, wav_path)
    except CouldntDecodeError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Could not decode the uploaded audio.",
        )
    except FileNotFoundError:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is not installed or not on your PATH.",
        )

    # --- 3. Generate & apply five v3.2 mainstream presets --------------------
    # v3.3: uses the v3.2 Mainstream Auto-Tune Parameter Library directly,
    # skipping live_tracking (too conservative to show meaningful contrast).
    QC_PRESET_ORDER = ["natural_pop", "modern_pop", "emotional_rnb", "melodic_trap", "trap_polished", "hyperpop"]
    results: dict[str, dict] = {}
    output_wavs: dict[str, Path] = {}

    for preset_name in QC_PRESET_ORDER:
        pdef = MAINSTREAM_AUTOTUNE_PRESETS[preset_name]
        profile = {
            "preset_name": pdef["preset_name"],
            "preset_label": pdef["preset_label"],
            "style_mode": PRESET_TO_STYLE[preset_name],
            "retune_speed": pdef["retune_speed"],
            "retune_ms_equivalent": pdef.get("retune_ms_equivalent", _retune_speed_to_ms(pdef["retune_speed"])),
            "correction_amount": pdef["correction_amount"],
            "humanize": pdef["humanize"],
            "formant_preserve": pdef["formant_preserve"],
            "vibrato_preserve": pdef["vibrato_preserve"],
            "target_key": key,
            "target_scale": scale,
            "target_scale_label": "小调" if scale == "minor" else "大调",
            "confidence": 100,
            "preset_source": "quality_check_v3.3",
            "vocal_quality": "normal",
        }

        try:
            audio = AudioSegment.from_file(wav_path)
            processed = _apply_autotune_preview(audio, profile, analysis)
            out_name = f"qc_{preset_name}_{vocal_id}.wav"
            out_path = PROCESSED_DIR / out_name
            processed.export(out_path, format="wav")
            output_wavs[preset_name] = out_path
        except Exception:
            logging.exception("Quality-check processing failed for %s", preset_name)
            raise HTTPException(
                status_code=500,
                detail=f"Audio processing failed for {pdef['preset_label']} version.",
            )

        results[preset_name] = {
            "filename": out_path.name,
            "download_url": f"/download/{out_path.name}",
            "preset_label": pdef["preset_label"],
            "profile": {
                "preset_name": profile["preset_name"],
                "preset_label": profile["preset_label"],
                "style_mode": profile["style_mode"],
                "retune_speed": profile["retune_speed"],
                "retune_ms_equivalent": profile["retune_ms_equivalent"],
                "correction_amount": profile["correction_amount"],
                "humanize": profile["humanize"],
                "formant_preserve": profile["formant_preserve"],
                "vibrato_preserve": profile["vibrato_preserve"],
                "pitch_tracking": pdef.get("pitch_tracking", ""),
                "flex_tune_like": pdef.get("flex_tune_like", ""),
                "best_for": pdef.get("best_for", ""),
                "risk": pdef.get("risk", ""),
            },
            "expected_character": pdef.get("description", ""),
        }

    # --- 4. Compute quantitative audio differences (v3.3) -------------------
    try:
        import librosa

        waveforms: dict[str, np.ndarray] = {}
        for vn, wpath in output_wavs.items():
            y, sr = librosa.load(str(wpath), sr=22050, mono=True)
            waveforms[vn] = y

        def _compute_delta(a: np.ndarray, b: np.ndarray) -> dict:
            """Return RMS delta (dB), peak ratio, and Pearson correlation."""
            min_len = min(len(a), len(b))
            a_cut, b_cut = a[:min_len], b[:min_len]

            rms_a = float(np.sqrt(np.mean(a_cut ** 2)) + 1e-9)
            rms_b = float(np.sqrt(np.mean(b_cut ** 2)) + 1e-9)
            rms_delta_db = round(20.0 * np.log10(rms_b / rms_a), 2)

            peak_a = float(np.max(np.abs(a_cut)))
            peak_b = float(np.max(np.abs(b_cut)))
            peak_ratio = round(peak_b / (peak_a + 1e-9), 3)

            corr = round(float(np.corrcoef(a_cut, b_cut)[0, 1]), 4)

            return {
                "rms_delta_db": rms_delta_db,
                "peak_ratio": peak_ratio,
                "waveform_correlation": corr,
            }

        comparison = {
            "natural_pop_vs_modern_pop": _compute_delta(waveforms["natural_pop"], waveforms["modern_pop"]),
            "modern_pop_vs_emotional_rnb": _compute_delta(waveforms["modern_pop"], waveforms["emotional_rnb"]),
            "emotional_rnb_vs_melodic_trap": _compute_delta(waveforms["emotional_rnb"], waveforms["melodic_trap"]),
            "melodic_trap_vs_hyperpop": _compute_delta(waveforms["melodic_trap"], waveforms["hyperpop"]),
            "natural_pop_vs_hyperpop": _compute_delta(waveforms["natural_pop"], waveforms["hyperpop"]),
        }
    except Exception:
        logging.exception("Quantitative comparison failed — continuing without")
        comparison = {"error": "Could not compute quantitative differences"}

    # --- 4b. Simulate auto-mode recommendation (v3.6) --------------------------
    recommended_preset: str | None = None
    try:
        sim_backing: dict | None = None
        if backing_style.strip():
            sim_backing = {"style": backing_style.strip()}
        auto_match = _match_autotune_preset_auto(
            beat_style=beat_style.strip() or "流行节奏",
            scale=scale,
            analysis=analysis,
            strength_preference=50,
            backing=sim_backing,
        )
        recommended_preset = auto_match.get("preset_name")
    except Exception:
        logging.exception("Auto-mode simulation failed — no recommendation")

    # --- 5. Build response --------------------------------------------------
    # Source WAV is already saved as qc_source_{vocal_id}.wav
    source_url = f"/download/{wav_name}"

    return {
        "vocal_id": vocal_id,
        "source_filename": file.filename,
        "source_download_url": source_url,
        "key": key,
        "scale": scale,
        "scale_label": "小调" if scale == "minor" else "大调",
        "recommended_preset": recommended_preset,
        "source_analysis": {
            "duration_seconds": analysis["duration_seconds"],
            "peak_dbfs": analysis["peak_dbfs"],
            "average_dbfs": analysis["average_dbfs"],
        },
        "versions": results,
        "comparison": comparison,
        "how_to_test": (
            "1. Listen to the source, then each version.  "
            "2. The 'recommended_preset' is what auto mode would pick.  "
            "3. Use the '10s preview' toggle for quick A/B.  "
            "4. natural_pop — 几乎听不出修音。  "
            "5. modern_pop — 稳定明亮的主流流行修音。  "
            "6. emotional_rnb — 保留转音和颤音。  "
            "7. melodic_trap — 快速音高锁定，明显的修音感。  "
            "8. hyperpop — 强电子音色，创意效果。"
        ).format(id=vocal_id),
    }


@app.get("/download/{filename}")
def download_file(filename: str):
    """Download a processed WAV file by filename.

    Only serves files from the ``processed/`` directory.
    """
    file_path = (PROCESSED_DIR / filename).resolve()
    if PROCESSED_DIR.resolve() not in file_path.parents:
        raise HTTPException(status_code=403, detail="Invalid path.")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        path=str(file_path),
        media_type="audio/wav",
        filename=filename,
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


# ── v3.4 quality-check A/B listening feedback loop ─────────────────────────

QUALITY_FEEDBACK_PATH = FEEDBACK_DIR / "autotune_listening.jsonl"

VALID_QC_LABELS = {"best", "too_fake", "too_light", "too_heavy", "harsh", "natural", "good"}

VALID_QC_RATINGS = {1, 2, 3, 4, 5}


class QualityFeedbackRequest(BaseModel):
    vocal_id: str
    preset_name: str
    rating: int | None = None
    label: str | None = None
    note: str | None = None
    backing_style: str | None = None


@app.post("/quality-feedback")
async def submit_quality_feedback(body: QualityFeedbackRequest):
    """Record A/B listening feedback for a quality-check preset version.

    Accepts a JSON body with:
    - ``vocal_id``: the vocal session ID from ``/quality-check``
    - ``preset_name``: one of natural_pop / modern_pop / emotional_rnb /
      melodic_trap / hyperpop
    - ``rating``: 1–5 (optional)
    - ``label``: best / too_fake / too_light / too_heavy / harsh / natural / good
    - ``note``: free-text comment (optional)
    - ``backing_style``: pop / trap / rnb / electronic / unknown (optional)

    Appends one JSONL line to ``backend/feedback/autotune_listening.jsonl``.
    This data will power future personalised Auto-Tune parameter recommendation.
    """
    # Validate label if provided.
    if body.label is not None and body.label not in VALID_QC_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid label: {body.label!r}. "
                   f"Must be one of {sorted(VALID_QC_LABELS)}.",
        )

    # Validate rating if provided.
    if body.rating is not None and body.rating not in VALID_QC_RATINGS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid rating: {body.rating}. Must be 1–5.",
        )

    # Validate preset_name.
    if body.preset_name not in MAINSTREAM_AUTOTUNE_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset: {body.preset_name!r}. "
                   f"Must be one of {sorted(MAINSTREAM_AUTOTUNE_PRESETS.keys())}.",
        )

    record: dict = {
        "vocal_id": body.vocal_id,
        "preset_name": body.preset_name,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    if body.rating is not None:
        record["rating"] = body.rating
    if body.label is not None:
        record["label"] = body.label
    if body.note is not None:
        record["note"] = body.note
    if body.backing_style is not None:
        record["backing_style"] = body.backing_style

    try:
        with open(QUALITY_FEEDBACK_PATH, "a", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        logging.exception("Failed to write quality-feedback record")
        raise HTTPException(
            status_code=500,
            detail=f"Could not save feedback: {exc}",
        )

    logging.info(
        "Quality feedback recorded: vocal_id=%s preset=%s label=%s rating=%s",
        body.vocal_id, body.preset_name, body.label, body.rating,
    )

    # v4.2: auto-update agent inbox after every feedback record
    try:
        _update_agent_inbox()
    except Exception:
        logging.exception("Failed to update agent inbox (non-fatal)")

    return {
        "status": "recorded",
        "vocal_id": body.vocal_id,
        "preset_name": body.preset_name,
        "label": body.label,
        "rating": body.rating,
    }


# ── v3.7 debug endpoints ────────────────────────────────────────────────────


@app.get("/debug/agent-inbox")
def debug_agent_inbox():
    """Return metadata and a preview of the agent inbox file.

    The inbox file is auto-updated after every POST /quality-feedback.
    An AI agent (Claude, Codex) can read the file directly from disk
    at ``agent_inbox/autotune_feedback_latest.md``.
    """
    exists = AGENT_INBOX_PATH.exists()
    preview = ""
    last_updated = None
    if exists:
        try:
            content = AGENT_INBOX_PATH.read_text(encoding="utf-8")
            # Grab first 800 chars as preview
            preview = content[:800]
            # Extract the timestamp from the first line containing "Last updated:"
            for line in content.split("\n"):
                if "Last updated:" in line:
                    last_updated = line.split("Last updated:")[-1].strip().rstrip(".")
                    break
        except Exception:
            preview = "(could not read)"

    return {
        "inbox_file_path": str(AGENT_INBOX_PATH),
        "file_exists": exists,
        "last_updated": last_updated,
        "preview": preview,
        "usage": (
            "The agent inbox file is at agent_inbox/autotune_feedback_latest.md.  "
            "An AI agent can read it directly from disk.  "
            "It is auto-updated after every POST /quality-feedback."
        ),
    }


@app.get("/debug/autotune-feedback-preferences")
def debug_feedback_preferences():
    """Return the full feedback preference snapshot for inspection.

    Useful for verifying that A/B listening feedback is being recorded and
    scored correctly before it influences auto-mode recommendations.
    """
    preferences = _load_autotune_feedback_preferences()

    per_preset: dict[str, dict] = {}
    for pname in sorted(MAINSTREAM_AUTOTUNE_PRESETS.keys()):
        pref = preferences.get(pname, {})
        per_preset[pname] = {
            "score": pref.get("score", 0),
            "count": pref.get("count", 0),
            "too_light_count": pref.get("too_light_count", 0),
            "too_fake_harsh_count": pref.get("too_fake_harsh_count", 0),
            "best_count": pref.get("best_count", 0),
        }

    return {
        "feedback_file_path": str(QUALITY_FEEDBACK_PATH),
        "file_exists": QUALITY_FEEDBACK_PATH.exists(),
        "record_count": sum(p.get("count", 0) for p in preferences.values()),
        "per_preset": per_preset,
    }


@app.get("/debug/autotune-gap-status")
def debug_gap_status():
    """Check whether the feedback-gap pattern that triggers trap_polished
    recommendation is currently active.

    Returns the specific label counts for melodic_trap and hyperpop, plus
    the gap_detected flag and the preset that would be recommended.
    """
    preferences = _load_autotune_feedback_preferences()

    mt = preferences.get("melodic_trap", {})
    hp = preferences.get("hyperpop", {})

    mt_too_light = mt.get("too_light_count", 0)
    hp_too_fake = hp.get("too_fake_harsh_count", 0)
    gap_detected = mt_too_light > 0 and hp_too_fake > 0

    return {
        "melodic_trap": {
            "too_light_count": mt_too_light,
            "total_count": mt.get("count", 0),
            "score": mt.get("score", 0),
        },
        "hyperpop": {
            "too_fake_harsh_count": hp_too_fake,
            "total_count": hp.get("count", 0),
            "score": hp.get("score", 0),
        },
        "gap_detected": gap_detected,
        "would_recommend": "trap_polished" if gap_detected else "normal_flow",
    }


# ── v3.9 calibration profile ────────────────────────────────────────────────

# Intensity mapping for presets (used in calibration).
_PRESET_INTENSITY = {
    "natural_pop": "light",
    "live_tracking": "light",
    "modern_pop": "medium",
    "emotional_rnb": "medium-light",
    "melodic_trap": "medium-heavy",
    "trap_polished": "heavy",
    "hyperpop": "extreme",
}


@app.get("/debug/autotune-calibration-profile")
def debug_calibration_profile():
    """Compute a user calibration profile from all A/B listening feedback.

    Reads every record in ``autotune_listening.jsonl`` and returns:

    - ``preferred_intensity`` — which intensity band gets the most "best" votes
    - ``preferred_retune_range_ms`` — [min, max] ms of best/good presets
    - ``preferred_correction_range`` — [min, max] % of best/good presets
    - ``disliked_artifacts`` — ranked list of negative patterns
    - ``best_presets_by_vocal_type`` — per-vocal_id best preset
    - ``total_sessions`` — number of distinct vocal_id values
    - ``total_records`` — total feedback lines read
    """
    if not QUALITY_FEEDBACK_PATH.exists():
        return {
            "status": "no_feedback_data",
            "hint": "Submit A/B listening feedback via /quality-feedback first.",
        }

    # ---- load all raw records -------------------------------------------------
    records: list[dict] = []
    try:
        with open(QUALITY_FEEDBACK_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        logging.exception("Failed to read feedback for calibration")
        return {"status": "error", "detail": "Could not read feedback file."}

    if not records:
        return {"status": "no_records", "hint": "Feedback file exists but contains no parseable records."}

    vocal_ids = sorted(set(r.get("vocal_id", "unknown") for r in records))

    # ---- per-vocal_id best preset --------------------------------------------
    best_presets_by_vocal: dict[str, str | None] = {}
    for vid in vocal_ids:
        vid_records = [r for r in records if r.get("vocal_id") == vid]
        # Count "best" labels per preset for this vocal
        preset_best: dict[str, int] = {}
        for rec in vid_records:
            pname = rec.get("preset_name", "")
            label = rec.get("label", "")
            rating = rec.get("rating") or 0
            if pname not in preset_best:
                preset_best[pname] = 0
            if label == "best" or (isinstance(rating, (int, float)) and rating >= 5):
                preset_best[pname] += 1
            elif label == "good" or (isinstance(rating, (int, float)) and rating >= 4):
                preset_best[pname] += 0.5  # type: ignore[operator]
        if preset_best:
            best_presets_by_vocal[vid] = max(preset_best, key=lambda k: preset_best[k])  # type: ignore[arg-type]
        else:
            best_presets_by_vocal[vid] = None

    # ---- preferred intensity -------------------------------------------------
    intensity_votes: dict[str, float] = {}
    for rec in records:
        pname = rec.get("preset_name", "")
        intensity = _PRESET_INTENSITY.get(pname, "unknown")
        label = rec.get("label", "")
        rating = rec.get("rating") or 0
        if label == "best" or (isinstance(rating, (int, float)) and rating >= 5):
            intensity_votes[intensity] = intensity_votes.get(intensity, 0) + 1
        elif label == "good" or (isinstance(rating, (int, float)) and rating >= 4):
            intensity_votes[intensity] = intensity_votes.get(intensity, 0) + 0.5

    preferred_intensity = max(intensity_votes, key=lambda k: intensity_votes[k]) if intensity_votes else "unknown"

    # ---- preferred retune / correction ranges ---------------------------------
    best_good_presets: set[str] = set()
    for rec in records:
        label = rec.get("label", "")
        rating = rec.get("rating") or 0
        if label in ("best", "good") or (isinstance(rating, (int, float)) and rating >= 4):
            pname = rec.get("preset_name", "")
            if pname in MAINSTREAM_AUTOTUNE_PRESETS:
                best_good_presets.add(pname)

    retune_ms_values: list[int] = []
    correction_values: list[int] = []
    for pname in best_good_presets:
        pdef = MAINSTREAM_AUTOTUNE_PRESETS[pname]
        retune_ms_values.append(int(pdef.get("retune_ms_equivalent", 0)))
        correction_values.append(int(pdef.get("correction_amount", 0)))

    preferred_retune_range_ms = [min(retune_ms_values), max(retune_ms_values)] if retune_ms_values else [0, 0]
    preferred_correction_range = [min(correction_values), max(correction_values)] if correction_values else [0, 0]

    # ---- disliked artifacts ---------------------------------------------------
    artifact_counts: dict[str, int] = {}
    for rec in records:
        label = rec.get("label", "")
        if label == "too_fake":
            artifact_counts["artificial_character"] = artifact_counts.get("artificial_character", 0) + 1
        elif label == "harsh":
            artifact_counts["high_frequency_harshness"] = artifact_counts.get("high_frequency_harshness", 0) + 1
        elif label == "too_heavy":
            artifact_counts["over_processing"] = artifact_counts.get("over_processing", 0) + 1
        elif label == "too_light":
            artifact_counts["under_correction"] = artifact_counts.get("under_correction", 0) + 1

    disliked_artifacts = sorted(artifact_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        "status": "ok",
        "total_sessions": len(vocal_ids),
        "total_records": len(records),
        "preferred_intensity": preferred_intensity,
        "preferred_retune_range_ms": preferred_retune_range_ms,
        "preferred_correction_range": preferred_correction_range,
        "disliked_artifacts": [{"artifact": a, "count": c} for a, c in disliked_artifacts],
        "best_presets_by_vocal_type": best_presets_by_vocal,
        "intensity_votes": intensity_votes,
        "presets_in_preferred_range": sorted(best_good_presets) if best_good_presets else [],
    }


# ── v4.0 AI Tuning Advisor data interface ───────────────────────────────────


def _load_all_feedback_records() -> list[dict]:
    """Read every parseable line from autotune_listening.jsonl."""
    if not QUALITY_FEEDBACK_PATH.exists():
        return []
    records: list[dict] = []
    try:
        with open(QUALITY_FEEDBACK_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        logging.exception("Failed to read feedback records")
    return records


def _update_agent_inbox():
    """Write the current feedback snapshot as a Markdown task file for AI agents.

    Called automatically after every successful POST /quality-feedback.
    Produces ``agent_inbox/autotune_feedback_latest.md`` — a single file
    that a Claude/Codex agent can read to get the full picture and propose
    next tuning steps without manual curl / copy / paste.
    """
    preferences = _load_autotune_feedback_preferences()
    records = _load_all_feedback_records()
    gap = debug_gap_status()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Per-preset stats table
    preset_lines: list[str] = []
    for pname in sorted(MAINSTREAM_AUTOTUNE_PRESETS.keys()):
        pref = preferences.get(pname, {})
        pdef = MAINSTREAM_AUTOTUNE_PRESETS[pname]
        preset_lines.append(
            f"| {pname} | {pdef['preset_label']} | "
            f"{pref.get('score', 0)} | {pref.get('count', 0)} | "
            f"{pref.get('too_light_count', 0)} | {pref.get('too_fake_harsh_count', 0)} | "
            f"{pref.get('best_count', 0)} |"
        )

    # Recent 10 records
    recent = records[-10:]
    recent_lines: list[str] = []
    for r in reversed(recent):
        recent_lines.append(
            f"  - `{r.get('vocal_id','?')}` | {r.get('preset_name','?')} | "
            f"{r.get('label','?')} | rating={r.get('rating','?')} | "
            f"{r.get('timestamp_utc','?')[:19]}"
        )

    md = f"""# Auto-Tune Feedback Inbox

> Auto-generated by POST /quality-feedback.  Last updated: {now_utc}

## Status

- **Feedback file**: `{QUALITY_FEEDBACK_PATH}`
- **Total records**: {len(records)}
- **Distinct vocal sessions**: {len(set(r.get('vocal_id','') for r in records))}

## Per-Preset Statistics

| Preset | Label | Score | Count | too_light | too_fake/harsh | best |
|---|---|---|---|---|---|---|
{chr(10).join(preset_lines)}

## Gap Status

- **gap_detected**: {gap['gap_detected']}
- **melodic_trap too_light**: {gap['melodic_trap']['too_light_count']}
- **hyperpop too_fake/harsh**: {gap['hyperpop']['too_fake_harsh_count']}
- **would_recommend**: {gap['would_recommend']}

## Recent Feedback (last 10)

{chr(10).join(recent_lines) if recent_lines else '  _(no records yet)_'}

## Agent 下一步任务

请根据以上真实听感反馈，提出下一轮 Auto-Tune 参数优化建议。

约束：
1. 不要改 Beat 功能。
2. 不要重做 UI。
3. 不要删除 feedback 文件。
4. 不要建议训练模型或买插件。
5. 只输出可落地到 `MAINSTREAM_AUTOTUNE_PRESETS` 的参数调整。
6. 只输出可落地到 `_match_autotune_preset_auto()` 的匹配规则调整。
7. 如果反馈数据不足（< 8 条），请说明需要多少额外数据。

可操作的参数：
- `correction_amount` (0-100)
- `retune_ms_equivalent` (0-200) → 内部 `retune_speed` (0-100, 越高越快)
- `humanize` (0-100)
- `formant_preserve` (0-100)
- `vibrato_preserve` (0-100)
- `pitch_tracking` (relaxed / medium / fast / instant)
- `style_mode` (natural / pop / rnb / trap / robotic)

可操作的 preset 强度分组（安全切换边界）：
- 极保守：live_tracking, natural_pop
- 平衡：natural_pop, modern_pop, emotional_rnb
- 进取：modern_pop, emotional_rnb, melodic_trap, trap_polished
- 极限：melodic_trap, trap_polished, hyperpop
"""
    try:
        AGENT_INBOX_PATH.write_text(md, encoding="utf-8")
    except OSError:
        logging.exception("Failed to write agent inbox file")


@app.get("/debug/autotune-learning-dataset")
def debug_learning_dataset():
    """Return every feedback record enriched with its preset's full parameter set.

    Each record includes the original feedback fields plus the corresponding
    ``MAINSTREAM_AUTOTUNE_PRESETS`` parameters at the time of recording.
    This is the AI-ready dataset — structured, labelled, ready for ingestion
    by OpenAI / Claude / local models for pattern discovery.
    """
    records = _load_all_feedback_records()
    if not records:
        return {"status": "no_data", "record_count": 0, "dataset": []}

    dataset: list[dict] = []
    for rec in records:
        pname = rec.get("preset_name", "")
        pdef = MAINSTREAM_AUTOTUNE_PRESETS.get(pname, {})
        dataset.append({
            "vocal_id": rec.get("vocal_id", "unknown"),
            "preset_name": pname,
            "preset_label": pdef.get("preset_label", ""),
            "final_used_params": {
                "retune_ms_equivalent": pdef.get("retune_ms_equivalent"),
                "correction_amount": pdef.get("correction_amount"),
                "humanize": pdef.get("humanize"),
                "formant_preserve": pdef.get("formant_preserve"),
                "vibrato_preserve": pdef.get("vibrato_preserve"),
                "pitch_tracking": pdef.get("pitch_tracking"),
                "style_mode": PRESET_TO_STYLE.get(pname, ""),
            },
            "feedback_label": rec.get("label"),
            "rating": rec.get("rating"),
            "backing_style": rec.get("backing_style"),
            "note": rec.get("note"),
            "timestamp": rec.get("timestamp_utc"),
        })

    return {
        "status": "ok",
        "record_count": len(dataset),
        "preset_count": len(MAINSTREAM_AUTOTUNE_PRESETS),
        "description": (
            "Each record is one A/B listening feedback entry joined with the "
            "preset's full parameter set.  Feed this JSON to an LLM and ask: "
            "'Which parameter ranges predict a 'best' label?' or "
            "'What correction_amount correlates with 'too_fake' feedback?'"
        ),
        "dataset": dataset,
    }


@app.get("/debug/autotune-learning-summary")
def debug_learning_summary():
    """Aggregate statistics from all feedback records for AI-ready insights.

    Returns per-preset average scores, label distributions, and the parameter
    ranges associated with positive vs negative feedback — the kind of summary
    an AI model would generate before making tuning recommendations.
    """
    records = _load_all_feedback_records()
    if not records:
        return {"status": "no_data", "record_count": 0}

    # ---- per-preset average score -------------------------------------------
    preset_stats: dict[str, dict] = {}
    for pname in MAINSTREAM_AUTOTUNE_PRESETS:
        preset_stats[pname] = {
            "total": 0, "best": 0, "good": 0, "natural": 0,
            "too_light": 0, "too_fake": 0, "harsh": 0, "too_heavy": 0,
            "avg_rating": 0.0, "rating_sum": 0, "rating_count": 0,
        }

    for rec in records:
        pname = rec.get("preset_name", "")
        if pname not in preset_stats:
            continue
        label = rec.get("label", "")
        rating = rec.get("rating")
        preset_stats[pname]["total"] += 1
        if label in preset_stats[pname]:
            preset_stats[pname][label] += 1
        if isinstance(rating, (int, float)):
            preset_stats[pname]["rating_sum"] += rating
            preset_stats[pname]["rating_count"] += 1

    per_preset_summary: dict[str, dict] = {}
    for pname, st in preset_stats.items():
        if st["total"] == 0:
            continue
        per_preset_summary[pname] = {
            "total_feedback": st["total"],
            "positive_pct": round((st["best"] + st["good"] + st["natural"]) / st["total"] * 100, 1),
            "negative_pct": round((st["too_light"] + st["too_fake"] + st["harsh"] + st["too_heavy"]) / st["total"] * 100, 1),
            "best_count": st["best"],
            "too_light_count": st["too_light"],
            "too_fake_harsh_count": st["too_fake"] + st["harsh"],
            "avg_rating": round(st["rating_sum"] / st["rating_count"], 2) if st["rating_count"] > 0 else None,
            "preset_label": MAINSTREAM_AUTOTUNE_PRESETS[pname]["preset_label"],
        }

    # ---- most-liked parameter ranges ----------------------------------------
    liked_records = [r for r in records if r.get("label") in ("best", "good", "natural")]
    disliked_records = [r for r in records if r.get("label") in ("too_fake", "harsh", "too_heavy")]

    def _param_ranges(recs: list[dict]) -> dict:
        vals: dict[str, list] = {
            "retune_ms": [], "correction": [], "humanize": [],
            "formant": [], "vibrato": [],
        }
        for rec in recs:
            pdef = MAINSTREAM_AUTOTUNE_PRESETS.get(rec.get("preset_name", ""), {})
            if pdef:
                vals["retune_ms"].append(pdef.get("retune_ms_equivalent", 0))
                vals["correction"].append(pdef.get("correction_amount", 0))
                vals["humanize"].append(pdef.get("humanize", 0))
                vals["formant"].append(pdef.get("formant_preserve", 0))
                vals["vibrato"].append(pdef.get("vibrato_preserve", 0))
        return {
            k: {"min": min(v) if v else 0, "max": max(v) if v else 0,
                "avg": round(sum(v) / len(v), 1) if v else 0}
            for k, v in vals.items()
        }

    return {
        "status": "ok",
        "total_records": len(records),
        "total_sessions": len(set(r.get("vocal_id", "") for r in records)),
        "per_preset_summary": per_preset_summary,
        "label_distribution": {
            label: sum(1 for r in records if r.get("label") == label)
            for label in ["best", "good", "natural", "too_light", "too_fake", "harsh", "too_heavy"]
        },
        "most_liked_param_ranges": _param_ranges(liked_records),
        "most_disliked_param_ranges": _param_ranges(disliked_records),
        "ai_prompt_hint": (
            "Feed this summary to an LLM: 'Given these Auto-Tune feedback "
            "statistics, what correction_amount range is most likely to get a "
            "'best' label? Which preset should we recommend for a vocal that "
            "the user found too_fake on hyperpop and too_light on natural_pop?'"
        ),
    }


# ── v4.1 AI Tuning Advisor Prompt Export ────────────────────────────────────


@app.get("/debug/autotune-ai-prompt")
def debug_ai_prompt():
    """Generate a self-contained Chinese-language prompt for AI-assisted
    Auto-Tune parameter analysis.

    Combines the full preset library, user feedback summary, and structured
    output instructions into a single string ready to copy-paste into any
    LLM chat (OpenAI, Claude, local model, etc.).  No external API is called.
    """
    # ---- gather data --------------------------------------------------------
    records = _load_all_feedback_records()
    summary_data = debug_learning_summary()

    # ---- build preset table -------------------------------------------------
    preset_lines: list[str] = []
    for pname in ["live_tracking", "natural_pop", "modern_pop", "emotional_rnb",
                   "melodic_trap", "trap_polished", "hyperpop"]:
        p = MAINSTREAM_AUTOTUNE_PRESETS.get(pname)
        if not p:
            continue
        fb = summary_data.get("per_preset_summary", {}).get(pname, {})
        pos = fb.get("positive_pct", 0)
        neg = fb.get("negative_pct", 0)
        total = fb.get("total_feedback", 0)
        preset_lines.append(
            f"  - {pname} ({p['preset_label']}): "
            f"retune={p['retune_ms_equivalent']}ms "
            f"correction={p['correction_amount']}% "
            f"humanize={p['humanize']} "
            f"formant={p['formant_preserve']} "
            f"vibrato={p['vibrato_preserve']} "
            f"pitch_tracking={p['pitch_tracking']} "
            f"style_mode={PRESET_TO_STYLE.get(pname, '')}"
            + (f" | 反馈{total}条 正面{pos}% 负面{neg}%" if total > 0 else " | 暂无反馈")
        )

    # ---- build feedback summary text ----------------------------------------
    fb_text = ""
    if records:
        liked = summary_data.get("most_liked_param_ranges", {})
        disliked = summary_data.get("most_disliked_param_ranges", {})
        label_dist = summary_data.get("label_distribution", {})
        fb_text = f"""
## 用户历史反馈摘要

共 {summary_data.get('total_records', 0)} 条反馈，{summary_data.get('total_sessions', 0)} 个试听会话。

标签分布：
  best={label_dist.get('best', 0)} good={label_dist.get('good', 0)} natural={label_dist.get('natural', 0)}
  too_light={label_dist.get('too_light', 0)} too_fake={label_dist.get('too_fake', 0)}
  harsh={label_dist.get('harsh', 0)} too_heavy={label_dist.get('too_heavy', 0)}

用户喜欢的参数范围（best/good/natural 标签对应的 preset 参数）：
  retune_ms: {liked.get('retune_ms', {})}
  correction: {liked.get('correction', {})}
  humanize: {liked.get('humanize', {})}
  formant: {liked.get('formant', {})}
  vibrato: {liked.get('vibrato', {})}

用户不喜欢的参数范围（too_fake/harsh/too_heavy 标签对应的 preset 参数）：
  retune_ms: {disliked.get('retune_ms', {})}
  correction: {disliked.get('correction', {})}
  humanize: {disliked.get('humanize', {})}
  formant: {disliked.get('formant', {})}
  vibrato: {disliked.get('vibrato', {})}
"""
    else:
        fb_text = "\n## 用户历史反馈摘要\n\n暂无反馈数据。请先通过 A/B 听感测试收集反馈。\n"

    # ---- assemble prompt ----------------------------------------------------
    prompt = f"""# Auto-Tune 参数调优分析任务

## 项目背景

我们正在开发一个 AI 音乐创作工具的后端 Auto-Tune 引擎。系统根据上传的人声、伴奏特征、曲风（pop/trap/rnb/electronic）和调性（major/minor），自动选择最合适的 Auto-Tune 预设并应用真实音高校正。

当前引擎使用 librosa.pyin 做 F0 检测 + 分段 phase-vocoder pitch_shift 做音高修正。所有预设参数（correction_amount / retune_speed / humanize / formant_preserve / vibrato_preserve）都实际影响输出音频，不是仅展示的参数。

## 可调参数说明

| 参数 | 范围 | 含义 |
| retune_ms_equivalent | 0-200 ms | 音高修正速度，越低越快越机械 |
| correction_amount | 0-100% | 修正量，0%=不修 100%=完全拉到目标音阶 |
| humanize | 0-100 | 自然人声保留程度，越高越自然（时值/振幅抖动越大） |
| formant_preserve | 0-100 | 共振峰保留，越高干声比例越大 |
| vibrato_preserve | 0-100 | 颤音保留，越高颤音越不被修正 |
| pitch_tracking | relaxed/medium/fast/instant | 音高追踪速度 |
| style_mode | natural/pop/rnb/trap/robotic | 引擎模式（控制窗口大小、量化策略、滤波器、低切等） |

注：retune_speed（内部 0-100，越高越快）和 retune_ms_equivalent（ms，越低越快）是同一参数的两个表示。引擎内部使用 retune_speed，用户界面展示 retune_ms_equivalent（更接近 Antares Auto-Tune 的 Retune Speed 概念）。

## 当前 Preset 列表

{chr(10).join(preset_lines)}

## Preset 强度分组（用于安全切换）

- 极保守：live_tracking, natural_pop
- 平衡：natural_pop, modern_pop, emotional_rnb
- 进取：modern_pop, emotional_rnb, melodic_trap, trap_polished
- 极限：melodic_trap, trap_polished, hyperpop
{fb_text}
## 分析任务

请根据以上数据，给出以下建议：
1. **推荐保留**：哪些 preset 反馈良好，应该保留不变？
2. **增强/减弱**：哪些 preset 的参数需要调整？具体怎么调？
3. **新增中间 profile**：如果现有 preset 之间有空白区间，建议新增什么参数的中间 preset？
4. **推荐参数范围**：根据用户偏好，给出一组全局推荐参数范围。
5. **规则建议**：有没有可以加入自动匹配逻辑的规则？（例如 "trap 曲风 + 小调 + 反馈偏好中等修音 → 自动倾向 melodic_trap"）

注意：
- 只建议我们可以实现的参数调整方案，不要推荐买插件或训练模型。
- 参数值必须在合理范围内（retune_ms: 0-200, correction: 0-100, humanize: 0-100, formant: 0-100, vibrato: 0-100）。
- preset 之间的切换只能在同强度分组内进行。
- 如果反馈数据不足，请说明需要多少额外数据才能给出可靠建议。
"""

    return {
        "purpose": "Ask an AI model to analyze listening feedback and suggest Auto-Tune parameter changes",
        "prompt": prompt,
        "prompt_length_chars": len(prompt),
        "has_feedback_data": len(records) > 0,
        "usage": (
            "1. Copy the 'prompt' field.  "
            "2. Paste into any LLM chat (ChatGPT, Claude, etc.).  "
            "3. The AI will return structured tuning recommendations.  "
            "4. Apply the parameter suggestions to MAINSTREAM_AUTOTUNE_PRESETS in app.py."
        ),
    }
