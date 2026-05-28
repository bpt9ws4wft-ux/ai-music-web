# AI Music Web Backend v2.8 (preset engine + auto-adaptation + beat-driven adaptation)

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
| `preset_name` | `trap_hard` | Matched preset ID (v2.6.2) |
| `preset_label` | `Trap 强修` | Chinese preset label |
| `suitable_for` | `["Trap","Drill"]` | Genres this preset is designed for |
| `preset_source` | `auto_adaptation` | `mainstream_rule_preset` or `auto_adaptation` |
| `confidence` | `78` | 0–100, match confidence |
| `style_mode` | `trap` | Legacy engine mode (`natural` / `pop` / `rnb` / `trap` / `robotic`) |
| `style_mode_label` | `Trap` | Chinese label for the engine mode |
| `retune_speed` | `82` | 0–100, higher = faster pitch snap |
| `correction_amount` | `85` | 0–100, how much correction to apply |
| `humanize` | `25` | 0–100, timing jitter for natural feel |
| `formant_preserve` | `45` | 0–100, keep original vocal character |
| `vibrato_preserve` | `28` | 0–100, keep natural vibrato |
| `target_key` | `C` | Target root key |
| `target_scale` | `major` | Target scale |
| `target_scale_label` | `大调` | Chinese label for the scale |
| `vocal_quality` | `normal` | Quality assessment |
| `reason` | (string) | Why these parameters were chosen |
| `next_step` | (string) | Suggested next action for the user |
| `adaptation_inputs` | (object) | v2.8 dual-input: `vocal`, `style_source`, `backing` sub-fields |
| `adaptation_summary` | (string) | v2.8: `仅人声` / `人声 + 手动曲风` / `人声 + 伴奏分析` |

### Mainstream Preset Library (v2.6.2)

Six curated presets replace the old strength-band-only mapping:

| Preset | retune | correction | humanize | formant | vibrato | Best for |
|---|---|---|---|---|---|---|
| `natural_vocal` 自然修音 | 30 | 35 % | 85 | 85 | 90 | 民谣、唱作人、不插电 |
| `mainstream_pop` 主流流行 | 52 | 55 % | 60 | 72 | 65 | 流行、电子、舞曲 |
| `rnb_smooth` R&B 顺滑 | 42 | 45 % | 80 | 82 | 88 | R&B、Soul、慢节奏情歌 |
| `melodic_rap` 旋律说唱 | 65 | 70 % | 42 | 58 | 48 | 旋律说唱、Hip-Hop |
| `trap_hard` Trap 强修 | 82 | 85 % | 25 | 45 | 28 | Trap、Drill、重电子 |
| `robotic_hyperpop` 电音硬修 | 96 | 96 % | 8 | 20 | 10 | Hyperpop、实验电子、未来感 |

### Preset Matching Rules (ordered by priority)

| Priority | Condition | Result |
|---|---|---|
| 1 | `autotune_strength` > 85 | `robotic_hyperpop` |
| 2 | `beat_style` contains "Trap" **and** strength ≥ 60 | `trap_hard` |
| 3 | `beat_style` contains "Trap" **and** strength < 60 | `melodic_rap` |
| 4 | `beat_style` contains "R&B" | `rnb_smooth` |
| 5 | strength 60–85 (no style match) | `melodic_rap` |
| 6 | strength 30–60 (no style match) | `mainstream_pop` |
| 7 | strength < 30 (no style match) | `natural_vocal` |

Audio-quality adjustments applied on top of presets:
- **too_quiet** → retune −10, correction −20, confidence −20
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
| Beat style | Primary | Trap → trap_hard / melodic_rap; R&B → rnb_smooth; 电子 → mainstream_pop |
| Scale (major/minor) | Primary | minor + Trap → trap_hard; minor + R&B → rnb_smooth; major + 电子 → mainstream_pop |
| Audio quality | Override | too_quiet → force natural_vocal; clipped_risk → force mainstream_pop |
| Strength slider | Nudge | High pref (> 75) + 电子 → robotic_hyperpop; Low pref (< 35) → natural_vocal |
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

### Backing-Track Analysis Endpoint (v2.8 dual-input)

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
├── app.py              # FastAPI entry point
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── .gitignore
├── uploads/            # Raw uploaded files (auto-created)
├── processed/          # Converted WAV files (auto-created)
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
