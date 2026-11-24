# clipkit — next phase plan (0.5)

**Status:** 0.5.0 shipped per-shot layout, two-pass loudness, per-clip hooks,
and the split-screen co-visibility guard. Still open from this plan: better
split-pair selection, true active-speaker detection, soft-cut detection, and
overlay handling. The core pipeline now has four real-video validations (FO588
podcast, a TEDx talk, a two-speaker film excerpt, and a 16:9 crop→split→crop
composite).

This file is the working plan for 0.5. It is intentionally concrete: each item
has a user problem, an implementation sketch, acceptance criteria, and tests.

---

## 0. Lessons from the FO588 test

The first 10 minutes are mostly a cold open plus the guest's origin story. The
strongest standalone moments were:

- **Self-awareness lesson** (4:56–5:27): complete practical value.
- **“I broke the mold”** (3:39–4:03): complete identity story, with name lower-thirds.
- **Toilet → Lucas arc** (8:27–9:44): complete emotional story, but it needs ~77s;
  the 46s payoff alone feels contextless.

What broke or looked weak:

1. **Half-face in blur/split fills.** A close-up was used as the fill, so a
   recognisable half-face sat beside the 9:16 panel. Fixed in 0.4.1 with
   `boxblur=80:4` + `eq=brightness=-0.35:saturation=0.65`.
2. **No per-shot layout.** A clip that cuts between a single shot and a two-shot
   cannot use one layout well. The 0.4.1 Lucas clip is three renders concatenated
   at camera cuts.
3. **Split pair chosen by prominence alone.** At 360p the tracker created duplicate
   tracks of the same speaker; the top-two-by-prominence policy picked two tracks
   of Vinh instead of Raj + Vinh. At 1080p it was better, but the logic is still
   fragile.
4. **No active-speaker signal.** The crop occasionally lands on the listener for a
   beat while the other person is speaking. 0.5.0 is conservative: a shot with
   multiple separated faces but no split decision falls back to blur, so the
   listener-crop failure is gone without true ASD. A real model remains 0.6.
5. **Baked-in overlays.** “Raj Shamani Shorts” subscribe bars at ~453–457,
   494–498, 533–537s and the sponsor overlay at ~568–570s are part of the source.
6. **Long-video download is fragile.** Stable `yt-dlp` exposed only 360p; the
   2026.11.19 dev build downloaded 1080p. Adaptive URLs can be SABR-limited to the
   first 10 MB. This is environment work, but it should be documented in ingest.
7. **Small UX issues.** `clipkit render` silently skips existing clips unless
   `--force`; `clipkit inspect --clip` expects a path, not a clip ID.
8. **Rank was not a useful signal.** The TEDx talk's top ten candidates spanned
   77.8–79.3 and five overlapped heavily; `inspect --top` also selects the Nth
   rendered clip, not the Nth candidate. 0.4.3 addressed the duplicate windows
   and the inspect/render UX; calibration is still basic.

The 0.4.3 TEDx render added one more lesson: a hook banner is table stakes for
Shorts/Reels, and it belongs in the caption style (top-of-frame, two lines,
semi-transparent box) rather than in a separate render pass.

The 0.4.5 two-speaker validation (a cut-heavy film excerpt) added a ninth:

9. **A global split needs a co-visibility floor.** Without scene cuts, a clean
   two-shot was merged into one identity and rendered as a single crop; with
   cuts, cut-heavy clips picked pairs that shared the frame for only 27–32% of
   the clip, so one panel held a stale position and showed empty background.
   0.4.5 added the 70% pair co-visibility guard and default-on scene detection;
   0.5.0 adds per-shot layout, so the guard now applies inside each shot.

---

## 1. Dynamic per-shot layout (shipped in 0.5.0)

**Problem:** one clip can contain single shots and two-shots. A single crop cuts
the second person out; a single split duplicates the same person during singles;
a blur keeps everyone but leaves large dark bands.

**Goal:** one render, one output file, layout chosen per shot.

**Shipped approach:** segment-based rather than one filter graph. Each shot is
rendered with the existing crop/split/blur renderer, the segments are
concatenated, and one final pass burns the global ASS, muxes source audio, and
runs two-pass loudnorm. The sidecar is `clips/clip_00N.reframe.json` with
`layout: "per-shot"` and a `shots` array. The sketch below is retained for the
remaining work (soft cuts, per-layout captions, a single graph).

### Implementation sketch

1. Detect shot boundaries. Use the existing scene detector, lower the threshold,
   and add a fallback for soft camera cuts (histogram distance + face-count
   changes). Produce `shot_cuts: list[float]` in `features.json`.
2. For each shot, run `plan_reframe` with the shot's start/duration. Decide:
   - one persistent face → `crop`;
   - two persistent, separated faces → `split`;
   - no reliable face → `center` or `blur`.
3. Build one FFmpeg filter graph:
   - `trim`/`setpts` each shot;
   - apply crop or split filters per shot;
   - `concat=n=N:v=1:a=1` (or render video-only and mux audio once);
   - burn one ASS file whose times are relative to the final clip.
4. Write a sidecar `clip_00N.layout.json` with the shot list, per-shot layout,
   tracks, panel boxes, and notes.

### Acceptance criteria

- A 45–80s clip with crop → split → crop renders in one command.
- Both speakers are fully visible in every two-shot; no panel duplicates the
  same speaker.
- Captions stay at one position across layout switches (or use a per-layout
  style, but do not jump mid-sentence).
- Output is one 1080×1920 MP4 with one continuous audio stream.
- The sidecar explains every layout decision.

### Tests

- Unit: shot list from synthetic cuts; layout choice per shot.
- Integration: 24s video with a single shot → two-shot → single shot; assert one
  output, correct duration, and two panels in the middle.
- Real smoke: FO588 538–584s must match the manual 0.4.1 dynamic edit.

---

## 2. Split-pair selection and track dedupe (P0)

**Problem:** `eligible[:2]` by prominence can pick two tracks of the same person.

**Plan:**

- Deduplicate tracks by median x proximity, appearance-descriptor similarity, and
  temporal overlap before choosing the pair.
- Choose the pair with the best score:
  `separation / width * overlap / min_split * min(prominence)`.
- Require different identities: descriptor similarity below a threshold.
- If no valid pair, fall back to the dominant track's single crop with a note.
- Add a `--pair left,right` / MCP `panel_pair` override for manual correction
  (the roadmap's manual speaker picker, limited to two panels).

**Acceptance:** on the FO588 two-shot, the pair is always Raj + Vinh, never two
Vinh tracks; the sidecar records both track IDs and the score.

---

## 3. Active speaker / diarization (P1)

**Problem:** the crop follows the most prominent face, not the person talking.

**Plan (incremental):**

1. **Cheap first pass:** compute per-face audio-energy correlation. For each
   tracked face, sample the audio RMS during its visible intervals and prefer the
   face whose mouth/energy is active. This needs no new model.
2. **Diarization second pass:** optional `pyannote` backend (license check first);
   assign speaker turns to tracks; use the active track for crop and the inactive
   track for reaction shots.
3. Add `speaker` to `Word`/`Segment` (the model already has an optional field).

**Acceptance:** on a two-person podcast, the crop is on the speaker for ≥90% of
speech frames; listener reaction shots are explicit, not accidental.

---

## 4. Overlay detection / delogo (P1)

**Problem:** baked-in subscribe bars and sponsor lower-thirds land inside clips.

**Plan:**

- Detect static bright rectangles in the lower third across frames (template /
  contour + temporal stability).
- Record them in `features.json` as `overlays: [{start, end, box, kind}]`.
- Candidate scoring penalises windows with overlays; the agent can see the flag.
- Optional render pass: `delogo` or a blurred patch over the box, with a sidecar
  note. Never silently remove content; make it inspectable.
- Add a `--ignore-overlays` flag for users who prefer the original.

**Acceptance:** the FO588 subscribe bars at 453–457, 494–498, 533–537s are
flagged; a clip can be rendered with and without a delogo pass.

---

## 5. Per-layout caption style (P1)

**Problem:** the default `position_y=1450` is right for a single crop but covers
the lower speaker's face in split-screen.

**Plan:**

- Add `CaptionStyle.for_layout(layout)` or a `split` style block:
  `position_y=1600` or `1750`, font 72–76.
- Use one style for the whole output when the layout is dynamic; do not jump
  between y positions at a cut.
- Add tests for the ASS position in crop vs split vs dynamic.

---

## 6. 1080p ingest and download hardening (P1)

**Problem:** stable `yt-dlp` may expose only 360p for long videos; adaptive URLs
can be SABR-limited to the first 10 MB.

**Plan:**

- Add an ingest preflight: after download, `ffprobe` the file and warn if the
  resolution is below the source's best available format.
- Document the yt-dlp dev-build workaround in `README.md` and `context.md`.
- Add `clipkit ingest --max-height 1080` and a `--probe-only` mode.
- Do not bundle yt-dlp; keep it optional.
- If the user provides a local 1080p file, prefer it and skip the download.

**Acceptance:** a 10-minute 1080p download is reproducible; `clipkit doctor`
reports the yt-dlp version and warns when it is older than the tested build.

---

## 7. Eval set and labels (P2)

**Problem:** the seed labels are a regression smoke test, not a quality benchmark.

**Plan:**

- Add 20–50 owned videos with blind human labels (start/end, title, 1–5 score).
- Start with the FO588 windows as a **known-good regression case**, not as a
  quality benchmark:
  - 296.29–327.04 (self-awareness)
  - 219.35–243.40 (broke the mold)
  - 507.45–584.27 (full toilet → Lucas arc)
- Track precision@3, coverage recall, and mean best IoU per commit.
- Keep labels out of the agent's prompt; evaluate after selection.

---

## 8. UX and developer experience (P2)

Completed in 0.4.3:

- `clipkit render`: prints `skipped N existing clip(s)` with `--force` still
  available.
- `clipkit inspect`: accepts a clip ID (`clip_017`) or a path.
- `clipkit inspect --top`: help states that it selects the Nth rendered clip by
  id, not the Nth candidate.

Still open:

- `clipkit candidates`: add `--min-duration` / `--max-duration` filters.
- `clipkit status`: show the source resolution and whether the transcript is CTC
  or estimated.
- `clipkit doctor`: report yt-dlp version and the tested 1080p workaround.
- Add `--fill blur|dark|solid` for blur/split backgrounds. Default stays the
  0.4.1 dark blur; `solid` is useful for very low-resolution sources.

---

## 9. Non-goals for 0.5

- No LLM call in the core. The agent is the editor.
- No GPL FFmpeg or AGPL YOLO.
- No publishing to TikTok/YouTube.
- No web UI yet; keep the CLI/MCP core.
- No face recognition; re-ID remains a short-occlusion appearance descriptor.

---

## 10. Definition of done for 0.5

- One command renders a mixed single/two-shot clip with a per-shot layout.
- The FO588 Lucas clip matches the manual 0.4.1 edit without manual concat.
- Split pair selection is Raj + Vinh on the FO588 two-shot; no duplicate tracks.
- The crop follows the active speaker on ≥90% of speech frames in the test set.
- Overlay flags are visible in candidates and can be delogoed.
- 94+ tests pass; `ruff check` is clean; CI is green.
- `README.md`, `context.md`, and `FUTURE.md` describe the new behavior and the
  remaining limitations.
