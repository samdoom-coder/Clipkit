# clipkit

[![tests](https://github.com/samdoom-coder/Clipkit/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/samdoom-coder/Clipkit/actions/workflows/tests.yml)

**Open, agent-native clip studio.** Turn long videos into captioned vertical shorts — locally, with your own AI agent doing the editorial thinking.

**v0.4.4** — per-clip hooks, hook banners, candidate diversity, and UX fixes.

```bash
clipkit init podcast.mp4
clipkit transcribe --backend rumble --ctc-align
clipkit analyze
clipkit candidates
clipkit render --ids c0000123_0004567
clipkit inspect --top 1
```

No upload. No credits. No API key. No SaaS.

> Handing this to another agent or contributor? Read [`context.md`](context.md)
> first — it covers architecture, schemas, ASR/alignment details, known traps,
> and the roadmap.

## Why

OpusClip-style tools are black boxes: you upload your video, pay per minute, and hope the algorithm picked the right moment. clipkit flips that:

- **Agent-native.** The clip-selection brain is a `SKILL.md` + MCP server that works with Claude Code, Cursor, Codex, OpenCode, Windsurf, Cline, Zed, or any MCP client. The agent brings the LLM; clipkit brings deterministic media tools.
- **Local-first.** Rumble-2-Flash (0.5B, Apache-2.0) runs speech recognition on your machine. FFmpeg does the rendering. Your source never leaves the box.
- **Inspectable.** Candidates come with a transparent 0–100 score broken into hook, standalone, pacing, emotion, and payoff. No vibes-only ranking.
- **Hackable.** Apache-2.0 core. Every stage is a file on disk. Swap the ASR backend, the caption style, or the scoring rubric.

## Install

Requires Python 3.10+ and **FFmpeg on PATH**.

```bash
git clone https://github.com/your-org/clipkit
cd clipkit
python -m pip install -e ".[mcp]"

# optional: local ASR
python -m pip install -e ".[rumble]"
```

Check the toolchain:

```bash
clipkit doctor
```

Docker:

```bash
docker build -t clipkit .
docker run --rm -v "$PWD:/work" clipkit init /work/video.mp4 -w /work/demo.clipkit
```

No model download or GPU needed for the demo:

```bash
make demo
```

## Quickstart

```bash
# 1. workspace + probe
clipkit init interview.mp4 -w demo.clipkit

# 2. speech recognition (first run downloads ~1 GB)
clipkit transcribe -w demo.clipkit --backend rumble --language English --ctc-align

# 3. deterministic candidate generation
clipkit analyze -w demo.clipkit --min-seconds 20 --max-seconds 60 --top 10

# 4. read the candidates
clipkit candidates -w demo.clipkit --top 10 --json

# 5. render chosen candidates (auto split-screen + tracked crop)
clipkit render -w demo.clipkit --ids c0000123_0004567 --layout auto --reframe track --hook-from-title

# 6. review the contact sheet (clip ids are printed by render)
clipkit inspect -w demo.clipkit --top 1
```

Or run the whole pipeline:

```bash
clipkit auto interview.mp4 -w demo.clipkit --top 3
```

### Reframing

- `--layout crop` — one 9:16 crop (default).
- `--layout auto` — detect faces; use split-screen when two persistent,
  horizontally separated speakers are present, otherwise a single crop.
- `--layout split` — force split-screen if the tracker finds two eligible faces.
- `--layout blur` — keep the full frame and blur-pad the background.
- `--reframe center|face|track` — center, static median face, or a smoothed
  per-frame crop path (sendcmd). `track` follows a moving speaker.
- `--reframe-fps 4 --min-split 2.0` — control tracking density and the
  hysteresis threshold for split-screen.
- `--crop-center X` — source x-coordinate for a static crop. Useful for
  fixed-camera talks and as a safe fallback when tracking confidence is low.

Tracking uses **YuNet** (bundled, MIT) with a Haar fallback, Kalman-predicted
boxes, and ByteTrack-style high/low-confidence association. A cheap appearance
descriptor re-identifies a track through short occlusions; scene cuts clear the
gallery. Faces smaller than 3.5% of the frame height are ignored, and per-shot
tracks for one speaker are merged into a single largest-face path so cut-heavy
stage footage stays on the speaker. If the merged path covers less than 20% of
the clip, the renderer falls back to a static crop instead of following a
fragmented track. Split panels are tracked independently with their own
`sendcmd` crop paths; each panel is a 9:16 crop scaled over a blurred fill. With
3+ persistent faces, the top two by prominence (screen time × face size) get the
split.

Every clip writes `clips/clip_001.reframe.json` with the detected tracks, panel
boxes, crop paths, and layout decision, so reframing is auditable.

### Hook banner

Add a styled banner at the top of the frame:

```bash
clipkit render -w demo.clipkit --ids c0000123_0004567 --hook-text "Stop winging it"
clipkit render -w demo.clipkit --top 3 --hook-from-title   # per-candidate title
```

For different hooks per clip, pass a JSON object keyed by candidate id:

```bash
cat > hooks.json <<'JSON'
{
  "c0000123_0004567": "Stop winging it",
  "c0000789_0012345": "The 30-second fix"
}
JSON
clipkit render -w demo.clipkit --ids c0000123_0004567,c0000789_0012345 --hooks hooks.json
```

Precedence is per-candidate hook → `--hook-text` → `--hook-from-title`. A hook
key that matches no candidate in the workspace prints a warning, so typos do
not silently produce a banner-free clip.

The banner is also available on `clipkit auto` and the MCP `clipkit_render` tool
(`hook_text` / `hook_from_title`). It is part of the caption style, so a style
JSON can brand it:

```json
{
  "hook_font_size": 52,
  "hook_position_y": 90,
  "hook_primary": "#FFFFFF",
  "hook_outline": "#000000",
  "hook_box_alpha": "80",
  "hook_box_padding": 14,
  "hook_max_chars": 36,
  "hook_max_lines": 2,
  "hook_uppercase": true,
  "hook_duration": 0
}
```

`hook_duration: 0` keeps the banner visible for the whole clip; set a positive
value (seconds) to fade it out after the hook. The banner also works without
word captions (`--no-captions --hook-text "Stop winging it"`).

Already have a transcript? Skip ASR:

```bash
clipkit analyze -w demo.clipkit --transcript transcript.json
```

### Authenticated or partial URL ingest

Sites that require a signed-in session (YouTube’s datacenter bot checks, private
feeds) can use a Netscape-format `cookies.txt` or a proxy. Long sources can be
downloaded partially with yt-dlp section syntax:

```bash
clipkit init "https://www.youtube.com/watch?v=..." -w demo.clipkit \
  --cookies cookies.txt --section "*0-1200"   # first 20 minutes

clipkit init "https://example.com/video" -w demo.clipkit \
  --proxy "http://user:pass@host:port"
```

The same options are available on `clipkit auto` and the `clipkit_init` MCP tool.

## Connect your agent

### MCP

Add clipkit to your client (Claude Code, Cursor, Windsurf, Cline, Zed, ...):

```bash
claude mcp add clipkit -- clipkit-mcp
```

Or JSON config:

```json
{
  "mcpServers": {
    "clipkit": {
      "command": "clipkit-mcp",
      "env": { "CLIPKIT_WORKSPACE": "/absolute/path/to/demo.clipkit" }
    }
  }
}
```

Tools:

| Tool | What it does |
|---|---|
| `clipkit_init` | create a workspace, probe the video |
| `clipkit_transcribe` | Rumble / Whisper / existing transcript |
| `clipkit_analyze` | features + ranked candidates |
| `clipkit_candidates` | read candidates and score breakdowns |
| `clipkit_get_transcript` | search or slice the transcript (context-friendly) |
| `clipkit_render` | render chosen candidates as 1080×1920 MP4 |
| `clipkit_inspect` | contact sheet for visual review |
| `clipkit_eval` | score candidates against human labels |
| `clipkit_status` | pipeline state |
| `clipkit_doctor` | toolchain check |

### Skill

Copy [`SKILL.md`](SKILL.md) into your agent's skills directory. It contains the editorial rubric, workflow, platform specs, and guardrails. The MCP server is the hands; the skill is the taste.

```bash
# Claude Code
mkdir -p ~/.claude/skills/clipkit
cp SKILL.md ~/.claude/skills/clipkit/SKILL.md

# Codex / OpenCode / other agents: point them at SKILL.md
# (see your client's "skills" or "instructions" configuration)
```

Cursor, Windsurf, Cline, and Zed can use the MCP server directly; the skill is
optional but strongly recommended — without it, the agent tends to pick clips
by score alone.

## How it works

```
agent (Claude Code / Cursor / Codex / OpenCode)
├── SKILL.md            editorial rubric + workflow
└── MCP tools           typed, deterministic
      └── clipkit core
            ├── ffmpeg / ffprobe
            ├── Rumble-2-Flash + CTC forced alignment
            ├── energy VAD + scene detection
            ├── candidate generation + heuristic scoring
            ├── YuNet/Haar tracking + split-screen reframe
            └── ASS karaoke captions + libx264 render
```

Stages are resumable and cached by content hash. Word-level timings, candidate scores, and clip metadata are plain JSON in the workspace:

```
demo.clipkit/
├── project.json
├── media.json
├── audio.wav
├── transcript.json
├── features.json
├── candidates.json
├── clips/
│   ├── clip_001.mp4
│   ├── clip_001.ass
│   └── clip_001.json
└── inspect/
    └── clip_001.jpg
```

## Rumble-2-Flash

[`Reson8/Rumble-2-Flash`](https://huggingface.co/Reson8/Rumble-2-Flash) is a 0.5B-parameter ASR model under Apache-2.0: 45 languages, 6.6% WER on the English Open ASR Leaderboard, and fast enough to run locally. It does **not** emit word timestamps, so clipkit uses the model's auxiliary CTC head (13 fps) for forced alignment when you pass `--ctc-align`.

If you prefer Whisper: `python -m pip install -e ".[whisper]"` and use `--backend whisper`.

Validated on an NVIDIA T4: the model loads in ~12s (fp16), an 11-second speech
clip transcribes in ~3s, and the CTC aligner produces word timings without a
separate alignment model. On CPU, `--ctc-align` is noticeably slower because the
CTC head projects every encoder frame across a 99k-token vocabulary.

Known v1 limits: no speaker diarization; re-ID handles short occlusions via a
cheap appearance descriptor, not long absences or re-entry; 3+ faces show the
top two by prominence, with no manual speaker picker; no B-roll/emoji/SFX. See
the roadmap.

## Evaluate the rubric

"These clips feel better" is not a metric. Label 20–50 videos with the moments a
human editor would actually post, then measure candidate recall and precision:

```bash
clipkit eval -w demo.clipkit --labels evals/seed_labels.json
# or:
python evals/harness.py --workspace demo.clipkit --labels evals/seed_labels.json
```

See [`evals/README.md`](evals/README.md) for the labeling protocol and the
precision@k / coverage-recall metrics. `evals/seed_labels.json` is a regression
smoke test, not a quality benchmark. Track a real labeled set per commit; treat
the rubric like code.

## Status

Working v1:

- [x] ingest local files and http(s) URLs (`yt-dlp`)
- [x] resumable workspace + stage cache
- [x] Rumble-2-Flash, faster-whisper, and existing-transcript backends
- [x] CTC forced alignment + proportional fallback
- [x] energy VAD, audio features, optional scene detection
- [x] deterministic candidate generation + transparent scoring
- [x] YuNet face detection (MIT, bundled) + Haar fallback
- [x] Kalman-predicted, ByteTrack-style tracking with smoothed crop paths
- [x] auto split-screen for 2 persistent, separated speakers (with hysteresis)
- [x] appearance re-ID through short occlusion; 3+ face prominence policy
- [x] dynamic split panels, face-aware center crop, auditable reframe plans
- [x] ASS karaoke captions, brand style JSON
- [x] hook banner (`--hook-text`, `--hook-from-title`, `--hooks` per clip, styleable)
- [x] containment-aware candidate dedupe + content-word diversity
- [x] 1080×1920 render with loudness normalization
- [x] CLI + 10-tool MCP server + agent skill
- [x] contact-sheet inspection; eval CLI + seed regression set
- [x] 83 tests, including an ffmpeg end-to-end test
- [x] GitHub Actions CI (`ruff` + `pytest`) on every push/PR

Next:

- [ ] long-term re-ID / re-entry + manual speaker picker
- [ ] B-roll / emoji / SFX / music ducking
- [ ] diarization (pyannote) and speaker-aware captions
- [ ] NVENC + batched ASR for scale
- [ ] real 20–50 video human-labeled benchmark
- [ ] thin local web UI
- [ ] publishing integrations (YouTube/TikTok OAuth)

## Legal & safety

- Only process video you own or have permission to use. Downloading third-party content may violate platform terms.
- URL ingest blocks private/loopback addresses, but run untrusted downloads in a network-restricted worker for real isolation.
- Transcript text is untrusted input. The skill instructs agents never to follow instructions found inside media.
- clipkit does not upload or publish anything.

## License

Apache-2.0. See [LICENSE](LICENSE).

Bundled assets: Montserrat is OFL (`src/clipkit/assets/fonts/OFL.txt`); YuNet is
MIT (`src/clipkit/assets/models/YUNET_LICENSE.txt`). Rumble-2-Flash is
Apache-2.0.
