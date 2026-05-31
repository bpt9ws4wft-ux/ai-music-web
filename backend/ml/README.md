# Auto-Tune ML Dataset Preparation Layer

Prepares structured, labelled training data from A/B listening feedback
for future machine learning models.  **No training happens here** — this
is purely the data preparation step.

## Quick Start

```bash
python backend/ml/build_training_dataset.py
```

Output: `backend/ml/data/autotune_training_data.jsonl`

## Training Sample Schema

Each JSONL line contains:

| Field | Type | Description |
|---|---|---|
| `vocal_id` | string | Vocal session identifier |
| `preset_name` | string | One of 7 preset IDs |
| `preset_label` | string | Chinese display name |
| `feedback_label` | string | best / good / natural / too_light / too_fake / harsh / too_heavy |
| `rating` | int\|null | 1–5 numerical rating |
| `retune_ms_equivalent` | int | Retune speed in ms |
| `correction_amount` | int | Correction percentage 0–100 |
| `humanize` | int | Humanize 0–100 |
| `formant_preserve` | int | Formant preservation 0–100 |
| `vibrato_preserve` | int | Vibrato preservation 0–100 |
| `pitch_tracking` | string | relaxed / medium / fast / instant |
| `style_mode` | string | natural / pop / rnb / trap / robotic |
| `backing_style` | string\|null | pop / trap / rnb / electronic / unknown |
| `positive` | bool | True if label in {best, good, natural} |
| `negative` | bool | True if label in {too_fake, harsh, too_heavy} |
| `preference_score` | int | Numeric score (−2 to +3) |
| `timestamp` | string | ISO 8601 UTC |

## How This Dataset Will Be Used for ML

### 1. Recommendation Model (classification)

**Input features**: `correction_amount`, `retune_ms_equivalent`, `humanize`,
`formant_preserve`, `vibrato_preserve`, `pitch_tracking`, `style_mode`,
`backing_style`

**Target label**: `feedback_label` (multi-class: best / too_fake / too_light / ...)

**Goal**: Given a vocal's audio quality + backing style, predict which preset
parameters are most likely to receive a "best" label from this user.

### 2. Parameter Optimization Model (regression)

**Input features**: `preset_name`, `backing_style`

**Target values**: `correction_amount`, `retune_ms_equivalent`, `humanize`

**Goal**: Learn the optimal parameter ranges for each user × backing combination.

### 3. Cold-start Strategy

When `record_count < 20`: use the rule-based preset matching (current).
When `record_count ≥ 20`: train a lightweight model (e.g. RandomForest or
logistic regression) and use it to rank presets before the rule-based matcher.

## Files NOT Committed to Git

- `backend/ml/data/` — contains real user feedback data
- `backend/feedback/autotune_listening.jsonl` — raw feedback records

## Preference Model (v4.4)

A lightweight, interpretable preference learner that aggregates historical
feedback into per-preset scores and global parameter ranges.

**This is NOT deep learning.**  It's pure statistical aggregation — no
neural networks, no gradient descent, no external libraries beyond the
Python standard library.

### Training

```bash
# Step 1: build the training dataset from feedback
python backend/ml/build_training_dataset.py

# Step 2: train the preference model
python backend/ml/train_preference_model.py
```

Output: `backend/ml/models/autotune_preference_model.json`

### Model Fields

| Field | Type | Description |
|---|---|---|
| `preset_scores.<name>.avg_score` | float | Mean preference_score (−2 to +3) |
| `preset_scores.<name>.positive_rate` | float | Fraction of best/good/natural labels |
| `preset_scores.<name>.negative_rate` | float | Fraction of too_fake/harsh/too_heavy |
| `preset_scores.<name>.sample_count` | int | Number of feedback records |
| `preset_scores.<name>.confidence` | float | 0–1, higher with more consistent data |
| `global_liked_param_ranges` | dict | {min, max, avg} of each parameter for liked presets |
| `global_disliked_param_ranges` | dict | Same for disliked presets |
| `confidence` | float | Overall model confidence (≥0.5 = usable) |

### Confidence Scoring

- Sample count weight: `min(1.0, n / 10)` — full weight at ≥10 samples/preset
- Consistency weight: `1.0 - |positive_rate - negative_rate|` — high when feedback agrees
- Overall: `min(1.0, total_samples / 15)` — full confidence at ≥15 total samples
- Low confidence (< 0.5) → fall back to rule-based matching

### Debug Endpoint

```bash
curl http://127.0.0.1:8000/debug/ml-preference-model-summary
```

### How the Model Will Be Used (Future)

When `confidence ≥ 0.5`:
- Auto-mode matcher reads `preset_scores` and boosts the `confidence`
  of highly-scored presets during preset selection.
- Parameter tuning (v3.8) reads `global_liked_param_ranges` to constrain
  adjustments within proven-safe ranges.

When `confidence < 0.5`:
- Fall back to rule-based matching (current behavior).

## Running the Debug Endpoint

```bash
curl http://127.0.0.1:8000/debug/ml-training-dataset-summary
```

Returns sample count, positive/negative split, and per-preset distribution.
