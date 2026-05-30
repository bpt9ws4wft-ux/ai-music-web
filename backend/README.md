# AI Music Web Backend v3.2 (mainstream Auto-Tune parameter library)

Converts any supported audio file to a normalised WAV (16-bit, 44.1 kHz,
mono), applies **real Auto-Tune pitch correction** using a **mainstream
preset library** (6 curated styles with rule-based matching), generates
**intelligent Beat-generation parameters**, and returns audio analysis,
parameter sync, an engine-ready Auto-Tune profile, and a Beat-generation
blueprint.  Pitch correction is an MVP; Beat generation is parameter-only.

## Prerequisites

### 1. Python 3.10+

> **Python 3.13 note**: Python 3.13 removed the built-in `audioop` module.
> pydub depends on it, so `audioop-lts` (a drop-in replacement) is included
> in `requirements.txt` for Python ≥ 3.13.  No extra steps needed — just
> `pip install -r backend\requirements.txt` as usual.

### 2. ffmpeg

**pydub** uses ffmpeg under the hood for decoding and encoding audio.

**Windows** — the easiest way:

```powershell
winget install ffmpeg
```

Or download from https://ffmpeg.org/download.html and add the `bin`
folder to your system `PATH`.  Restart your terminal after installing.

**macOS**:

```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**:

```bash
sudo apt install ffmpeg
```

Verify it works:

```bash
ffmpeg -version
```

## Start Locally

From the project root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

## Test the Backend

Open your browser and visit:

- Health check: http://127.0.0.1:8000/health
- Interactive API docs: http://127.0.0.1:8000/docs

### Upload a file with curl

```bash
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.mp3" \
  -o converted.wav
```

The downloaded `converted.wav` will be a 16-bit 44.1 kHz mono WAV.

### Audio Analysis Response Headers

The `/process-vocal` response includes these custom headers with analysis
data measured from the **original** uploaded audio (before conversion):

| Header | Example | Meaning |
|---|---|---|
| `X-Duration-Seconds` | `3.52` | Length in seconds |
| `X-Sample-Rate` | `44100` | Original sample rate (Hz) |
| `X-Channels` | `2` | Original channel count |
| `X-Peak-dBFS` | `-1.23` | Highest sample level |
| `X-Average-dBFS` | `-18.45` | RMS loudness |
| `X-Too-Quiet` | `false` | `true` when average < −30 dBFS |
| `X-Clipped-Risk` | `false` | `true` when peak > −0.3 dBFS |

To see them, use `curl -v`:

```bash
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.mp3" \
  -o converted.wav
```

### Processing Settings

The `/process-vocal` endpoint also accepts optional form fields that are
echoed back in the `X-Processing-Settings` header (URL-encoded JSON):

| Form Field | Default | Description |
|---|---|---|
| `autotune_strength` | `40` | Auto-Tune intensity (0–100) |
| `autotune_mode` | `manual` | `manual` (slider-driven) or `auto` (system chooses) |
| `key` | `C` | Root key (C–B) |
| `scale` | `major` | Scale type (`major` or `minor`) |
| `beat_style` | `清爽电子` | Selected beat style |
| `backing_style` | (empty) | Backing-track style from `/analyze-backing-track` (v2.8) |
| `backing_energy` | (empty) | Backing-track energy level (v2.8) |
| `backing_bass` | (empty) | Backing-track bass level (v2.8) |
| `backing_brightness` | (empty) | Backing-track brightness (v2.8) |

Example with all parameters:

```bash
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.mp3" \
  -F "autotune_strength=60" \
  -F "autotune_mode=auto" \
  -F "key=D" \
  -F "scale=minor" \
  -F "beat_style=沉浸 Trap" \
  -o converted.wav
```

The `X-Processing-Settings` response header will contain a URL-encoded
JSON object mirroring the received parameters.

### Auto-Tune Profile Header

The response also includes `X-Autotune-Profile`, a URL-encoded JSON object
with engine-ready parameters derived from the **mainstream preset library**
and fine-tuned by audio quality analysis:

| Field | Example | Description |
|---|---|---|
| `mode` | `auto` | `manual` or `auto` (v2.8) |
| `preset_name` | `melodic_trap` | Matched preset ID (v3.2) |
| `preset_label` | `Trap 强修` | Chinese preset label |
| `suitable_for` | `["Trap","Drill"]` | Genres this preset is designed for |
| `preset_source` | `auto_adaptation` | `mainstream_rule_preset` or `auto_adaptation` |
| `confidence` | `78` | 0–100, match confidence |
| `style_mode` | `trap` | Legacy engine mode (`natural` / `pop` / `rnb` / `trap` / `robotic`) |
| `style_mode_label` | `Trap` | Chinese label for the engine mode |
| `retune_speed` | `82` | 0–100, higher = faster pitch snap |
| `retune_ms_equivalent` | `8` | Approximate Antares-style Retune Speed in ms (lower = faster) |
| `correction_amount` | `85` | 0–100, how much correction to apply |
| `humanize` | `25` | 0–100, timing jitter for natural feel |
| `formant_preserve` | `45` | 0–100, keep original vocal character |
| `vibrato_preserve` | `28` | 0–100, keep natural vibrato |
| `flex_tune_like` | `Flex Tune ~15%` | Human-readable Flex Tune analogy |
| `pitch_tracking` | `fast` | Pitch tracking aggression: relaxed / medium / fast / instant |
| `best_for` | `说唱、Trap、Drill` | Best-use scenarios for this preset |
| `risk` | `中 — 快速修正...` | Risk assessment and caveats |
| `target_key` | `C` | Target root key |
| `target_scale` | `major` | Target scale |
| `target_scale_label` | `大调` | Chinese label for the scale |
| `vocal_quality` | `normal` | Quality assessment |
| `reason` | (string) | Why these parameters were chosen |
| `next_step` | (string) | Suggested next action for the user |
| `adaptation_inputs` | (object) | v3.0 dual-input: `vocal`, `style_source`, `backing` sub-fields |
| `adaptation_summary` | (string) | v2.8: `仅人声` / `人声 + 手动曲风` / `人声 + 伴奏分析` |
| `processing_intensity` | `medium` | v3.3: `low` / `medium` / `high` — how aggressive the processing is |
| `applied_pitch_correction` | `true` | v3.3: always `true` — confirms real pitch correction was applied |
| `processing_summary` | (string) | v3.3: human-readable description of the processing pipeline |

### Mainstream Auto-Tune Parameter Library (v3.2)

Six curated presets targeting real-world "好听、有质感" Auto-Tune sounds.
Each preset includes `retune_ms_equivalent` (Antares-style ms, lower=faster)
alongside the internal `retune_speed` (0-100, higher=faster) used by the engine.

| Preset | retune (ms) | correction | humanize | formant | vibrato | Pitch Track | Best For |
|---|---|---|---|---|---|---|---|
| `live_tracking` 现场录音 | ~130 ms | 18 % | 98 | 96 | 98 | relaxed | 现场、古典、播客 |
| `natural_pop` 自然流行 | ~90 ms | 28 % | 92 | 90 | 92 | relaxed | 民谣、唱作人、不插电 |
| `modern_pop` 现代流行 | ~26 ms | 60 % | 55 | 70 | 62 | medium | 流行、K-Pop、电子流行 |
| `emotional_rnb` 情绪 R&B | ~58 ms | 42 % | 84 | 84 | 90 | relaxed | R&B、Soul、转音密集 |
| `melodic_trap` 旋律 Trap | ~8 ms | 78 % | 30 | 48 | 35 | fast | 说唱、Trap、Drill |
| `hyperpop` Hyperpop | ~0 ms | 98 % | 2 | 10 | 5 | instant | 实验电子、创意效果 |

### Preset Matching Rules (v3.2)

**Manual mode** (`autotune_mode=manual`):

| Priority | Condition | Result |
|---|---|---|
| 1 | `too_quiet` (avg < −30 dBFS) | `live_tracking` |
| 2 | `clipped_risk` (peak > −0.3 dBFS) | `natural_pop` |
| 3 | `autotune_strength` > 88 | `hyperpop` |
| 4 | strength > 75 | `melodic_trap` |
| 5 | `beat_style` contains "Trap" | `melodic_trap` |
| 6 | `beat_style` contains "R&B" | `emotional_rnb` |
| 7 | strength 35–75 (default) | `modern_pop` |
| 8 | strength 15–35 | `natural_pop` |
| 9 | strength < 15 | `live_tracking` |

**Auto mode** (`autotune_mode=auto`):

| Input | Result |
|---|---|
| Backing hint = `trap` | `melodic_trap` |
| Backing hint = `rnb` | `emotional_rnb` |
| Backing hint = `electronic` + high pref | `hyperpop` |
| Backing hint = `electronic` / `pop` | `modern_pop` |
| Beat style "Trap" | `melodic_trap` |
| Beat style "R&B" | `emotional_rnb` |
| High pref (> 80) no genre | `hyperpop` |
| Medium pref (52–80) | `modern_pop` |
| Low pref (22–52) | `natural_pop` |
| Very low pref (< 22) | `live_tracking` |

**Audio-quality adjustments (both modes):**
- **too_quiet** → retune −8, correction −18, confidence −20
- **clipped_risk** → correction −12, confidence −15
- **minor scale** → humanize +8, vibrato_preserve +8; +5 confidence for rnb/trap

**Backing-driven micro-tuning (when backing/beat analysis available):**
- Bass high → retune +8, correction +6
- Bass low → retune −5
- Energy high → correction +5
- Energy low → humanize +10, vibrato +8
- Bright → formant +8
- Dark → formant −10
- BPM ≥ 120 → retune +3
- BPM ≤ 75 → humanize +5

- **clipping_risk** → correction −15, confidence −15
- **minor scale** → humanize +8, vibrato_preserve +8 (preserves emotional feel)

All parameters are derived from the preset library with audio-quality
fine-tuning — see **Mainstream Preset Library** and **Preset Matching Rules** above.

Real pitch correction is applied — see **Processing Pipeline** below.
The profile parameters also serve as a bridge to a future pyworld +
formant shifter engine for higher-quality correction.

### Auto-Adaptation Mode (v2.8)

When `autotune_mode=auto`, the system ignores the slider as the primary
matching key and instead selects the best preset autonomously:

| Input | Weight | Effect |
|---|---|---|
| Beat style | Primary | Trap → melodic_trap / melodic_trap; R&B → emotional_rnb; 电子 → modern_pop |
| Scale (major/minor) | Primary | minor + Trap → melodic_trap; minor + R&B → emotional_rnb; major + 电子 → modern_pop |
| Audio quality | Override | too_quiet → force natural_pop; clipped_risk → force modern_pop |
| Strength slider | Nudge | High pref (> 75) + 电子 → hyperpop; Low pref (< 35) → natural_pop |
| Duration < 5 s | Penalty | Confidence −20, mark as draft-grade |

Auto mode produces the same profile fields as manual mode, with these differences:
- `preset_source` = `"auto_adaptation"` (vs `"mainstream_rule_preset"`)
- `mode` = `"auto"` (vs `"manual"`)
- `reason` is prefixed with `[自动适配]` and the matching rationale
- `confidence` is driven by how well the beat style + scale match, not by strength distance

Quality adjustments (too_quiet, clipped_risk, minor scale) apply identically in both modes.

When a ``beat_analysis`` JSON (from ``/analyze-beat``) is also provided, the
auto matcher uses the beat's detected features (BPM, energy, bass, brightness)
to further refine retune_speed, correction_amount, humanize, formant_preserve,
and vibrato_preserve — see **Beat-Driven Auto-Tune Refinement** above.

### Beat Profile Header (v2.7)

The response also includes `X-Beat-Profile`, a URL-encoded JSON
object with Beat-generation parameters derived from audio analysis,
Auto-Tune profile, and user-selected beat style.  **This is parameter-only —
no Beat audio is generated yet.**

| Field | Example | Description |
|---|---|---|
| `target_bpm` | `78` | Suggested tempo for the beat |
| `beat_style` | `沉浸 Trap` | User-selected beat style |
| `groove_type` | `triplet_hihat` | `straight` / `swing` / `triplet_hihat` |
| `drum_density` | `75` | 0–100, drum pattern fill level |
| `bass_intensity` | `80` | 0–100, bass/sub-bass presence |
| `chord_progression` | `i–VI–III–VII in C minor` | Suggested chord loop |
| `arrangement_hint` | `前奏8 → 主歌16 …` | Section-by-section arrangement template |
| `vocal_match` | (string) | How well the vocal profile matches this beat style |
| `match_reason` | (string) | Why these parameters were chosen |
| `next_step` | (string) | Suggested next action for the user |

#### BPM Ranges by Style

| Beat Style | BPM Range | Default |
|---|---|---|
| 清爽电子 | 105–124 | 115 |
| 沉浸 Trap | 70–88 | 78 |
| 流行节奏 | 90–112 | 100 |
| 未来 R&B | 72–96 | 84 |

#### Parameter Rules

| Condition | Effect |
|---|---|
| `clipped_risk` | Drum density −20, bass intensity −15 — keep the beat less busy |
| `too_quiet` | Drum density −15 — don't overwhelm weak vocals; warn in `vocal_match` |
| `minor` scale + Trap/R&B | Bass intensity +10, drum density +5 |
| `robotic` / `trap` Auto-Tune | Harder drums + heavier bass + synth-driven arrangement |
| `natural` / `pop` Auto-Tune | Clean rhythm + moderate bass, preserves vocal detail |
| `correction_amount` > 80 | Extra +10 density, +8 bass — match the aggressive tuning |
| Duration < 5 s | `vocal_match` and `next_step` warn that profile is draft-grade |

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Check that the backend is running |
| `POST` | `/analyze-beat` | Upload beat → musical-feature analysis (legacy, Chinese labels) |
| `POST` | `/analyze-backing-track` | Upload backing track → musical-feature analysis (v2.8, English labels + confidence) |
| `POST` | `/process-vocal` | Upload vocal → processed WAV + Auto-Tune profile + Beat-gen blueprint |
| `POST` | `/quality-check` | **v3.3** — one vocal → 5 WAVs (natural_pop / modern_pop / emotional_rnb / melodic_trap / hyperpop) for A/B comparison |
| `GET` | `/download/{filename}` | **v3.3** — download a processed WAV from `processed/` |
| `POST` | `/quality-feedback` | **v3.4** — submit A/B listening label for a quality-check version |
| `POST` | `/feedback` | Submit Auto-Tune quality feedback (v2.6.3) |
| `DELETE` | `/uploads/{filename}` | Delete a temporary uploaded or processed file |

### Beat Analysis Endpoint (v2.8)

**`POST /analyze-beat`** — upload a beat/backing track to get musical features:

| Field | Example | Description |
|---|---|---|
| `duration_seconds` | `187.34` | Length in seconds |
| `estimated_bpm` | `78` | Librosa beat-tracker tempo estimate |
| `energy_level` | `high` | RMS-based: `low` / `medium` / `high` |
| `bass_level` | `high` | Sub-250 Hz ratio: `low` / `medium` / `high` |
| `brightness` | `low` | Spectral centroid: `low` / `medium` / `high` |
| `suggested_style` | `沉浸 Trap` | Rule-based from BPM + energy + bass + brightness |
| `suggested_key` | `unknown` | Placeholder for future key detection |

Style detection rules:
- BPM ≥ 105 + high energy + medium/high brightness → `清爽电子`
- BPM 70–95 + high bass → `沉浸 Trap`
- BPM 85–110 + medium/high energy → `流行节奏`
- BPM 60–100 + low/medium energy + low/medium brightness → `未来 R&B`
- Fallback: bass heavy → Trap, high energy → Electronic, otherwise → Pop

The result JSON can be passed as the `beat_analysis` form field to
`/process-vocal` for beat-driven Auto-Tune adaptation.

#### Beat-Driven Auto-Tune Refinement

When `beat_analysis` is provided in **auto** mode, the following adjustments
are applied on top of the normal auto-adaptation rules:

| Beat Feature | Effect |
|---|---|
| `bass_level = high` | retune_speed +8, correction_amount +6 |
| `bass_level = low` | retune_speed −5 |
| `energy_level = high` | correction_amount +5 (can handle more aggressive tuning) |
| `energy_level = low` | humanize +10, vibrato_preserve +8 (keep emotional feel) |
| `brightness = high` | formant_preserve +8 (preserve vocal clarity) |
| `brightness = low` | formant_preserve −10 |
| `estimated_bpm ≥ 120` | retune_speed +3 |
| `estimated_bpm ≤ 80` | humanize +5 |

Quality overrides (too_quiet, clipped_risk) still take priority over beat-driven adjustments.

### Backing-Track Analysis Endpoint (v3.0 dual-input)

**`POST /analyze-backing-track`** — upload a backing track to get musical features
with English naming conventions and a confidence score:

| Field | Example | Description |
|---|---|---|
| `duration_seconds` | `187.34` | Length in seconds |
| `estimated_bpm` | `78` | Librosa beat-tracker tempo estimate |
| `energy_level` | `high` | RMS-based: `low` / `medium` / `high` |
| `bass_level` | `high` | Sub-250 Hz ratio: `low` / `medium` / `high` |
| `brightness` | `dark` | Spectral centroid: `dark` / `balanced` / `bright` |
| `suggested_style` | `trap` | English: `pop` / `trap` / `rnb` / `electronic` / `unknown` |
| `suggested_key` | `unknown` | Placeholder for future key detection |
| `confidence` | `82` | 0–100, how well features match the detected style |

Style detection rules:
- BPM >= 105 + high energy + balanced/bright → `electronic` (confidence 78)
- BPM 70–95 + high bass → `trap` (confidence 82)
- BPM 85–110 + medium/high energy → `pop` (confidence 75)
- BPM 60–100 + low/medium energy + dark/balanced → `rnb` (confidence 72)
- Fallbacks: high bass → `trap`, high energy → `electronic`, otherwise → `unknown`
- BPM = 0 → confidence −20

Pass the result fields as separate form fields to `/process-vocal`:
`backing_style`, `backing_energy`, `backing_bass`, `backing_brightness`.

### Dual-Input Adaptation (v2.8)

When backing-track fields are sent to `/process-vocal`, the Auto-Tune profile
includes adaptation metadata describing what drove the parameter selection:

**`adaptation_inputs`:**
```json
{
  "vocal": "已上传人声",
  "style_source": "伴奏分析",
  "backing": {
    "style": "trap",
    "energy": "high",
    "bass": "high",
    "brightness": "dark"
  }
}
```

**`adaptation_summary`:**
- `仅人声` — no backing or beat-style input
- `人声 + 手动曲风` — user manually selected a beat style
- `人声 + 伴奏分析` — backing track was analysed and drives adaptation

Backing features are mapped to Chinese style names internally for the preset
matcher, and backing-driven refinement (bass → retune/correction,
energy → humanize, brightness → formant, BPM → retune/humanize) is applied
identically to the beat-analysis path.

### Profile-Driven Processing (v3.3)

All Auto-Tune profile parameters now genuinely affect the audio output.
Different presets produce **audibly different** results:

| Preset | retune | correction | humanize | formant | vibrato | Audible character |
|---|---|---|---|---|---|---|
| `natural_pop` | 30 | 35% | 85 | 85 | 90 | Very subtle, 5120-sample segments, highest dry blend |
| `emotional_rnb` | 42 | 45% | 80 | 82 | 88 | Gentle snap, 4608-sample segments, vibrato preserved |
| `modern_pop` | 52 | 55% | 60 | 72 | 65 | Balanced correction, 4096-sample segments |
| `melodic_trap` | 65 | 70% | 42 | 58 | 48 | 3072-sample segs, hard-tune, median aggregation |
| `melodic_trap` | 82 | 85% | 25 | 45 | 28 | 3072-sample segs + 80Hz low-cut, deep hard-tune |
| `hyperpop` | 96 | 96% | 8 | 20 | 10 | 2048-sample segs, stair-step quantise, near-zero dry |

**How each parameter affects the audio:**

| Parameter | Engine effect |
|---|---|
| `retune_speed` | Controls median-filter smoothing window (1–25 frames). Higher = faster pitch snap. |
| `correction_amount` | Blend factor 0–100%. Above 70% enters hard-tune region with accelerated snap. |
| `humanize` | Timing jitter (±25% of step) + amplitude jitter (±12%) on segment boundaries. Higher = more natural, looser feel. |
| `formant_preserve` | Dry/wet blend (0–40% dry). Higher = more original vocal character blended back. |
| `vibrato_preserve` | Detects vibrato segments via F0 coefficient-of-variation; scales down correction on those frames. |
| `style_mode` | Controls segment size: robotic=2048, trap=3072, pop=4096, rnb=4608, natural=5120 samples. Also controls overlap, median/mean aggregation, stair-step quantise, and 80 Hz low-cut. |

**Quality overrides:**
- `clipped_risk`: −8 dB input gain + correction reduced by 25%
- `too_quiet`: loudness boost (max +21.6 dB) + correction capped at 50% to avoid noise amplification

## Upload Rules

- Max file size: 25 MB
- Allowed input types: WAV, MP3, MP4 audio, M4A
- Output: always 16-bit 44.1 kHz mono WAV

## Processing Pipeline

```
Upload → [validate] → [save raw] → [convert to WAV] → [analyse]
                                                              ↓
                                          Auto-Tune profile generation
                                                              ↓
                                          Gain staging (clip prot / loudness)
                                                              ↓
                                          F0 detection (librosa.pyin)
                                                              ↓
                                          Per-segment pitch correction
                                          (phase vocoder → target scale)
                                                              ↓
                                          Tonal shaping + peak limiting
                                                              ↓
                                          return processed WAV + headers
```

### Pitch Correction Engine (v2.6)

The `/process-vocal` endpoint now applies **real pitch correction**:

| Step | Effect | Library |
|---|---|---|
| 1 | Gain staging | Clipping prot (−5 dB) + loudness norm (−17 dBFS) |
| 2 | F0 detection | `librosa.pyin` — probabilistic YIN, fmin=C2, fmax=C7 |
| 3 | Target scale mapping | Nearest MIDI note in key/scale (36–96) |
| 4 | Retune smoothing | Median filter: size 21 (natural) … 1 (robotic) |
| 5 | Per-segment pitch shift | ~93 ms segments, 66 % overlap, `librosa.effects.pitch_shift` |
| 6 | Tonal shaping + peak limit | 80 Hz low-cut (trap/robotic), ceiling −0.5 dBFS |

**How `autotune_strength` controls the result:**

| Strength | Style | correction_amount | retune_speed | Audible effect |
|---|---|---|---|---|
| 20 % | natural | 30 % | 28 | Very subtle — gentle nudge toward scale |
| 50 % | pop | 50 % | 50 | Moderate — audible correction, still natural |
| 70 % | trap | 75 % | 72 | Strong — fast snap, clearly tuned |
| 95 % | robotic | 100 % | 92 | Maximum — instant snap, hard-tuned sound |

- **correction_amount** = pitch blend (0 % = dry, 100 % = full snap)
- **retune_speed** = smoothing window size (higher = less smoothing = faster snap)

The `X-Processing-Status` response header is `"autotune-preview"` when
processing succeeds, or `"converted-wav"` on fallback.

### Testing Different Strength Levels

```bash
# Low strength (subtle, natural) — strength 20
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@vocal.wav" -F "autotune_strength=20" -o strength_20.wav

# Medium strength (audible correction) — strength 50
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@vocal.wav" -F "autotune_strength=50" -o strength_50.wav

# High strength (hard-tuned) — strength 95
curl -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@vocal.wav" -F "autotune_strength=95" -o strength_95.wav
```

Compare the three files — they should sound progressively more tuned.

Future steps:

1. Higher-quality F0 tracking (pyworld / CREPE for better accuracy)
2. Real-time formant preservation during pitch shifting
3. Sample-level pitch correction (PSOLA / WSOLA) instead of segment-based
4. High-quality Beat generation or MIDI rendering
5. Vocal + Beat mixing into a final downloadable stereo file

## Project Structure

```
backend/
├── app.py              # FastAPI entry point (v3.1)
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── .gitignore
├── uploads/            # Raw uploaded files (auto-created)
├── processed/          # Converted WAV files + quality-check outputs (auto-created)
├── feedback/           # User feedback records (auto-created)
│   └── feedback.jsonl  # Labelled training data for future ML
└── .venv/              # Virtual environment (not committed)
```

## Current Limits

This version does **not**:

- Use formant preservation (pitch shifting changes formants slightly)
- Perform sample-level pitch correction (segment-based only, ~93 ms windows)
- Generate Beat audio (parameter blueprint only — `X-Beat-Generation-Profile`)
- Mix vocal and Beat
- Store user accounts or project history

It **does** apply real pitch correction, generate intelligent Beat-generation
parameters, and collect user feedback for future personalised recommendations.

## Feedback System (v2.6.3)

The `/process-vocal` response includes an `X-Profile-Id` header.  The
frontend shows five feedback buttons after processing:

| Label | Meaning |
|---|---|
| 太轻 | Correction too weak — want stronger tuning |
| 正好 | Just right — parameters are well-matched |
| 太重 | Too aggressive — want more natural sound |
| 太假 | Sounds artificial — reduce robotic character |
| 更自然 | Want more human-like / transparent processing |

### POST /feedback

```
profile_id=<X-Profile-Id value>
label=<one of the five labels above>
```

Each submission appends one line to `backend/feedback/feedback.jsonl`:

```json
{"profile_id": "abc123", "label": "正好", "timestamp_utc": "2026-05-27T..."}
```

**Design principles:**
- No audio data is stored — only parameter metadata and feedback labels
- Data stays local (no cloud upload, no external service)
- Purpose: build a labelled dataset for future personalised Auto-Tune
  strength recommendation (e.g., "given vocal quality X and beat style Y,
  predict whether the user will find the correction too light/just right/too heavy")

## v3.1 Quality Check — 三版本 Auto-Tune 校准 (A/B Comparison)

The ``POST /quality-check`` endpoint processes the **same vocal** through
**three deliberately extreme parameter sets** so you can verify that the
pitch-correction engine genuinely produces audibly different results.

### Quick Start

Start the backend, then:

```bash
# Windows (PowerShell) — single command, outputs 3 versions
curl.exe -v -X POST http://127.0.0.1:8000/quality-check `
  -F "file=@your-vocal.wav" `
  -F "key=C" `
  -F "scale=major" `
  -o qc_report.json

# Read the response to get download URLs
type qc_report.json
```

### Download Each Version

The JSON response contains ``versions.natural_pop.download_url``,
``versions.modern_pop.download_url``, ``versions.emotional_rnb.download_url``,
``versions.melodic_trap.download_url``, and ``versions.hyperpop.download_url``.
Use them to fetch each processed WAV:

```bash
# Download all five (replace filenames from the JSON output)
curl.exe -O http://127.0.0.1:8000/download/qc_natural_pop_xxxxxxxx.wav
curl.exe -O http://127.0.0.1:8000/download/qc_modern_pop_xxxxxxxx.wav
curl.exe -O http://127.0.0.1:8000/download/qc_emotional_rnb_xxxxxxxx.wav
curl.exe -O http://127.0.0.1:8000/download/qc_melodic_trap_xxxxxxxx.wav
curl.exe -O http://127.0.0.1:8000/download/qc_hyperpop_xxxxxxxx.wav
```

Or use a single PowerShell snippet to parse and download:

```powershell
$report = Get-Content qc_report.json | ConvertFrom-Json
foreach ($preset in @('natural_pop','modern_pop','emotional_rnb','melodic_trap','hyperpop')) {
    $fn = Split-Path $report.versions.$preset.download_url -Leaf
    curl.exe -O "http://127.0.0.1:8000/download/$fn"
}
```

### How the Five Versions Differ

Uses the v3.2 Mainstream Auto-Tune Parameter Library directly.
Each version writes a file named `qc_{preset_name}_{vocal_id}.wav`.

| Parameter | natural_pop | modern_pop | emotional_rnb | melodic_trap | hyperpop |
|---|---|---|---|---|---|
| `retune_ms` | ~90 ms | ~26 ms | ~58 ms | ~8 ms | ~0 ms |
| `correction_amount` | 28 % | 60 % | 42 % | 78 % | 98 % |
| `humanize` | 92 | 55 | 84 | 30 | 2 |
| `formant_preserve` | 90 | 70 | 84 | 48 | 10 |
| `vibrato_preserve` | 92 | 62 | 90 | 35 | 5 |
| `pitch_tracking` | relaxed | medium | relaxed | fast | instant |
| `style_mode` | natural | pop | rnb | trap | robotic |

**Engine effects per preset:**

| Engine knob | natural_pop | modern_pop | emotional_rnb | melodic_trap | hyperpop |
|---|---|---|---|---|---|
| F0 blend | 28 % | 60 % | 42 % | 78 % | 100 % hard |
| Median-filter | 25 frames | 7 frames | 15 frames | 3 frames | 1 frame |
| Segment size | 6144 smp | 4096 smp | 5120 smp | 2560 smp | 1024 smp |
| Dry/wet blend | 75 % dry | 52.5 % dry | 63 % dry | 36 % dry | 7.5 % dry |
| Vibrato preserve | full | moderate | full | low | crushed |
| Crossfade | raised-cosine | raised-cosine | raised-cosine | raised-cosine | 2-smp micro |
| Stair-step quant | no | no | no | 0.5 st | 1.0 st |
| Tanh saturation | no | no | no | no | yes |
| 80 Hz low-cut | no | no | no | yes | yes |

### Expected Audible Results

**natural_pop** — 几乎听不出修音。
  - ~90 ms retune + 92 humanize + 90 vibrato → 极自然
  - 75 % 干声混合 → 绝大部分保留原声
  - **验收标准**：A/B 对比与原声几乎无差异。

**modern_pop** — 稳定、明亮、有控制的主流流行修音。
  - ~26 ms retune + 60 % correction → 明显但不过度
  - 52.5 % 干声混合 → 平衡处理和原声
  - **验收标准**：类似 Billbord 流行唱片修音效果。

**emotional_rnb** — 转音和滑音完整保留，柔和自然。
  - ~58 ms retune + 84 humanize + 90 vibrato → 尊重即兴
  - 63 % 干声混合 → 大量原声保留
  - **验收标准**：转音/滑音段与原声几乎一致。

**melodic_trap** — 快速音高锁定，紧凑有力。
  - ~8 ms retune + 78 % correction → 明显的快速修正
  - 36 % 干声 + 80Hz 低切 → 修音感清晰但不破音
  - **验收标准**：类似旋律说唱的修音质感。

**hyperpop** — 完全电子化音色，创意效果。
  - 0 ms retune + 98 % correction → 即时量化
  - 7.5 % 干声 + tanh 饱和 + 阶梯量化 + 二次残差
  - **验收标准**：明显的 T-Pain / Hyperpop 电子效果。

### Quantitative Comparison Metric

The response includes a ``comparison`` object with five pairwise metrics:

| Pair | Metric | Typical value | Meaning |
|---|---|---|---|
| natural_pop vs modern_pop | `waveform_correlation` | 0.85–0.95 | Similar shape, moderate difference |
| modern_pop vs emotional_rnb | `waveform_correlation` | 0.88–0.96 | Similar processing intensity |
| emotional_rnb vs melodic_trap | `waveform_correlation` | 0.55–0.80 | Clearly different correction |
| melodic_trap vs hyperpop | `waveform_correlation` | 0.40–0.70 | Very different character |
| natural_pop vs hyperpop | `waveform_correlation` | 0.25–0.55 | Maximum difference |

If ``waveform_correlation`` > 0.95 across all pairs, the vocal likely lacks
clear pitch (spoken word, whispering) — try a **sung melody** for more
meaningful results.

## v3.4 A/B Listening Feedback Loop

After running ``/quality-check`` to generate five preset versions, you can
submit per-version listening labels to ``POST /quality-feedback``.  These
records build a labelled dataset for future personalised Auto-Tune
parameter recommendation.

### How to Do an A/B Listening Test

**Step 1 — start the backend** and open the frontend in a browser:

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8000
# Open index.html in your browser
```

**Step 2 — upload a vocal file** (WAV/MP3/M4A, any key, < 25 MB).

**Step 3 — click "A/B 听感测试"**.  The backend generates five WAV files
(natural_pop / modern_pop / emotional_rnb / melodic_trap / hyperpop) and
returns download URLs.  Each version appears with an inline audio player.

**Step 4 — listen and label**:

| Button | Label | Meaning |
|---|---|---|
| ★ 最好听 | `best` | This preset sounds the best for this vocal |
| 自然 | `natural` | Sounds transparent, barely processed |
| 不错 | `good` | Acceptable, useable |
| 太假 | `too_fake` | Artificial / robotic character |
| 太轻 | `too_light` | Correction not strong enough |
| 太重 | `too_heavy` | Over-processed |
| 刺耳 | `harsh` | Harsh high frequencies / distortion |

You can label multiple presets.  Each click sends one record to the backend.

**Step 5 — verify the feedback was recorded**:

```bash
type backend\feedback\autotune_listening.jsonl
```

Example record:

```json
{"vocal_id": "abc123", "preset_name": "modern_pop", "label": "best", "rating": 5, "timestamp_utc": "2026-05-30T..."}
```

### API: POST /quality-feedback

| Field | Type | Required | Description |
|---|---|---|---|
| `vocal_id` | string | yes | Vocal session ID from `/quality-check` response |
| `preset_name` | string | yes | natural_pop / modern_pop / emotional_rnb / melodic_trap / hyperpop |
| `rating` | int | no | 1–5, where 5 = best |
| `label` | string | no | best / too_fake / too_light / too_heavy / harsh / natural / good |
| `note` | string | no | Free-text comment |
| `backing_style` | string | no | pop / trap / rnb / electronic / unknown |

```bash
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"abc123","preset_name":"modern_pop","label":"best","rating":5}'
```

### Future Use of Feedback Data

The labelled records serve two purposes:

1. **Personalised ranking** — given a vocal's audio quality + key/scale +
   backing style, predict which preset the user is most likely to prefer.
2. **Preset calibration** — if 80 % of users label ``hyperpop`` as
   ``too_fake`` on natural vocals, we know the preset should be tuned down
   for that vocal type.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| All 5 sound identical | Vocal has no clear F0 (spoken, whisper) | Upload a melody with clear pitch |
| hyperpop sounds too quiet | Tanh saturation + 80 Hz cut | Expected — check peak ratio in comparison |
| natural_pop sounds processed | Input already heavily Auto-Tuned | Use a raw/unprocessed vocal recording |
| Server error 500 | ffmpeg not on PATH | `ffmpeg -version` → install if missing |
| Feedback not saved | `feedback/` dir missing | Backend auto-creates on first write |
| Frontend CORS error | Backend not on 127.0.0.1:8000 | Start uvicorn with `--host 127.0.0.1 --port 8000` |
