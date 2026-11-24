# Changelog

## 0.5.0 — per-shot layout and two-pass loudness

- **Per-shot layout:** with `--layout auto`, clipkit now plans crop / split /
  blur independently for every shot and renders one continuous file: render
  each shot, concatenate, then mux source audio and burn one global ASS track.
  A clip that cuts between a single and a two-shot no longer holds one layout
  across the whole clip. `--no-per-shot` restores the old whole-clip decision.
- **16:9 acceptance test:** a 17.6 s remote-interview composite renders
  crop → split → crop in one file; the sidecar records each shot's layout and
  reason.
- **Two-pass loudnorm:** measure the source segment first, then normalize with
  the measured values. The ToS validation moved from −17.7 to −14.1/−15.3 LUFS.
- **Active-speaker safety:** when a shot has multiple separated faces but no
  split decision, it falls back to blur instead of cropping a possible
  listener. True active-speaker detection remains future work.
- **Scene threshold:** lowered from 0.35 to 0.25 because half-frame changes were
  missed; the threshold is part of the features cache key.
- Tests: 94 pass (was 84); `ruff` clean.

## 0.4.5 — split-screen quality on cut-heavy footage

- **Split coverage guard:** a clip only splits when the chosen pair shares the
  frame for at least 70% of the clip, in addition to the 2s hysteresis and 18%
  horizontal-separation checks. Two tracks that met only briefly — or that came
  from different shots — used to hold a panel on empty background for the rest
  of the clip.
- **Scene cuts on by default:** `analyze` / `auto` now run hard-cut detection
  unless `--no-scenes` is passed. The two-speaker validation showed that
  omitting cuts turned a clean two-shot into a single crop and a cut-heavy clip
  into a false split; clearing track IDs at cuts matters.
- **Validation:** rendered a continuous two-shot split-screen (100% pair
  co-visibility, both panels tracked) and confirmed the guard rejects cut-heavy
  narrative clips. Tests: 84 pass (was 83); `ruff` clean.

## 0.4.4 — per-clip hooks

- **Per-clip hooks:** `render` / `auto` accept `--hooks hooks.json`, a JSON
  object mapping candidate ids to banner text. One render now produces a batch
  with a different hook per clip, instead of one command per hook. Precedence is
  per-candidate hook → `--hook-text` → `--hook-from-title`; unknown candidate
  ids warn instead of failing silently. The MCP `clipkit_render` tool accepts a
  `hooks` object directly.
- Tests: 83 pass (was 77); `ruff` clean.

## 0.4.3 — hook banners, candidate diversity, UX

- **Hook banner:** `render` / `auto` accept `--hook-text "..."` or
  `--hook-from-title`; the banner sits at the top of the frame, is styled by the
  new `hook_*` caption-style fields (bold uppercase, semi-transparent box, two
  lines max), and the MCP `clipkit_render` tool exposes `hook_text` /
  `hook_from_title`. The banner still renders with `--no-captions`, since it is
  burned through the ASS track.
- **Candidate diversity:** selection now suppresses containment duplicates
  (overlap over the shorter clip) and near-duplicate content (content-word
  Jaccard), not just low-IoU pairs. Warning flags also apply small score
  penalties, so the top list is less compressed and rank is more meaningful.
- **UX:** `clipkit inspect --clip clip_002` accepts a clip id; `render` reports
  `skipped N existing clip(s)` instead of counting them as rendered; the
  `inspect --top` help now states that it selects the Nth rendered clip by id.
  Title hints also strip orphaned quotation marks left by ASR at clip
  boundaries (a real defect in the TEDx render).
- Tests: 77 pass (was 65); `ruff` clean.

## 0.4.2 — face-tracking hotfix

- **Face-tracking hotfix:** a bad merge left the old OpenCV seek loop inside
  `sample_faces` while the new FFmpeg reader was in place, so `--layout auto`,
  `--layout split`, `--reframe face`, and `--reframe track` failed at runtime.
  Restored the single-pass FFmpeg decoder and added a real-video regression test
  that exercises the sampling path instead of monkeypatching it.
- **CI:** added GitHub Actions (`.github/workflows/tests.yml`) to run `ruff` and
  the full `pytest` suite on every push and pull request. The original merge
  regression would have failed the lint step.
- **URL ingest:** `init` / `auto` accept `--cookies`, `--proxy`, and `--section`
  (e.g. `*0-1200` for the first 20 minutes); the `clipkit_init` MCP tool passes
  the same options to yt-dlp.
- **Cache correctness:** `transcribe --force` now re-extracts audio, and probe /
  audio stages are keyed by source size + mtime. This fixes a stale truncated
  audio file when a source is still uploading.
- **Manual framing:** `--crop-center X` on `render` / `auto` and `crop_center` on
  the MCP render tool for fixed-camera talks.
- **Tracking hardening:** ignore faces below 3.5% of frame height, reject
  implausible face-box aspect ratios, merge per-shot speaker tracks into one
  largest-face path, and fall back to a static crop when merged coverage is below
  20%.
- **Faster tracking:** face samples are decoded in one sequential ffmpeg pass
  instead of seeking for every sample. A 60s clip now samples in ~27s on the T4
  test box, roughly 9× faster than the seek-per-sample path.
- Added regression tests for URL option passthrough, tiny background faces,
  merged tracks across scene cuts, low-confidence fallback, and real FFmpeg face
  sampling. 65 tests pass.

## 0.4.1 — real-video hardening

- **Blur/split fill fix:** the fill is now heavily blurred and darkened
  (`boxblur=80:4` + `eq=brightness=-0.35:saturation=0.65`). A 16:9 close-up no
  longer leaves a recognisable half-face beside a 9:16 foreground/panel.
- Added a regression test for both filter builders (`tests/test_media.py`).
- Added `FUTURE.md`: the 0.5 plan for dynamic per-shot layout, split-pair
  selection, active-speaker choice, overlay detection, caption styles, eval data,
  and UX.
- Added `docs/real-video-test-fo588.md`: the 1080p FO588 test notes and the
  editorial lessons.
- Updated `README.md` and `context.md` for 0.4.1 and the new real-video findings.
- No public API changes. No new dependencies.

### Real-video test (FO588, first 10:00)

- Source: 1920×1080, 25 fps, downloaded with the 2026.11.19 yt-dlp dev build.
- Transcript: Rumble-2-Flash + CTC, 25 segments, 1,883 words.
- Renders: 1080×1920, 30 fps, burned captions, −14 LUFS.
- Findings: single-face tracking is good; pure two-shot split works; mixed
  single/two-shot clips need a per-shot layout; 360p tracking creates duplicate
  tracks; split pair selection by prominence alone is fragile; source overlays
  need detection.

## 0.4.0 — re-ID / eval slice

- Appearance re-ID through short occlusion.
- 3+ face prominence policy.
- `clipkit eval` precision@k / coverage recall.
- Seed regression labels.
