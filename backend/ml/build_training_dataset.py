"""Build a structured ML training dataset from A/B listening feedback.

Reads ``backend/feedback/autotune_listening.jsonl``, joins each record with
the corresponding ``MAINSTREAM_AUTOTUNE_PRESETS`` parameters, and writes
one enriched JSONL line per record to ``backend/ml/data/autotune_training_data.jsonl``.

Usage::

    python backend/ml/build_training_dataset.py

No external API calls — pure data preparation.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the backend package is importable.
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR.parent))  # project root

from backend.app import MAINSTREAM_AUTOTUNE_PRESETS, PRESET_TO_STYLE

FEEDBACK_PATH = _BACKEND_DIR / "feedback" / "autotune_listening.jsonl"
OUTPUT_DIR = _THIS_DIR / "data"
OUTPUT_PATH = OUTPUT_DIR / "autotune_training_data.jsonl"

# Scoring mirroring _load_autotune_feedback_preferences() in app.py.
LABEL_SCORE = {
    "best": 3,
    "good": 1,
    "natural": 1,
    "too_light": -1,
    "too_fake": -2,
    "too_heavy": -2,
    "harsh": -2,
}


def _load_records() -> list[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    records: list[dict] = []
    with open(FEEDBACK_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def build() -> int:
    """Return the number of training samples written."""
    records = _load_records()
    if not records:
        print(f"[{datetime.now(timezone.utc).isoformat()}] No feedback records found — empty dataset.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text("", encoding="utf-8")
        return 0

    samples: list[dict] = []
    positive_labels = {"best", "good", "natural"}
    negative_labels = {"too_fake", "harsh", "too_heavy"}

    for rec in records:
        pname = rec.get("preset_name", "")
        pdef = MAINSTREAM_AUTOTUNE_PRESETS.get(pname, {})
        if not pdef:
            continue

        label = rec.get("label", "")
        rating = rec.get("rating")

        # Compute preference_score (same rules as app.py).
        score = LABEL_SCORE.get(label, 0)
        if isinstance(rating, (int, float)):
            if rating >= 5:
                score = max(score, 3)
            elif rating >= 4:
                score = max(score, 1)

        sample = {
            "vocal_id": rec.get("vocal_id", "unknown"),
            "preset_name": pname,
            "preset_label": pdef.get("preset_label", ""),
            "feedback_label": label,
            "rating": rating,
            # Feature columns (what the ML model would learn from).
            "retune_ms_equivalent": pdef.get("retune_ms_equivalent"),
            "correction_amount": pdef.get("correction_amount"),
            "humanize": pdef.get("humanize"),
            "formant_preserve": pdef.get("formant_preserve"),
            "vibrato_preserve": pdef.get("vibrato_preserve"),
            "pitch_tracking": pdef.get("pitch_tracking"),
            "style_mode": PRESET_TO_STYLE.get(pname, ""),
            "backing_style": rec.get("backing_style"),
            # Label columns (what the ML model would predict / optimize for).
            "positive": label in positive_labels,
            "negative": label in negative_labels,
            "preference_score": score,
            "timestamp": rec.get("timestamp_utc"),
        }
        samples.append(sample)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        for s in samples:
            json.dump(s, fh, ensure_ascii=False)
            fh.write("\n")

    positive_count = sum(1 for s in samples if s["positive"])
    negative_count = sum(1 for s in samples if s["negative"])
    print(
        f"[{datetime.now(timezone.utc).isoformat()}] "
        f"Wrote {len(samples)} samples to {OUTPUT_PATH} "
        f"(positive={positive_count}, negative={negative_count})"
    )
    return len(samples)


if __name__ == "__main__":
    build()
