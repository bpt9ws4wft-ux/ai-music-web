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
| `trap_polished` 精修 Trap | ~5 ms | 88 % | 22 | 40 | 23 | fast | 强修音但保留质感 |
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
├── app.py              # FastAPI entry point (v3.6)
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

## Quality Check Endpoint (v3.6)

The ``POST /quality-check`` endpoint processes the **same vocal** through
**five mainstream preset profiles** (natural_pop / modern_pop / emotional_rnb /
melodic_trap / hyperpop) so you can compare Auto-Tune styles side by side.

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

The JSON response contains:
- ``source_download_url`` — the unprocessed original vocal for reference
- ``recommended_preset`` — which preset auto mode would select (v3.6)
- ``versions.natural_pop.download_url``, ``versions.modern_pop.download_url``,
  ``versions.emotional_rnb.download_url``, ``versions.melodic_trap.download_url``,
  and ``versions.hyperpop.download_url``

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

## v3.7 Feedback-Gap Preset Recommendation

When the A/B listening feedback reveals a consistent pattern where:

- ``melodic_trap`` is labelled ``too_light`` (用户觉得修得不够强)
- ``hyperpop`` is labelled ``too_fake`` / ``harsh`` (用户觉得太假/刺耳)

the system detects a **preset gap** and recommends ``trap_polished`` — a new
preset positioned between melodic_trap and hyperpop.

### trap_polished — 精修 Trap

| Parameter | Value | Rationale |
|---|---|---|
| `retune_ms_equivalent` | ~5 ms | Faster than melodic_trap (8ms), slower than hyperpop (0ms) |
| `correction_amount` | 88 % | Higher than melodic_trap (78%), lower than hyperpop (98%) |
| `humanize` | 22 | More natural than hyperpop (2), tighter than melodic_trap (30) |
| `formant_preserve` | 40 | Between melodic_trap (48) and hyperpop (10) |
| `vibrato_preserve` | 23 | Between melodic_trap (35) and hyperpop (5) |
| `style_mode` | trap | Same engine mode as melodic_trap (80Hz cut, median aggregation) |
| `pitch_tracking` | fast | Responsive tracking for rap/trap vocals |
| **Best for** | 旋律说唱、强修音但保留质感的人声 |
| **Risk** | 中高 — 需要 voiced-only 保护和 soft limiter 防破音 |

### Gap Detection Logic

```
if melodic_trap.too_light_count > 0 AND hyperpop.too_fake_harsh_count > 0:
    if auto-mode would select melodic_trap OR hyperpop:
        → recommend trap_polished instead
        → confidence +5
        → feedback_adjustment: "反馈缺口检测：推荐中间方案 trap_polished"
```

The gap detection runs **after** quality protections (too_quiet / clipped_risk
still take priority) and **alongside** the normal feedback nudge.  If both the
gap and a regular nudge fire, the gap takes precedence.

### Verification

```bash
# 1. Create feedback showing the gap pattern
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"gap","preset_name":"melodic_trap","label":"too_light"}'
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"gap","preset_name":"hyperpop","label":"too_fake"}'

# 2. Auto mode with trap backing → should recommend trap_polished
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@test_vocal.wav" \
  -F "autotune_mode=auto" \
  -F "backing_style=trap" \
  -o /dev/null 2>&1 | grep -i "trap_polished"

# 3. Check X-Autotune-Profile for feedback_adjustment
# Should show: "反馈缺口检测：...推荐中间方案 trap_polished"
```

## v3.8 Feedback-driven Parameter Tuning

v3.8 goes beyond preset switching: the system now **micro-tunes individual
Auto-Tune parameters** based on A/B listening feedback patterns, without
requiring new fixed presets.

### Tuning Rules

| Feedback pattern | Parameter adjustment |
|---|---|
| Preset marked `too_light` | correction +8, retune +6 (faster), humanize −8 |
| Preset marked `too_fake` / `harsh` | correction −8, retune −8 (slower), humanize +8, formant +8, vibrato +5 |
| Mixed (`too_light` + `too_fake`) | Conservative center adjustment: correction ±2, humanize +3, formant +4 |
| **Gap**: `melodic_trap` too_light **+** `trap_polished` too_fake | Dynamic intermediate: corr=82%, retune≈6ms, humanize=28, formant=48, vibrato=32 |

All adjustments are clamped to safe ranges and never override quality
protections (too_quiet / clipped_risk).

### X-Autotune-Profile New Fields

| Field | Example | Meaning |
|---|---|---|
| `feedback_parameter_adjustment.applied` | `true` | Whether parameter tuning was applied |
| `feedback_parameter_adjustment.before_params` | `{correction_amount: 78, ...}` | Pre-tuning snapshot |
| `feedback_parameter_adjustment.after_params` | `{correction_amount: 86, ...}` | Post-tuning snapshot |
| `feedback_parameter_adjustment.personalization_reason` | `"标记为 too_light（2 次）→ correction +8, ..."` | Why and how |

### Verification

```bash
# Submit too_light feedback for melodic_trap
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"tune","preset_name":"melodic_trap","label":"too_light"}'

# Run auto mode — check X-Autotune-Profile for feedback_parameter_adjustment
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@test_vocal.wav" -F "autotune_mode=auto" \
  -F "backing_style=trap" -o /dev/null 2>&1 | Select-String "correction|personalization"
```

## v4.2 Agent Inbox for Auto-Tune Feedback

Every time a user submits A/B listening feedback via ``POST /quality-feedback``,
the backend **automatically writes** a Markdown task file to
``agent_inbox/autotune_feedback_latest.md``.

No manual curl, no copy-paste — an AI agent (Claude, Codex, etc.) can read
this file directly from disk and propose the next tuning step.

### How It Works

```
POST /quality-feedback  →  _update_agent_inbox()  →  agent_inbox/autotune_feedback_latest.md
```

### Inbox File Contents

| Section | Content |
|---|---|
| Status | Feedback file path, total records, session count |
| Per-Preset Statistics | Score / count / too_light / too_fake_harsh / best for all 7 presets |
| Gap Status | gap_detected flag + would_recommend |
| Recent Feedback | Last 10 records in reverse chronological order |
| Agent 下一步任务 | Structured prompt with constraints, tunable parameters, and intensity groups |

### GET /debug/agent-inbox

```bash
curl http://127.0.0.1:8000/debug/agent-inbox
```

Returns `file_exists`, `last_updated`, and an 800-char preview.

### Using the Inbox with an AI Agent

```bash
# The agent reads the file directly — no API call needed
cat backend/agent_inbox/autotune_feedback_latest.md

# Or view via the debug endpoint
curl http://127.0.0.1:8000/debug/agent-inbox | python -m json.tool
```

The agent task section at the bottom of the file includes:
- 7 concrete constraints (don't change beat, don't redo UI, etc.)
- All tunable parameter ranges
- Preset intensity groups for safe swapping
- Explicit instruction to propose parameter values and matching rules

## v4.1 AI Tuning Advisor Prompt Export

One endpoint that generates a **self-contained Chinese-language prompt**
ready to copy-paste into any LLM chat.  No external API is called — the
backend just assembles the preset library + feedback summary + analysis
instructions into a single string.

### GET /debug/autotune-ai-prompt

```bash
curl http://127.0.0.1:8000/debug/autotune-ai-prompt | python -m json.tool
```

Returns:

```json
{
  "purpose": "Ask an AI model to analyze listening feedback...",
  "prompt": "# Auto-Tune 参数调优分析任务\n\n## 项目背景\n...",
  "prompt_length_chars": 3281,
  "has_feedback_data": true,
  "usage": "1. Copy the 'prompt' field. 2. Paste into any LLM chat..."
}
```

### Prompt Contents

| Section | Content |
|---|---|
| 项目背景 | What the system does, engine details |
| 可调参数说明 | All 7 parameters with ranges and meanings |
| 当前 Preset 列表 | All 7 presets with parameters + per-preset feedback stats |
| 强度分组 | Safe-swapping boundaries |
| 用户反馈摘要 | Label distribution, liked/disliked parameter ranges |
| 分析任务 | 5 structured questions for the AI to answer |

### Quick Export

```bash
# Save the prompt to a file
curl http://127.0.0.1:8000/debug/autotune-ai-prompt > ai_prompt.json

# Extract just the prompt text
python -c "import json; print(json.load(open('ai_prompt.json','r',encoding='utf-8'))['prompt'])" > ai_prompt.txt

# Copy and paste ai_prompt.txt into ChatGPT / Claude
```

### What the AI Will Tell You

The prompt asks the AI to output:
1. **推荐保留** — which presets to keep as-is
2. **增强/减弱** — which presets need parameter changes
3. **新增中间 profile** — suggested new presets between existing ones
4. **推荐参数范围** — global recommended ranges
5. **规则建议** — matching logic improvements

The AI is explicitly instructed to only suggest parameter changes we can
implement in code — not to recommend buying plugins or training models.

## v4.0 AI Tuning Advisor Data Interface

Two read-only endpoints prepare structured, labelled data for AI analysis.
**No real AI model is connected** — these endpoints produce the dataset that
you would feed to OpenAI, Claude, or a local model to discover tuning patterns.

### GET /debug/autotune-learning-dataset

Every feedback record joined with its preset's full parameter set:

```bash
curl http://127.0.0.1:8000/debug/autotune-learning-dataset | python -m json.tool
```

Each record:

```json
{
  "vocal_id": "abc123",
  "preset_name": "modern_pop",
  "preset_label": "现代流行",
  "final_used_params": {
    "retune_ms_equivalent": 26,
    "correction_amount": 60,
    "humanize": 55,
    "formant_preserve": 70,
    "vibrato_preserve": 62,
    "pitch_tracking": "medium",
    "style_mode": "pop"
  },
  "feedback_label": "best",
  "rating": 5,
  "backing_style": "pop",
  "note": null,
  "timestamp": "2026-05-30T..."
}
```

### GET /debug/autotune-learning-summary

Aggregate statistics across all feedback:

```bash
curl http://127.0.0.1:8000/debug/autotune-learning-summary | python -m json.tool
```

| Field | Meaning |
|---|---|
| `per_preset_summary.<name>.positive_pct` | % of best/good/natural labels |
| `per_preset_summary.<name>.negative_pct` | % of too_fake/harsh/too_heavy labels |
| `label_distribution` | Global count per label type |
| `most_liked_param_ranges` | {min, max, avg} of retune/correction/humanize/formant/vibrato for liked presets |
| `most_disliked_param_ranges` | Same for disliked presets |

### How to Use This for AI Analysis

**Step 1 — accumulate feedback** by running multiple A/B listening tests.

**Step 2 — export the dataset**:

```bash
curl http://127.0.0.1:8000/debug/autotune-learning-dataset > tuning_data.json
```

**Step 3 — feed to an LLM** with a prompt like:

> "Here are 50 Auto-Tune parameter + user feedback records. Which
> correction_amount range is most likely to get a 'best' label? What
> retune_ms threshold separates 'natural' from 'too_fake' feedback?
> Given that vocal 'abc123' got 'too_light' on natural_pop and 'best'
> on melodic_trap, what preset should we recommend for a similar vocal?"

**Step 4 — use the summary for quick insights**:

```bash
curl http://127.0.0.1:8000/debug/autotune-learning-summary
# → most_liked_param_ranges.retune_ms: {min: 8, max: 26, avg: 17}
# → Interpretation: users prefer retune between 8-26ms
```

## v3.9 Auto-Tune Calibration Session

A structured workflow to establish stable user preferences across 3 vocal
types, producing a ``calibration_profile`` that captures which presets and
parameter ranges work best for different vocal timbres.

### Calibration Workflow (Browser + curl)

**Step 1 — prepare 3 vocal files** representing different vocal types:

| File | Vocal type | Example |
|---|---|---|
| `rap_vocal.wav` | 低音/说唱型 | Low register, rhythmic, spoken/semi-sung |
| `melody_vocal.wav` | 旋律演唱型 | Mid register, sustained notes, clear melody |
| `high_vocal.wav` | 高音/副歌型 | Higher register, belted chorus, more energy |

**Step 2 — for each vocal file**, run the A/B listening test in the browser:

```bash
# Start backend
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

1. Open `index.html` in browser
2. Upload `rap_vocal.wav` → click "A/B 听感测试"
3. Listen to all 6 versions (natural_pop / modern_pop / emotional_rnb /
   melodic_trap / trap_polished / hyperpop)
4. Label each version: ★最好听 / 自然 / 不错 / 太假 / 太轻 / 太重 / 刺耳
5. Repeat for `melody_vocal.wav` and `high_vocal.wav`

**Step 3 — inspect the calibration profile**:

```bash
curl http://127.0.0.1:8000/debug/autotune-calibration-profile | python -m json.tool
```

### Calibration Profile Fields

| Field | Example | Meaning |
|---|---|---|
| `preferred_intensity` | `"medium-heavy"` | Most-liked intensity band across all vocals |
| `preferred_retune_range_ms` | `[5, 90]` | Retune ms range of all best/good presets |
| `preferred_correction_range` | `[28, 88]` | Correction % range of best/good presets |
| `disliked_artifacts` | `[{artifact:"under_correction",count:3},...]` | Ranked negative patterns |
| `best_presets_by_vocal_type` | `{"vocal_rap":"melodic_trap",...}` | Per-vocal best preset |
| `total_sessions` | `3` | Number of distinct vocal sessions |
| `intensity_votes` | `{"medium-heavy":1.5,...}` | Weighted votes per intensity |

### Interpreting the Calibration

```bash
# Example output analysis:
# preferred_intensity: medium-heavy
# → The user generally prefers melodic_trap / trap_polished level correction
#
# disliked_artifacts: under_correction is #1
# → User consistently finds lighter presets too weak
#
# best_presets_by_vocal_type:
#   vocal_rap → melodic_trap, vocal_melody → modern_pop, vocal_high → trap_polished
# → Different vocal types need different presets — system should weight by register
```

### Testing the Full Calibration Flow

```powershell
# 1. Simulate 3-vocal calibration with curl
$ids = @('vocal_rap','vocal_melody','vocal_high')

# Rap vocal: prefers melodic_trap
curl -X POST http://127.0.0.1:8000/quality-feedback -H "Content-Type: application/json" -d '{\"vocal_id\":\"vocal_rap\",\"preset_name\":\"melodic_trap\",\"label\":\"best\",\"rating\":5}'
curl -X POST http://127.0.0.1:8000/quality-feedback -H "Content-Type: application/json" -d '{\"vocal_id\":\"vocal_rap\",\"preset_name\":\"hyperpop\",\"label\":\"harsh\"}'

# Melody vocal: prefers modern_pop
curl -X POST http://127.0.0.1:8000/quality-feedback -H "Content-Type: application/json" -d '{\"vocal_id\":\"vocal_melody\",\"preset_name\":\"modern_pop\",\"label\":\"best\",\"rating\":5}'
curl -X POST http://127.0.0.1:8000/quality-feedback -H "Content-Type: application/json" -d '{\"vocal_id\":\"vocal_melody\",\"preset_name\":\"melodic_trap\",\"label\":\"too_heavy\"}'

# High vocal: prefers trap_polished
curl -X POST http://127.0.0.1:8000/quality-feedback -H "Content-Type: application/json" -d '{\"vocal_id\":\"vocal_high\",\"preset_name\":\"trap_polished\",\"label\":\"best\",\"rating\":5}'
curl -X POST http://127.0.0.1:8000/quality-feedback -H "Content-Type: application/json" -d '{\"vocal_id\":\"vocal_high\",\"preset_name\":\"hyperpop\",\"label\":\"too_fake\"}'

# 2. View calibration profile
curl http://127.0.0.1:8000/debug/autotune-calibration-profile

# 3. Verify
# - preferred_intensity should be medium-heavy or heavy
# - best_presets_by_vocal_type should show 3 entries
# - disliked_artifacts should list harsh/too_fake
```

## Debug Endpoints (v3.7)

Two read-only debug endpoints let you inspect whether A/B listening feedback
is actually affecting auto-mode recommendations — no code changes needed.

### GET /debug/autotune-feedback-preferences

Returns the full feedback preference snapshot:

```bash
curl http://127.0.0.1:8000/debug/autotune-feedback-preferences | python -m json.tool
```

| Field | Meaning |
|---|---|
| `feedback_file_path` | Absolute path to `autotune_listening.jsonl` |
| `file_exists` | Whether the feedback file has been created |
| `record_count` | Total number of feedback records across all presets |
| `per_preset.<name>.score` | Cumulative preference score |
| `per_preset.<name>.count` | Number of records for this preset |
| `per_preset.<name>.too_light_count` | "修太轻" labels |
| `per_preset.<name>.too_fake_harsh_count` | "太假"/"太重"/"刺耳" labels |
| `per_preset.<name>.best_count` | "最好听" labels |

### GET /debug/autotune-gap-status

Checks whether the feedback-gap pattern that triggers ``trap_polished``
recommendation is currently active:

```bash
curl http://127.0.0.1:8000/debug/autotune-gap-status
```

Example response when gap is detected:

```json
{
  "melodic_trap": {"too_light_count": 2, "total_count": 3, "score": -2},
  "hyperpop": {"too_fake_harsh_count": 2, "total_count": 2, "score": -4},
  "gap_detected": true,
  "would_recommend": "trap_polished"
}
```

### How to Verify Feedback Actually Affects Recommendations

```bash
# Step 1 — start clean
del backend\feedback\autotune_listening.jsonl

# Step 2 — verify empty state
curl http://127.0.0.1:8000/debug/autotune-feedback-preferences
# → "record_count": 0, "file_exists": false

curl http://127.0.0.1:8000/debug/autotune-gap-status
# → "gap_detected": false, "would_recommend": "normal_flow"

# Step 3 — check auto mode without feedback
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@test_vocal.wav" -F "autotune_mode=auto" \
  -F "backing_style=trap" -o /dev/null 2>&1 | Select-String "preset_name"
# → should be "melodic_trap" (no feedback, normal flow)

# Step 4 — simulate gap feedback pattern
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"vfy","preset_name":"melodic_trap","label":"too_light"}'
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"vfy","preset_name":"hyperpop","label":"too_fake"}'

# Step 5 — verify gap is now detected
curl http://127.0.0.1:8000/debug/autotune-gap-status
# → "gap_detected": true, "would_recommend": "trap_polished"

# Step 6 — check auto mode NOW recommends trap_polished
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@test_vocal.wav" -F "autotune_mode=auto" \
  -F "backing_style=trap" -o /dev/null 2>&1 | Select-String "preset_name"
# → should be "trap_polished" (gap detected)

# Step 7 — inspect the full preference snapshot
curl http://127.0.0.1:8000/debug/autotune-feedback-preferences
# → per_preset.melodic_trap.too_light_count: 1
# → per_preset.hyperpop.too_fake_harsh_count: 1
```

## v3.6 Auto-Tune Listening Workbench

The frontend A/B testing area has been upgraded into a full listening
workbench for efficient preset comparison.

### Workbench Features

| Feature | Description |
|---|---|
| **Original vocal player** | The unprocessed source WAV at the top — always available as reference |
| **5 version players** | One inline `<audio>` per preset with per-version parameter display |
| **"Current Recommendation" badge** | Highlights which preset auto mode would pick for this vocal |
| **10-second preview toggle** | Limits all players to the first 10 seconds — fast A/B cycling |
| **Auto-pause** | Playing one audio automatically pauses all others — no overlapping sound |
| **Feedback log** | Live-updating text showing which presets you've labelled and how |
| **Parameter display** | Each version shows retune_ms, correction%, humanize, formant, vibrato |

### How to Use the Workbench

**Step 1 — start the backend** and open the frontend:

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8000
# Open index.html in your browser
```

**Step 2 — upload a vocal file** (WAV/MP3/M4A, any key, < 25 MB).

**Step 3 — click "A/B 听感测试"**.  The backend generates five versions and
returns them with a source audio URL and a `recommended_preset` field.

**Step 4 — use the workbench controls**:
- Play the original, then each version.  Only one plays at a time.
- Enable "前10秒预览" for rapid cycling between presets.
- The preset with the "当前推荐" badge is what auto mode would select.
- Click feedback buttons to label each version.
- The feedback log at the top tracks what you've labelled.

### How to Do an A/B Listening Test (API-only)

After running ``/quality-check``, submit per-version listening labels
to ``POST /quality-feedback``.  These records build a labelled dataset
for future personalised Auto-Tune parameter recommendation.

**Step 5 — listen and label**:

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

## v3.5 Feedback-aware Auto-Tune Recommendation

In **auto mode** (`autotune_mode=auto`), the system reads past A/B listening
feedback from ``backend/feedback/autotune_listening.jsonl`` and uses it to
nudge the preset selection toward what the user has historically preferred.

### How Feedback Affects Recommendations

1. **Scoring**: each feedback record contributes a per-preset score:
   - `best` or rating≥5 → +3
   - `good` / `natural` or rating≥4 → +1
   - `too_fake` / `too_heavy` / `harsh` → −2
   - `too_light` → −1

2. **Nudge rule**: after the normal auto-adaptation logic selects a preset,
   the system checks whether another preset in the same "intensity group"
   has a feedback score at least **3 points higher**.  If so, it nudges the
   selection toward the higher-scored preset.

3. **Intensity groups** (safe swapping boundaries):

   | Group | Presets |
   |---|---|
   | Conservative | `live_tracking`, `natural_pop` |
   | Balanced | `natural_pop`, `modern_pop`, `emotional_rnb` |
   | Aggressive | `modern_pop`, `emotional_rnb`, `melodic_trap`, `trap_polished` |
   | Extreme | `melodic_trap`, `trap_polished`, `hyperpop` |

   Feedback can only nudge within a group — e.g., `modern_pop` ↔ `emotional_rnb`
   but never `natural_pop` → `hyperpop`.

4. **Quality protections always take priority**: `too_quiet` → `live_tracking`
   and `clipped_risk` → `natural_pop` are never overridden by feedback.

5. **When there is no feedback file**, behavior is identical to v3.4 — no
   nudge is applied.

### What You See in the Response

The `X-Autotune-Profile` header includes three new fields:

| Field | Example | Meaning |
|---|---|---|
| `feedback_preference_score` | `5` | Cumulative score for the selected preset (or 0) |
| `feedback_adjustment` | `反馈偏好：'现代流行' → '情绪 R&B'（历史评分 0 → 5）` | What the nudge did (or "保持选择") |
| `personalization_source` | `基于 3 条历史反馈` | Data source note (or "无历史反馈数据") |

### Verification

```bash
# 1. Submit several A/B feedback records favoring emotional_rnb
curl -X POST http://127.0.0.1:8000/quality-feedback \
  -H "Content-Type: application/json" \
  -d '{"vocal_id":"test","preset_name":"emotional_rnb","label":"best","rating":5}'
# (repeat 3-4 times)

# 2. Run process-vocal in auto mode with a pop backing
curl -v -X POST http://127.0.0.1:8000/process-vocal \
  -F "file=@your-vocal.wav" \
  -F "autotune_mode=auto" \
  -F "backing_style=pop" \
  -o /dev/null 2>&1 | grep -i "feedback"

# 3. Check the X-Autotune-Profile for feedback_preference_score and feedback_adjustment
```

### Future Use of Feedback Data

The labelled records serve three purposes:

1. **Real-time nudge (v3.5)** — already implemented: nudges auto-mode preset
   selection toward historically preferred presets within the same intensity group.
2. **Personalised ranking (future)** — given a vocal's audio quality + key/scale +
   backing style, predict which preset the user is most likely to prefer.
3. **Preset calibration (future)** — if 80 % of users label ``hyperpop`` as
   ``too_fake`` on natural vocals, the preset parameters should be tuned down
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
