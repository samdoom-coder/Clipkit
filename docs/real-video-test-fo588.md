# Real-video test — FO588 (Vinh Giang)

**Purpose:** validate the 0.4.x pipeline on a real podcast, not a synthetic demo.

| | |
|---|---|
| Source | https://www.youtube.com/watch?v=BgeNDG5jD60 |
| Processed | first 10:00 of a 2:22:22 episode |
| Source file | 1920×1080, 25 fps, 600.04 s (130 MB) |
| Download | yt-dlp `2026.11.19.232656` dev build, `137+140`, `--download-sections "*0-600"` |
| ASR | Rumble-2-Flash, English, `alignment=ctc` |
| Transcript | 25 segments, 1,883 words |
| Render | 1080×1920, 30 fps, burned captions, −14.15 LUFS |

## What worked

- Rumble + CTC on a real, accented, music-backed podcast: 1,883 words, no model
  or CUDA failures.
- Single-face tracking: 78–85% track coverage on single-speaker shots, no visible
  jitter in contact sheets.
- Pure two-shot split-screen: two persistent tracks, 100% coverage, 774 px
  horizontal separation, independent panels.
- Captions, bundled Montserrat, loudness normalization, contact sheets.

## What looked weak

1. **Half-face in blur/split fills.** Fixed in 0.4.1 with a much stronger,
   darkened blur. The old fill was a zoomed close-up of the source.
2. **Mixed single/two-shot clips.** One layout cannot serve both. The Lucas clip
   was manually built from three renders (crop → split → crop). This is the main
   P0 for 0.5.
3. **Split pair selection.** At 360p the tracker produced duplicate tracks of
   Vinh; top-two-by-prominence picked two Vinh tracks instead of Raj + Vinh.
   At 1080p it worked, but the policy is fragile.
4. **Active speaker.** No diarization; the crop occasionally lands on the
   listener.
5. **Source overlays.** Subscribe bars at ~453–457, 494–498, 533–537 s; sponsor
   overlay at ~568–570 s. They are part of the source and need detection/delogo.
6. **Long-video download.** Stable yt-dlp exposed only 360p; the dev build
   downloaded 1080p. Adaptive URLs can be SABR-limited to the first 10 MB.

## Editorial lessons

The first 10 minutes are mostly a cold open + guest intro + origin-story setup.
The strongest standalone moments were:

- **Self-awareness lesson** (296.29–327.04, 30.8 s): complete practical value.
- **“I broke the mold”** (219.35–243.40, 24.1 s): complete identity story with
  name lower-thirds for context.
- **Toilet → Lucas arc** (507.45–584.27, 77.0 s): complete emotional story; the
  46 s Lucas payoff alone lacks the setup.

The scorer’s top candidates were inside the produced cold open (0:00–3:13), where
chopped audio and text overlays inflate hook/energy scores. The agent must read
the transcript and apply the rubric; rank alone is not enough.

## Evidence

The working test outputs and contact sheets live outside the repo in the test
run’s `output_1080p/` directory. The durable lessons are captured here and in
[`FUTURE.md`](../FUTURE.md).
