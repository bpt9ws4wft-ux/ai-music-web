"""Train a lightweight, interpretable preference model from A/B feedback.

Reads ``backend/ml/data/autotune_training_data.jsonl``, computes per-preset
aggregate scores and global liked/disliked parameter ranges, and writes a
JSON model file to ``backend/ml/models/autotune_preference_model.json``.

This is NOT deep learning — it's pure statistical aggregation.  The output
is human-readable and can be used directly by the auto-mode matcher to
rank presets by historical user preference.

Usage::

    python backend/ml/train_preference_model.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR.parent))

from backend.app import MAINSTREAM_AUTOTUNE_PRESETS

TRAINING_DATA_PATH = _THIS_DIR / "data" / "autotune_training_data.jsonl"
MODELS_DIR = _THIS_DIR / "models"
MODEL_PATH = MODELS_DIR / "autotune_preference_model.json"


def _load_samples() -> list[dict]:
    if not TRAINING_DATA_PATH.exists():
        return []
    samples: list[dict] = []
    with open(TRAINING_DATA_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return samples


def train() -> dict:
    """Return the trained model dict (also writes to disk)."""
    samples = _load_samples()
    now_utc = datetime.now(timezone.utc).isoformat()

    if not samples:
        model = {
            "model_version": "1.0.0",
            "model_type": "statistical_preference_aggregator",
            "trained_at": now_utc,
            "training_sample_count": 0,
            "preset_scores": {},
            "global_liked_param_ranges": {},
            "global_disliked_param_ranges": {},
            "confidence": 0.0,
            "note": "No training data available. Model is empty.",
        }
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{now_utc}] Empty model written (0 samples)")
        return model

    # ---- per-preset scores -------------------------------------------------
    preset_accum: dict[str, dict] = {}
    for pname in MAINSTREAM_AUTOTUNE_PRESETS:
        preset_accum[pname] = {
            "score_sum": 0.0, "score_count": 0,
            "positive_count": 0, "negative_count": 0,
        }

    for s in samples:
        pname = s.get("preset_name", "")
        if pname not in preset_accum:
            continue
        score = s.get("preference_score", 0)
        preset_accum[pname]["score_sum"] += score
        preset_accum[pname]["score_count"] += 1
        if s.get("positive"):
            preset_accum[pname]["positive_count"] += 1
        if s.get("negative"):
            preset_accum[pname]["negative_count"] += 1

    preset_scores: dict[str, dict] = {}
    for pname, acc in preset_accum.items():
        n = acc["score_count"]
        if n == 0:
            continue
        avg = round(acc["score_sum"] / n, 2)
        pos_rate = round(acc["positive_count"] / n, 2)
        neg_rate = round(acc["negative_count"] / n, 2)
        # Confidence: higher with more samples, lower with mixed signals.
        sample_conf = min(1.0, n / 10.0)  # max confidence at ≥10 samples
        consistency = 1.0 - abs(pos_rate - neg_rate)  # 1.0 if all agree, 0.0 if split
        conf = round(sample_conf * consistency, 2)

        preset_scores[pname] = {
            "preset_label": MAINSTREAM_AUTOTUNE_PRESETS[pname]["preset_label"],
            "avg_score": avg,
            "positive_rate": pos_rate,
            "negative_rate": neg_rate,
            "sample_count": n,
            "confidence": conf,
        }

    # ---- global liked / disliked parameter ranges --------------------------
    liked = [s for s in samples if s.get("positive")]
    disliked = [s for s in samples if s.get("negative")]

    def _ranges(recs: list[dict]) -> dict:
        keys = ["retune_ms_equivalent", "correction_amount", "humanize",
                "formant_preserve", "vibrato_preserve"]
        result: dict[str, dict] = {}
        for k in keys:
            vals = [r[k] for r in recs if r.get(k) is not None]
            result[k] = {
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "avg": round(sum(vals) / len(vals), 1) if vals else None,
            }
        return result

    global_liked = _ranges(liked)
    global_disliked = _ranges(disliked)

    # ---- overall confidence ------------------------------------------------
    total_n = len(samples)
    overall_conf = round(min(1.0, total_n / 15.0), 2)  # ≥15 samples → full confidence

    model = {
        "model_version": "1.0.0",
        "model_type": "statistical_preference_aggregator",
        "description": (
            "Lightweight interpretable preference model.  Each preset is scored "
            "by aggregating historical A/B listening feedback.  Global liked/"
            "disliked parameter ranges indicate which Auto-Tune settings users "
            "consistently prefer or reject.  This is NOT deep learning — it's "
            "pure statistical aggregation suitable for cold-start recommendations."
        ),
        "trained_at": now_utc,
        "training_sample_count": total_n,
        "preset_scores": preset_scores,
        "global_liked_param_ranges": global_liked,
        "global_disliked_param_ranges": global_disliked,
        "confidence": overall_conf,
        "usage": (
            "Read preset_scores to rank presets by avg_score.  "
            "Use global_liked_param_ranges to constrain parameter tuning.  "
            "Low confidence (< 0.5) means not enough data — fall back to rule-based matching."
        ),
    }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{now_utc}] Model trained: {total_n} samples, {len(preset_scores)} presets scored, confidence={overall_conf}")
    return model


if __name__ == "__main__":
    train()
