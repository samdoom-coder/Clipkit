# Changelog

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
