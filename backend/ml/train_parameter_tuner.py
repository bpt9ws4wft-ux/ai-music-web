"""Train a parameter-level preference model from A/B feedback.

Reads ``backend/ml/data/autotune_training_data.jsonl``, groups samples by
feedback label and preset, and computes:

- Per-label parameter distributions (e.g. what correction_amount leads to
  "too_light"?)
- Per-preset adjustment suggestions (e.g. melodic_trap should increase
  correction_amount by 6 because it's often labelled "too_light")
- Liked / disliked parameter ranges

This is NOT deep learning — it's pure statistical aggregation.  The output
is human-readable JSON that can be used to inform manual parameter tuning
or future automated adjustment rules.

Usage::

    python backend/ml/train_parameter_tuner.py
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
MODEL_PATH = MODELS_DIR / "autotune_parameter_tuner.json"

# Parameters we track for tuning.
TRACKED_PARAMS = [
    "correction_amount", "retune_ms_equivalent", "humanize",
    "formant_preserve", "vibrato_preserve",
]


def train() -> dict:
    """Return the trained model dict (also writes to disk)."""
    if not TRAINING_DATA_PATH.exists():
        model = _empty_model()
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        return model

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

    if not samples:
        model = _empty_model()
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        return model

    now_utc = datetime.now(timezone.utc).isoformat()

    # ---- per-label parameter ranges ----------------------------------------
    label_groups: dict[str, list[dict]] = {}
    for s in samples:
        label = s.get("feedback_label", "")
        if label not in label_groups:
            label_groups[label] = []
        label_groups[label].append(s)

    def _param_stats(recs: list[dict]) -> dict:
        result: dict[str, dict] = {}
        for pk in TRACKED_PARAMS:
            vals = [r[pk] for r in recs if r.get(pk) is not None]
            result[pk] = {
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "avg": round(sum(vals) / len(vals), 1) if vals else None,
                "count": len(vals),
            }
        return result

    label_param_ranges: dict[str, dict] = {}
    for label, recs in label_groups.items():
        label_param_ranges[label] = _param_stats(recs)

    # ---- liked / disliked ranges -------------------------------------------
    positive_labels = {"best", "good", "natural"}
    negative_labels = {"too_fake", "harsh", "too_heavy"}
    liked = [s for s in samples if s.get("feedback_label") in positive_labels]
    disliked = [s for s in samples if s.get("feedback_label") in negative_labels]
    liked_ranges = _param_stats(liked)
    disliked_ranges = _param_stats(disliked)

    # ---- per-preset adjustment suggestions ---------------------------------
    preset_adjustments: dict[str, dict] = {}
    for pname in MAINSTREAM_AUTOTUNE_PRESETS:
        pdef = MAINSTREAM_AUTOTUNE_PRESETS[pname]
        p_samples = [s for s in samples if s.get("preset_name") == pname]
        if not p_samples:
            continue

        label_counts: dict[str, int] = {}
        for s in p_samples:
            lbl = s.get("feedback_label", "")
            label_counts[lbl] = label_counts.get(lbl, 0) + 1

        total = len(p_samples)
        too_light_n = label_counts.get("too_light", 0)
        too_fake_n = label_counts.get("too_fake", 0) + label_counts.get("harsh", 0) + label_counts.get("too_heavy", 0)
        best_n = label_counts.get("best", 0)

        adjust = {"preset_name": pname, "preset_label": pdef["preset_label"],
                  "sample_count": total, "best_count": best_n,
                  "too_light_count": too_light_n, "too_fake_harsh_count": too_fake_n}

        # Only suggest adjustments if we have meaningful signal.
        if total < 2:
            adjust["suggested_correction_delta"] = 0
            adjust["suggested_retune_ms_delta"] = 0
            adjust["suggested_humanize_delta"] = 0
            adjust["reason"] = "样本不足，无调整建议"
        elif too_light_n > too_fake_n and too_light_n >= total * 0.5:
            # Consistently too_light → need stronger correction
            adjust["suggested_correction_delta"] = min(10, too_light_n * 3)
            adjust["suggested_retune_ms_delta"] = -min(3, too_light_n)
            adjust["suggested_humanize_delta"] = -min(8, too_light_n * 2)
            adjust["reason"] = f"多次标记 too_light ({too_light_n}/{total}) → 建议增强修正"
        elif too_fake_n > too_light_n and too_fake_n >= total * 0.5:
            # Consistently too_fake/harsh → need softer approach
            adjust["suggested_correction_delta"] = -min(10, too_fake_n * 3)
            adjust["suggested_retune_ms_delta"] = min(3, too_fake_n)
            adjust["suggested_humanize_delta"] = min(8, too_fake_n * 2)
            adjust["reason"] = f"多次标记 too_fake/harsh ({too_fake_n}/{total}) → 建议减弱修正"
        elif best_n >= total * 0.6:
            adjust["suggested_correction_delta"] = 0
            adjust["suggested_retune_ms_delta"] = 0
            adjust["suggested_humanize_delta"] = 0
            adjust["reason"] = f"多数反馈正面 ({best_n}/{total}) → 建议保持当前参数"
        else:
            adjust["suggested_correction_delta"] = 0
            adjust["suggested_retune_ms_delta"] = 0
            adjust["suggested_humanize_delta"] = 0
            adjust["reason"] = "反馈信号不明确，保持当前参数"

        preset_adjustments[pname] = adjust

    # ---- v5.2: tuner feedback reverse correction -----------------------------
    # Read tuner_better / tuner_same / tuner_worse labels and adjust suggestions.
    tuner_feedback: dict[str, dict] = {}
    for s in samples:
        label = s.get("feedback_label", "")
        if label in ("tuner_better", "tuner_same", "tuner_worse"):
            pname = s.get("preset_name", "")
            if pname not in tuner_feedback:
                tuner_feedback[pname] = {"better": 0, "same": 0, "worse": 0}
            key = {"tuner_better": "better", "tuner_same": "same", "tuner_worse": "worse"}[label]
            tuner_feedback[pname][key] += 1

    accepted_adjustments: dict[str, dict] = {}
    rejected_adjustments: dict[str, dict] = {}
    low_impact_adjustments: dict[str, dict] = {}
    tuner_feedback_summary: dict[str, dict] = {}

    for pname, adj in preset_adjustments.items():
        tf = tuner_feedback.get(pname)
        if not tf or adj.get("suggested_correction_delta", 0) == 0:
            continue

        better_n = tf.get("better", 0)
        same_n = tf.get("same", 0)
        worse_n = tf.get("worse", 0)
        total_tuner = better_n + same_n + worse_n

        summary = {"better": better_n, "same": same_n, "worse": worse_n, "total": total_tuner}

        if total_tuner == 0:
            summary["status"] = "no_tuner_feedback"
            summary["note"] = "无参数学习验证反馈，建议保持原有方向"
        elif better_n > worse_n:
            # Tuner feedback confirms the direction.
            adj["adjustment_confidence"] = min(1.0, 0.5 + better_n * 0.15)
            if better_n >= 2:
                # Amplify slightly
                amp = min(1.3, 1.0 + better_n * 0.1)
                adj["suggested_correction_delta"] = int(adj["suggested_correction_delta"] * amp)
                adj["suggested_retune_ms_delta"] = int(adj["suggested_retune_ms_delta"] * amp)
                adj["suggested_humanize_delta"] = int(adj["suggested_humanize_delta"] * amp)
                adj["reason"] += f"；经 {better_n} 次 tuner_better 验证，增强建议幅度"
            else:
                adj["reason"] += f"；经 {better_n} 次 tuner_better 验证，保留方向"
            summary["status"] = "accepted"
            summary["note"] = f"tuner_better ({better_n}) > tuner_worse ({worse_n})，保留并增强"
            accepted_adjustments[pname] = {
                "preset_name": pname, "preset_label": adj.get("preset_label", ""),
                "better_count": better_n, "worse_count": worse_n,
                "correction_delta": adj["suggested_correction_delta"],
                "retune_ms_delta": adj["suggested_retune_ms_delta"],
                "humanize_delta": adj["suggested_humanize_delta"],
            }
        elif worse_n > better_n:
            # Tuner feedback rejects the direction.
            adj["adjustment_confidence"] = max(0.1, 1.0 - worse_n * 0.3)
            if worse_n >= 2:
                # Roll back
                adj["suggested_correction_delta"] = 0
                adj["suggested_retune_ms_delta"] = 0
                adj["suggested_humanize_delta"] = 0
                adj["reason"] += f"；经 {worse_n} 次 tuner_worse 验证，回退调整建议"
                adj["rejected_adjustment"] = True
            else:
                adj["suggested_correction_delta"] = int(adj["suggested_correction_delta"] * 0.5)
                adj["suggested_retune_ms_delta"] = int(adj["suggested_retune_ms_delta"] * 0.5)
                adj["suggested_humanize_delta"] = int(adj["suggested_humanize_delta"] * 0.5)
                adj["reason"] += f"；经 {worse_n} 次 tuner_worse，减弱调整幅度"
            summary["status"] = "rejected"
            summary["note"] = f"tuner_worse ({worse_n}) > tuner_better ({better_n})，回退或减弱"
            rejected_adjustments[pname] = {
                "preset_name": pname, "preset_label": adj.get("preset_label", ""),
                "better_count": better_n, "worse_count": worse_n,
                "rolled_back": worse_n >= 2,
            }
        else:
            # same_n is dominant or tied
            adj["adjustment_confidence"] = 0.3
            adj["suggested_correction_delta"] = int(adj["suggested_correction_delta"] * 0.4)
            adj["suggested_retune_ms_delta"] = int(adj["suggested_retune_ms_delta"] * 0.4)
            adj["suggested_humanize_delta"] = int(adj["suggested_humanize_delta"] * 0.4)
            adj["reason"] += f"；经 {same_n} 次 tuner_same，降低调整幅度（差异不明显）"
            adj["low_impact_adjustment"] = True
            summary["status"] = "low_impact"
            summary["note"] = f"tuner_same ({same_n}) 较多，听不出差异，降低幅度"
            low_impact_adjustments[pname] = {
                "preset_name": pname, "preset_label": adj.get("preset_label", ""),
                "same_count": same_n, "total": total_tuner,
            }

        summary["adjustment_confidence"] = adj.get("adjustment_confidence", 0.5)
        tuner_feedback_summary[pname] = summary

    # ---- overall confidence ------------------------------------------------
    total_n = len(samples)
    presets_with_signal = sum(1 for a in preset_adjustments.values()
                              if a.get("sample_count", 0) >= 3)
    confidence = round(min(1.0, presets_with_signal / max(1, len(MAINSTREAM_AUTOTUNE_PRESETS))), 2)
    adj_conf = round(sum(a.get("adjustment_confidence", 0.5)
                         for a in preset_adjustments.values()
                         if a.get("suggested_correction_delta", 0) != 0)
                     / max(1, sum(1 for a in preset_adjustments.values()
                                  if a.get("suggested_correction_delta", 0) != 0)), 2)

    model = {
        "model_version": "1.0.0",
        "model_type": "statistical_parameter_tuner",
        "description": (
            "Learns per-label and per-preset parameter adjustment directions "
            "from A/B listening feedback.  Does NOT auto-apply adjustments — "
            "human review is recommended before changing any preset values."
        ),
        "trained_at": now_utc,
        "sample_count": total_n,
        "liked_param_ranges": liked_ranges,
        "disliked_param_ranges": disliked_ranges,
        "label_param_ranges": label_param_ranges,
        "preset_specific_adjustments": preset_adjustments,
        "tuner_feedback_summary": tuner_feedback_summary,
        "accepted_adjustments": accepted_adjustments,
        "rejected_adjustments": rejected_adjustments,
        "low_impact_adjustments": low_impact_adjustments,
        "adjustment_confidence": adj_conf,
        "confidence": confidence,
        "note": (
            "These are statistical suggestions only.  Adjustments have NOT "
            "been automatically applied to avoid degrading output quality "
            "without human review."
        ),
    }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{now_utc}] Parameter tuner trained: {total_n} samples, "
          f"{len(preset_adjustments)} presets with adjustments, confidence={confidence}")
    return model


def _empty_model() -> dict:
    return {
        "model_version": "1.0.0",
        "model_type": "statistical_parameter_tuner",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": 0,
        "liked_param_ranges": {},
        "disliked_param_ranges": {},
        "label_param_ranges": {},
        "preset_specific_adjustments": {},
        "tuner_feedback_summary": {},
        "accepted_adjustments": {},
        "rejected_adjustments": {},
        "low_impact_adjustments": {},
        "adjustment_confidence": 0.0,
        "confidence": 0.0,
        "note": "No training data available.",
    }


if __name__ == "__main__":
    train()
