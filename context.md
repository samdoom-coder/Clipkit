# clipkit — project context for the next agent

**Read this before changing code.** It explains what exists, why it exists, what is deliberately missing, and which traps already cost time.

| | |
|---|---|
| Project | `clipkit` — open, agent-native clip studio |
| Version | `0.5.0` |
| Baseline commit | latest on `main` (0.5.0 per-shot layout) |
| License | Apache-2.0 |
| Repo root | `/content/clipkit` |
| Python | 3.10+ (validated on 3.13.16) |
| Core dependency | FFmpeg + ffprobe on `PATH` |
| Status | Working local MVP; 94 tests passing; CI on every push/PR |
| Release | 0.5.0; local `main` is 6 commits ahead of `origin`, tag `v0.5.0`, push pending |
| Next work | `FUTURE.md` §0.6 checklist (soft cuts, split-pair identity, true ASD, captions) |

---

## 1. What this software is

`clipkit` turns a long video into captioned 1080×1920 clips. It is **not** a SaaS and the core **does not call an LLM**.

The design is agent-native:

- The **core** is deterministic: probe, VAD, ASR, features, candidate generation, scoring, captions, render.
- The **agent** brings the intelligence: it reads candidates through MCP/CLI, applies the editorial rubric in `SKILL.md`, picks clips, writes titles/hooks, and reviews contact sheets.
- Everything runs locally. The source video never leaves the machine.

This was built as an open-source alternative to OpusClip’s black-box clipping, with a deliberate inversion: instead of paying for LLM inference, the user’s coding agent (Claude Code, Cursor, Codex, OpenCode, Windsurf, Cline, Zed, etc.) supplies the editorial reasoning.

### Product thesis

The moat is **clip selection + evaluation**, not plumbing FFmpeg. Generic FFmpeg MCP servers already exist. `clipkit`’s differentiator is:

1. OpusClip-style candidate selection with a transparent rubric.
2. An eval harness so “better clips” is measurable.
3. A skill that encodes editorial judgment.
4. A local ASR stack (Rumble-2-Flash) good enough to replace cloud APIs.

---

## 2. Current status

### Working

- Local file and URL ingest (`yt-dlp` optional; SSRF guard).
- Resumable workspace with per-stage content-hash caching.
- Rumble-2-Flash ASR (validated on an NVIDIA T4), faster-whisper fallback, existing-transcript backend.
- CTC word alignment from Rumble’s auxiliary CTC head, with graceful proportional fallback.
- Energy VAD, audio RMS features, words-per-second, optional scene cuts.
- Deterministic candidate windows + transparent scoring:
  - hook 35%
  - standalone 25%
  - pacing 25%
  - emotion 15%
  - payoff as a ±15% adjustment
- Boundary snapping to sentence units; no bleeding into neighbouring sentences.
- ASS karaoke captions with active-word highlight and pop.
- Bundled Montserrat ExtraBold (OFL) so captions look consistent without system fonts.
- 1080×1920 crop and blur-pad layouts.
- Face tracking with **YuNet** (bundled, MIT) and a Haar fallback:
  - Kalman-predicted boxes + ByteTrack-style high/low-confidence association;
  - appearance re-ID through short occlusion (32×32 grayscale + hue descriptor,
    spatial gate, 5s lost gallery);
  - EMA smoothing, deadzone, scene-cut reset;
  - `sendcmd` dynamic crop paths for moving speakers;
  - static median face crop for `--reframe face`.
- Auto split-screen for two persistent, horizontally separated speakers with
  co-temporal overlap and `--min-split` hysteresis.
- 3+ face policy: top two by prominence (screen time × face size), with the
  rest ignored; a note records the decision.
- Dynamic split panels: each panel gets its own tracked `sendcmd` crop path.
- Auditable reframe sidecar: `clips/clip_00N.reframe.json` with tracks, panels,
  panel paths, crop path, layout decision, and notes.
- `clipkit eval` + `clipkit_eval` MCP tool for precision@k / coverage recall.
- Seed regression labels in `evals/seed_labels.json`.
- Per-shot layout: crop/split/blur decided per shot, rendered as segments,
  concatenated, then captioned and muxed in one final pass.
- Two-pass loudness normalization (targets `loudnorm -14 LUFS`) and faststart.
- Hook banner (top-of-frame, styleable, `--hook-text` / `--hook-from-title`
  / `--hooks hooks.json` for a different hook per clip).
- Split-screen coverage guard (pair co-visible for 70% of the clip) and
  default-on hard-cut detection (threshold 0.25).
- Containment-aware candidate dedupe + content-word diversity pass.
- CLI with 13 subcommands, 10-tool MCP server, bundled skill, eval harness.
- Contact-sheet inspection for visual QA.
- Blur and split fills are heavily blurred and darkened so a 16:9 close-up
  cannot leave a recognisable half-face beside a 9:16 panel/foreground.
- Dockerfile, Makefile, examples, synthetic demo, 94 tests, GitHub Actions CI.
- 0.4.1 was validated on a real 10-minute FO588 episode at 1920×1080; see
  `docs/real-video-test-fo588.md` and `FUTURE.md`. 0.4.3 added a TEDx talk
  (single speaker, 16 min), 0.4.5 added a cut-heavy two-speaker film excerpt
  (`twoperson.clipkit/VALIDATION.md`), and 0.5.0 added a 16:9
  crop→split→crop composite (`interview.clipkit`). See
  `docs/real-video-test-0.5.md`.

### Deliberately not built yet

- Long-term re-ID / re-entry (the descriptor gallery only covers 5s and clears
  at scene cuts).
- Manual speaker picker; the 3+ policy is automatic.
- Speaker diarization (pyannote) and true active-speaker detection; the
  current fallback is split or blur whenever multiple separated faces appear.
- B-roll, emoji, SFX, music ducking.
- Multi-aspect output (1:1, 4:5, 16:9).
- Publishing to TikTok/YouTube/Reels.
- Web UI.
- Real labeled eval dataset / public leaderboard.
- NVENC and batched ASR throughput.
- Speaker-aware caption colours.
- CPU-optimized CTC alignment.

---

## 3. First 10 minutes for a new agent

```bash
cd /content/clipkit
python -m pip install -e ".[dev,mcp]"
python -m pytest -q                    # 94 pass
ruff check src tests evals             # clean
clipkit doctor
make demo                              # synthetic video, no model/GPU needed
```

Then read:

1. `SKILL.md` — the editorial brain and agent workflow.
2. `README.md` — user-facing quickstart.
3. `src/clipkit/pipeline.py` — stage orchestration.
4. `src/clipkit/candidates.py` — selection logic.
5. `src/clipkit/asr/rumble.py` — the ASR/alignment path.

Real speech smoke test (needs the model, ~1 GB first run):

```bash
clipkit auto /content/demo/weather.mp4 -w /content/demo/real \
  --backend rumble --ctc-align --language English \
  --min-seconds 8 --max-seconds 12 --top 1
clipkit inspect -w /content/demo/real --top 1
```

---

## 4. Architecture

```
agent (Claude Code / Cursor / Codex / OpenCode / Windsurf / Cline / Zed)
├── SKILL.md                  editorial rubric + workflow + guardrails
└── MCP server (10 tools)     typed, deterministic, no LLM
      └── clipkit core (Python)
            ├── ffmpeg / ffprobe
            ├── Rumble-2-Flash (Transformers) + CTC greedy alignment
            ├── faster-whisper (optional)
            ├── energy VAD + optional scene detection
            ├── candidate generation + heuristic scoring
            ├── YuNet/Haar detection + Kalman/ByteTrack-style tracking
            │     └── per-panel crop-path planner + split-screen layout
            └── ASS captions + libx264 render
```

Three surfaces, one core:

- **CLI** — humans, CI, batch, reproducible runs.
- **MCP** — agents call typed tools; they never write FFmpeg commands.
- **SKILL.md** — the workflow and taste; installable with `clipkit skill --install DIR`.

### Stage flow

```
probe → audio → transcribe → features → candidates → render
```

Every stage writes a JSON artifact into the workspace and records:

- `status`: pending / running / done / failed / skipped
- `params_hash` (so changing backend/language/min/max invalidates the cache)
- `started_at`, `finished_at`, `error`, `artifacts`

The workspace is the database. No Postgres, Redis, or server is required for local use.

---

## 5. Repo map

| Path | Purpose |
|---|---|
| `README.md` | User quickstart, install, MCP setup, status, roadmap |
| `SKILL.md` | Agent skill: rubric, workflow, platform specs, guardrails |
| `context.md` | This file |
| `SECURITY.md` | Threat model: prompt injection, SSRF, path handling |
| `pyproject.toml` | Package, extras, scripts, wheel asset inclusion |
| `src/clipkit/cli.py` | argparse CLI, 12 commands |
| `src/clipkit/mcp_server.py` | 10-tool MCP server; supports MCP 1.x/2.x |
| `src/clipkit/pipeline.py` | Stage orchestration, caching, render loop |
| `src/clipkit/workspace.py` | Workspace/project state, stage cache, path containment |
| `src/clipkit/models.py` | Pydantic models: Word, Segment, Transcript, Candidate, Features, Project, ClipResult |
| `src/clipkit/media.py` | ffprobe/ffmpeg wrappers, crop box, split/sendcmd render, contact sheet, scene detect |
| `src/clipkit/ingest.py` | URL download + SSRF guard |
| `src/clipkit/vad.py` | RMS, adaptive VAD, chunking |
| `src/clipkit/text.py` | Sentence splitting, word-time estimation, title helper |
| `src/clipkit/align.py` | CTC Viterbi, greedy CTC words, SequenceMatcher alignment |
| `src/clipkit/asr/rumble.py` | Rumble-2-Flash backend + CTC word timing |
| `src/clipkit/asr/whisper.py` | faster-whisper backend |
| `src/clipkit/asr/mock.py` | Existing-transcript backend |
| `src/clipkit/candidates.py` | Window generation, scoring, containment-aware NMS, content diversity |
| `src/clipkit/captions.py` | ASS builder, caption + hook-banner style, bundled font copy |
| `src/clipkit/reframe.py` | YuNet/Haar detection, Kalman tracking, crop-path planner, split layout |
| `src/clipkit/features.py` | RMS/VAD/WPS/scene feature aggregation |
| `src/clipkit/evaluation.py` | precision@k / coverage-recall metrics |
| `src/clipkit/doctor.py` | Toolchain report |
| `src/clipkit/skill.py` | Locate/install SKILL.md |
| `src/clipkit/assets/fonts/` | Montserrat ExtraBold + OFL license |
| `src/clipkit/assets/models/` | YuNet ONNX + MIT license |
| `tests/` | 94 tests, including FFmpeg split/dynamic-crop end-to-end tests |
| `evals/` | Labeling protocol, metrics, harness |
| `examples/` | Demo script, MCP config, caption style |
| `Dockerfile`, `Makefile` | Reproducible install/test/demo |

---

## 6. Workspace layout

Default workspace is `.clipkit`; `-w/--workspace` overrides.

```
demo.clipkit/
├── project.json          # ProjectState: stages, source metadata, options
├── media.json            # MediaInfo from ffprobe
├── audio.wav             # 16 kHz mono PCM
├── transcript.json       # segments + word-level timings
├── features.json         # RMS series, speech regions, WPS, scene cuts
├── candidates.json       # ranked Candidate objects
├── clips/
│   ├── clip_001.mp4
│   ├── clip_001.ass
│   ├── clip_001.json           # ClipResult metadata (layout, plan path)
│   ├── clip_001.reframe.json   # tracks, panels, crop paths, notes
│   ├── clip_001.crop.cmd       # sendcmd path (only for --reframe track)
│   ├── clip_001.p0.cmd         # split panel 0 sendcmd path
│   ├── clip_001.p1.cmd         # split panel 1 sendcmd path
│   ├── Montserrat-ExtraBold.ttf
│   └── OFL.txt
└── inspect/
    └── clip_001.jpg      # 3×3 contact sheet
```

### Important schemas

`Transcript`:

```json
{
  "language": "en",
  "backend": "rumble",
  "duration": 11.04,
  "alignment": "ctc",
  "segments": [
    {
      "start": 0.6,
      "end": 9.4,
      "text": "Yesterday it was 35 degrees ...",
      "words": [{"text": "Yesterday", "start": 1.06, "end": 1.52}]
    }
  ]
}
```

`Candidate`:

```json
{
  "id": "c0000942_0009589",
  "rank": 1,
  "start": 0.942,
  "end": 9.589,
  "duration": 8.647,
  "text": "...",
  "title_hint": "...",
  "score": 71.2,
  "scores": {"hook": 72, "standalone": 91, "pacing": 44.8, "emotion": 65, "payoff": 65},
  "reasons": ["stands alone (91/100)"],
  "warnings": ["short clip; may feel incomplete"]
}
```

Candidate IDs are deterministic: `c<start_ms>_<end_ms>`, zero-padded.

---

## 7. ASR deep dive (read before touching alignment)

### Rumble-2-Flash

- Model: `Reson8/Rumble-2-Flash`, 0.5B, Apache-2.0, 45 languages.
- Base: `Qwen/Qwen3-ASR-0.6B`, Transformers 5.x, `AutoModelForMultimodalLM`.
- Validated on a Tesla T4: ~12s load (bf16/fp16), ~3s for 11s audio.
- First run downloads ~1 GB. `clipkit doctor` reports `rumble cached`.
- `_transcribe_chunk` passes `verbatim=False` by default to collapse stutters
  (“go go” → “go”). Override with `CLIPKIT_RUMBLE_VERBATIM=1`.
- When language is not forced, `processor.decode(..., return_format="parsed")`
  returns `{"language": "...", "transcription": "..."}` — **the key is
  `transcription`, not `text`**. This caused a real “no speech detected” bug.
- Long audio is VAD-chunked to ≤25s (`RumbleBackend.chunk_seconds`). The model
  uses 8s encoder attention windows.
- The model does **not** emit word timestamps.

### CTC word alignment

The repo ships an auxiliary CTC head: `ctc/ctc_head.safetensors`, 13 frames/s,
99,631 classes, blank = 99,630. `RumbleBackend._load_ctc()` imports the model
repo’s `ctc_head.py` dynamically, then:

1. `ctc.ln_post_states(ctc.log_mel(samples))` → encoder states.
2. `ctc.logits(states).argmax(-1)` → frame-level class IDs.
3. `ctc_greedy_words(...)` → CTC words with timing peaks.
4. `align_words_to_ctc(LLM words, CTC words)` → SequenceMatcher maps accurate
   LLM words onto CTC timing, interpolates unmatched words.

**Do not replace this with raw CTC Viterbi over the LLM transcript.** Viterbi
reproduces greedy decoding perfectly when labels match, but it stretches words
badly when the LLM transcript has insertions/deletions (e.g. a duplicated
“go”). The current greedy+SequenceMatcher approach survived real audio. The
Viterbi functions remain in `align.py` for experiments/tests only.

Fallback: if CTC alignment fails or matches <35% of words, proportional
`estimate_word_times` is used and `Transcript.alignment == "estimated"`.

`--ctc-align` is optional because the full 99k-class projection is heavy on CPU.

### Other backends

- `whisper`: `faster-whisper` with native word timestamps. `CLIPKIT_WHISPER_MODEL`
  defaults to `small`.
- `mock`: pass `--transcript file.json`; accepts clipkit transcript JSON, a list
  of segments, or a word list. Used by the demo and e2e tests.

---

## 8. Candidate generation and scoring

`candidates.py`:

- Build sentence-like `Unit`s from words: split on `.?!`, gaps >0.7s, max 32 words.
- Generate windows from each unit start. Evaluate ~5 target lengths (min..max
  step = max(8s, range/4)) rather than every sentence boundary. This keeps a
  2-hour podcast at a few thousand candidates instead of ~70k.
- Add 0.12s pre-roll and 0.22s post-roll, clamped so a clip cannot bleed into a
  neighbouring sentence.
- Score with lexicons, punctuation, WPS, silence ratio, RMS variance, duration.
- NMS uses intersection over the shorter clip (≥0.55) plus content-word Jaccard
  (≥0.60), keep up to `max(3*top_n, 30)`, return top N. IoU alone missed short
  windows nested inside longer ones; the TEDx render exposed that.
- Titles are hints, not final copy. The agent rewrites them.

Weights are in `_build_candidate`: 0.35 hook + 0.25 standalone + 0.25 pacing +
0.15 emotion, plus `(payoff-50)*0.15`, minus warning penalties (0.4.3). The
penalties stop a weak-opening / context-dependent window from scoring the same
as a clean standalone moment.

Hard rejections (in scoring warnings, not automatic removal): weak opening,
back-references, very slow/fast pacing, >55s, <18s.

---

## 9. Captions and render

- `CaptionStyle` defaults: Montserrat ExtraBold 82px, white, green active word
  `#00FF66`, outline 6, shadow 3, y=1450 on 1080×1920, max 3 words / 22 chars.
- ASS colours are `&HAABBGGRR`; `hex_to_ass` handles the swap.
- Each phrase emits one Dialogue per active word, spanning word start → next
  word start, with a pop transform:
  `\c&H..&\fscx112\fscy112\t(0,60,...)\t(60,140,\fscx100...)`.
- Bundled font is copied into `clips/` and `ass=filename=...:fontsdir=.` tells
  libass where to find it. Do not remove `fontsdir=.` or the look drifts.
- Render:
  - `-ss` before `-i`, `-t` after `-i`.
  - crop: `crop=... ,scale=1080:1920:flags=lanczos,fps=30` + `-map 0:v:0`.
  - blur: `filter_complex` (bg blur + fg fit + overlay) + `-map [v]`.
    **Do not add `-map 0:v:0` in blur mode**; it silently wins over `[v]` and
    returns the source resolution. This bug existed and is tested now.
  - split: two `crop` + `scale=1080:960` panels → `vstack=inputs=2` → `ass`;
    map `[v]`, never `0:v:0`.
  - two-pass `loudnorm` targeting `I=-14:TP=-1.5:LRA=11`, AAC 128k, faststart.
- A re-render (caption typo) is ~5s for a short clip; no GPU needed.

### Reframing (0.3)

- Detection: **YuNet** (`face_detection_yunet_2023mar.onnx`, MIT, bundled) on
  frames downscaled to 640px. Haar is the fallback if the model is missing or
  detection errors. Override with `CLIPKIT_FACE_DETECTOR=auto|yunet|haar` and
  `CLIPKIT_YUNET_MODEL`.
- Tracking: Kalman filter on `(cx, cy, w, h)` + ByteTrack-style two-stage
  association. High-score detections (`>=0.5`) match at IoU 0.3; low-score
  detections (`0.1–0.5`) get a second chance at IoU 0.15. New tracks are tentative
  until 3 hits.
- Re-ID: a track missing for 0.8s moves to a lost gallery for up to 5s. A new
  detection re-attaches if a normalized 32×32 grayscale + hue descriptor clears
  `reid_threshold` and a spatial gate. Scene cuts clear active and lost tracks,
  so identities never leak across shots. This is short-occlusion re-ID, not face
  recognition.
- 3+ faces: the top two by `prominence = hits × median face area` get the split;
  the note records that choice. If the pair is wrong, force a single crop.
- Single crop: `build_crop_path` uses EMA (`alpha=0.3`), a 4%-of-width deadzone,
  clamps to the crop range, and resets on scene cuts. `--reframe track` writes
  `clip_00N.crop.cmd` and renders with `sendcmd=f=...,crop=...`. `--reframe face`
  uses a static median crop.
- Crop commands are interpolated to 30 fps even though detection runs at 4 fps;
  large jumps snap so a scene cut does not become a pan.
- Split: requires ≥2 tracks with coverage ≥25%, each ≥`--min-split` seconds,
  **co-temporal overlap ≥`--min-split`**, and horizontal separation ≥18% of
  frame width. Ordered left→top / right→bottom. Each 1080×960 output panel is a
  9:16 source crop scaled to 540×960 over a blurred fill; a 9:8 crop would be
  nearly two-thirds of a 16:9 frame and clamp against the edges.
- Dynamic split panels: each panel has its own `sendcmd` file and a labeled
  `crop@panel0`/`crop@panel1` filter. Commands target `crop@panelN x ...`.
  Both panels are interpolated independently.
- Every plan is saved as `clips/clip_00N.reframe.json`. `ClipResult` records
  `layout` and `reframe_plan`.
- `list_clips` matches `clip_[0-9][0-9][0-9].json`; do not revert to
  `clip_*.json` or it tries to validate `.reframe.json` as a ClipResult.
- If `sendcmd` fails on an exotic FFmpeg build, `render_clip` retries once with
  a static crop/panels.

---

## 10. Agent surfaces

### MCP tools

| Tool | Purpose |
|---|---|
| `clipkit_init` | create workspace, probe video |
| `clipkit_status` | stages + clips |
| `clipkit_transcribe` | Rumble / Whisper / existing transcript |
| `clipkit_analyze` | features + candidates |
| `clipkit_candidates` | read candidates, score breakdown, text |
| `clipkit_get_transcript` | search/slice transcript (context economy) |
| `clipkit_render` | render ids; crop/blur/split/auto + center/face/track reframe |
| `clipkit_inspect` | contact sheet for visual review |
| `clipkit_eval` | precision@k / coverage recall against labels |
| `clipkit_doctor` | toolchain report |

MCP 2.x renamed `FastMCP` to `MCPServer`. `mcp_server.py` imports
`mcp.server.mcpserver.MCPServer` first, then falls back to
`mcp.server.fastmcp.FastMCP` for 1.x.

### Skill

`SKILL.md` is bundled into the wheel via
`[tool.hatch.build.targets.wheel.force-include]`. Use:

```bash
clipkit skill
clipkit skill --install ~/.claude/skills/clipkit
clipkit skill --print
```

### CLI map

```
clipkit init SOURCE [-w DIR] [--copy] [--force]
clipkit probe|transcribe|analyze|candidates|render|inspect|eval|status|doctor
clipkit auto SOURCE [--backend ...] [--ctc-align] [--top N]
clipkit eval -w DIR --labels labels.json
clipkit mcp
clipkit skill [--install DIR]
```

`auto` uses `--transcript` for mock mode. `analyze` will run transcription
automatically if needed.

### Eval

```bash
clipkit eval -w demo.clipkit --labels evals/seed_labels.json
clipkit eval -w demo.clipkit --labels evals/seed_labels.json --json
```

Metrics live in `src/clipkit/evaluation.py`; `evals/harness.py` is a thin
wrapper. `evals/seed_labels.json` is a regression smoke test, not a quality
benchmark. A real benchmark still needs 20–50 owned videos with blind human
labels.

---

## 11. Testing and verification

```bash
python -m pytest -q        # 94 pass
ruff check src tests evals # clean
```

Coverage highlights:

- `test_align.py`: CTC Viterbi, greedy CTC words, insertion interpolation.
- `test_captions.py`: ASS colors, times, escaping, font copy.
- `test_candidates.py`: scoring, NMS, boundary non-bleed.
- `test_media.py`: crop geometry (1920×1080 → 608×1080 at x=656) and the
  dark blur/split fill regression.
- `test_reframe.py`: Kalman/IoU association, two-track split, one-face single,
  sequential-speaker non-split, 3+ prominence policy, re-ID through occlusion,
  appearance mismatch, scene-cut gallery clear, hysteresis, scene-cut jump,
  clamped crop path, empty scene, YuNet coordinate mapping.
- `test_pipeline_e2e.py`: generates a 24s video with FFmpeg, runs mock transcript
  → features → candidates → crop render → blur render → split render →
  dynamic split panels → sendcmd dynamic crop → contact sheet.
- `test_mcp.py`: MCP server exposes all 10 tools.
- `test_ingest.py`: private/loopback URL rejection.
- `test_evals.py`: coverage recall, precision@k, eval reports.

Real validation performed (not in CI):

- Rumble-2-Flash on T4, 11s speech, `alignment=ctc`, 18 words.
- CTC timings: `20` 8.83–9.06s, `degrees.` 9.14–9.37s.
- YuNet detected the test face at score 0.91 on a 640px-wide frame.
- Two-face 1280×720 test video: auto layout chose split, YuNet tracks at x=342
  and x=982, two 404×720 (9:16) panels with blurred fill and independent
  37-point crop paths, output 1080×1920.
- Two moving faces: left panel path moved 264→393 over the clip; right panel
  clamped at the frame edge as expected.
- Moving single face: `--reframe track` produced a crop path from x=355 to
  x=768 over the clip; face stayed centered.
- Occlusion re-ID: one of two faces was covered for 1.5s (black box, real
  descriptor extraction). Tracker kept 2 tracks; the occluded identity kept its
  ID with 29/35 hits and full duration.
- Dynamic split rendered with two labeled crop filters and per-panel `sendcmd`;
  output verified 1080×1920.
- `clipkit eval` on the seed demo: recall 100%, mean best IoU 0.714,
  precision@1/3/5/10 = 100%.
- Rendered 1080×1920 MP4s with burned captions; contact sheets reviewed visually.

---

## 12. Known traps / do not repeat

1. **`transcription` vs `text`** in Rumble’s parsed decode.
2. **Blur map bug**: don’t keep `-map 0:v:0` when mapping `[v]`.
3. **`fontsdir=.`** is required for the bundled font; cwd is the clips dir.
4. **Do not add an LLM call to the core.** The agent is the LLM. This is a
   product constraint, not an implementation detail.
5. **Do not scan the whole transcript per candidate**; use the window’s own
   words. The original O(n²) code took 20s for a 2h synthetic podcast.
6. **Don’t let post-roll bleed into the next sentence.** Pass `next_start` into
   `_build_candidate`.
7. **Don’t use raw CTC Viterbi for alignment** on LLM transcripts with
   insertions. Use greedy CTC words + SequenceMatcher.
8. **Don’t assume `bf16` is unsupported on T4**; `torch.cuda.is_bf16_supported()`
   returned true in this environment and the model loaded. Allow
   `CLIPKIT_RUMBLE_DTYPE` override.
9. **Stage failures can overwrite a good status.** `auto` intentionally reuses a
   `done` transcript when `backend=auto`; explicit backends/`--force` replace it.
10. **`yt-dlp` is optional.** URL ingest is disabled without it; local files
    always work.
11. **SSRF guard is not a sandbox.** It blocks private/loopback before download,
    but DNS rebinding is possible. For multi-tenant use, isolate egress.
12. **Don’t bundle GPL FFmpeg or AGPL YOLO.** FFmpeg is invoked as a subprocess.
    Rumble is Apache-2.0; Montserrat is OFL; OpenCV is Apache-2.0.
13. **`list_clips` must ignore `.reframe.json` sidecars.** It matches
    `clip_[0-9][0-9][0-9].json`; a `clip_*.json` glob breaks `clipkit inspect`.
14. **Split requires co-temporal faces.** Two speakers in sequential shots are
    not a two-shot; require track overlap ≥ `--min-split`.
15. **YuNet is strong, not magic.** Low light, profiles, tiny faces, and heavy
    occlusion still fail. Tracks need 3 hits and die after 0.8s without an
    update; if faces flicker, there is no track. Tune `score_threshold`/`Q`/`R`
    before adding layout complexity.
16. **Dynamic split filters are labeled `crop@panel0`/`crop@panel1`.** The
    `sendcmd` files target those exact names. Rename one without the other and
    the commands silently apply nowhere.
17. **Kalman gains are tuned for ~1080p box sizes.** On 4K sources, re-check
    `Q`/`R` and the `min_hits`/`max_gap` behavior before assuming the tracker is
    broken.
18. **Re-ID is a cheap appearance descriptor, not face recognition.** It uses a
    normalized 32×32 grayscale crop + hue histogram. It covers short occlusions
    (5s gallery) and clears at scene cuts. Similar outfits, long absences, or
    cuts will produce a new ID; do not present it as biometric identity.
19. **3+ faces shows the top two by prominence**, not all speakers. `prominence`
    is `hits × median face area`. If the pair is wrong, force a single crop or
    pick a one-speaker candidate; there is no manual picker yet.
20. **Long YouTube videos are sign-in gated and often SABR-limited.** Stable
    `yt-dlp` may expose only the progressive 360p format. The 2026.11.19 dev
    build downloaded the 1080p `137+140` streams for the FO588 test. Adaptive
    URLs may serve only the first 10 MB, so verify the downloaded resolution with
    `ffprobe` before blaming the renderer. Do not call a 360p fallback a clipkit
    quality bug.
21. **Dynamic layout is currently a manual edit.** The FO588 Lucas clip is three
    renders (crop → split → crop) concatenated at camera cuts. It is not a core
    per-shot layout switch yet; keep that distinction in docs and reports.
22. **Do not weaken the blur/split fill.** `boxblur=80:4` plus
    `eq=brightness=-0.35:saturation=0.65` is deliberate. A softer blur leaves a
    recognisable half-face beside the panel; that was the main real-video defect
    fixed in 0.4.1.
23. **A mocked test is not coverage for the sampling boundary.** Every
    `plan_reframe` test monkeypatches `sample_faces`; only
    `test_sample_faces_decodes_real_frames` runs the real FFmpeg pipe. The 0.4.2
    regression came from a merge that mixed the old OpenCV loop with the new
    FFmpeg setup; `ruff` caught the undefined names and CI now runs it. If you
    change the `sample_faces` signature, update `plan_reframe` and that test.
24. **A 1.5-point score spread is not a ranking.** On the TEDx talk the top ten
    candidates spanned 77.8–79.3 and five of them overlapped heavily, so the
    agent had to pick by reading, not by rank. 0.4.3 adds containment-aware NMS,
    content-word dedupe, and warning penalties. If you touch scoring, re-check
    the spread on a real transcript; do not tune against the synthetic fixtures.
25. **A global split decision needs co-visibility and scene cuts.** The 0.4.5
    two-speaker test showed both failure modes: without scene cuts, a clean
    two-shot was merged into one identity (false single), while cut-heavy clips
    split on a pair that shared the frame for only 27–32% of the clip, leaving a
    panel on empty background. Hard-cut detection is now default-on, and split
    requires 70% pair co-visibility.
26. **Per-shot layout is segment-based, and the boundaries matter.** Each shot
    is rendered with the existing single/split/blur renderer, concatenated, then
    re-encoded once to burn captions and mux audio. Do not merge every layout
    into one filter graph without solving per-shot `sendcmd` first. Keep
    `MIN_SHOT_SECONDS` at 0.6: a 1 s tail merged into a two-shot diluted pair
    co-visibility (56%) and turned a valid split into a blur. The scene
    threshold is 0.25 because 0.35 missed half-frame changes; it is part of the
    features cache key.

---

## 13. Licensing / compliance

- Core: Apache-2.0 (`LICENSE`).
- Font: Montserrat OFL (`src/clipkit/assets/fonts/OFL.txt`).
- Face detector: YuNet MIT (`src/clipkit/assets/models/YUNET_LICENSE.txt`).
- Rumble-2-Flash: Apache-2.0.
- faster-whisper: MIT; Whisper weights: MIT.
- FFmpeg: invoked as an external binary (avoid distributing a GPL build).
- `yt-dlp`: Unlicense; downloading third-party content is the user’s
  responsibility.
- No Ultralytics YOLO (AGPL), no InsightFace models (non-commercial).

---

## 14. Roadmap (ordered by product value)

The detailed 0.5 plan, acceptance criteria, and the FO588 lessons live in
[`FUTURE.md`](FUTURE.md). The short version:

1. **Dynamic per-shot layout**: crop for single shots, split for two-shots, in
   one render (the 0.4.1 Lucas clip is currently three manual segments).
2. **Long-term identity**: re-ID through long absences/re-entry, a manual
   speaker picker, and tuning Kalman `Q`/`R` for 4K. Short-occlusion re-ID and
   the 3+ prominence policy are in place.
3. **Diarization** (pyannote or a licensed alternative) + speaker-aware captions.
4. **Real eval set**: 20–50 videos, blind human labels, precision@3 and
   coverage recall per commit. The harness exists; the data does not.
5. **B-roll / emoji / SFX / music ducking** — table stakes for OpusClip parity.
6. **Throughput**: NVENC, batched Rumble inference, parallel clip rendering.
7. **Aspect ratios** 1:1 and 4:5, platform presets.
8. **Thin web UI** for non-agent users; keep the CLI/MCP core.
9. **Publishing** (YouTube/TikTok OAuth; note TikTok app audit lead time).
10. **CPU-friendly CTC alignment** (restrict vocab / use a smaller aligner).

---

## 15. How to extend

### Add an ASR backend

1. Implement `transcribe(audio, *, language, duration, progress) -> Transcript`
   in `src/clipkit/asr/`.
2. Return `Segment`s with `Word` timings; set `alignment` to `native`, `ctc`, or
   `estimated`.
3. Register it in `asr/__init__.py:get_backend`.
4. Add an optional extra in `pyproject.toml` and a `doctor` check.
5. Add a fixture-based test; do not require the model in CI.

### Add a caption style

Add fields to `CaptionStyle`, use them in `build_ass`, and add a test asserting
the ASS output. Keep the karaoke event model (one event per active word).

### Add a platform preset

Add a `RenderOptions`/CLI preset for resolution and safe area; keep the ASS
`position_y` above the platform’s bottom UI (TikTok/Shorts ~15% of height).

### Add a scoring dimension

Add it to `_score`, include it in `Candidate.scores`, and update the weights in
`_build_candidate` + `SKILL.md` + eval expectations. Do not silently change
weights without an eval run.

---

## 16. Environment validated here

```
Python 3.13.16
FFmpeg/ffprobe 6.1.1
torch 2.14.1+cu130
transformers 5.22.1
numpy 2.4.6
pydantic 2.14.2
mcp 2.3.0
GPU: NVIDIA L4, 24 GB
```

`yt-dlp` is installed, but YouTube refuses unauthenticated downloads from this
environment (`Sign in to confirm you're not a bot`), so a cookies file is needed
for URL ingest from YouTube. Local-file ingest, Rumble ASR, CTC alignment,
crop/blur/split render, MCP calls, and the full test suite were exercised.

---

## 17. One-paragraph handoff

`clipkit` is a local-first, Apache-2.0 clip studio. The deterministic Python
core finds and scores candidate moments, tracks faces, plans single vs
split-screen reframing, and renders branded vertical clips; the agent supplies
editorial judgment through a bundled skill and ten MCP tools. Rumble-2-Flash
provides local ASR, and its auxiliary CTC head provides word timings via greedy
CTC + sequence alignment. The 0.4 slice added appearance re-ID through short
occlusion, a 3+ face prominence policy, `clipkit eval`, and a seed regression
set. The highest-value next step is long-term identity/manual speaker selection
and a real 20–50 video human-labeled benchmark. Do not add LLM calls to the
core, do not bundle GPL FFmpeg or AGPL YOLO, and do not claim a clip is good
without inspecting the contact sheet.
