# Real-video validation — 0.5.0

Two sources exercise the parts the TEDx talk cannot: cut-heavy footage, two
people in frame, a continuous two-shot, and a 16:9 crop→split→crop sequence.

## Source 1 — cut-heavy film

*Tears of Steel* (Blender Foundation, CC-BY 3.0), 720p, excerpt 190–370 s.
The excerpt has 40 hard cuts in three minutes, including a continuous
two-person shot at 49–56 s.

Rendered with `--layout auto --reframe track` and per-clip hooks:

| Clip | Layout | Result |
|---|---|---|
| `clip_003` | single | every shot resolves to one face; one merged crop path |
| `clip_005` | per-shot | crop shots + split at 14.0–23.5 s |
| `clip_007` | per-shot | crop 0–3.3 s, split 3.3–8.9 s |
| `clip_009` | split | 100% pair co-visibility, both panels tracked |
| `clip_010` | per-shot | crop 0–3.7 s, split 3.7–18.4 s, crop to the end |

Loudness after two-pass normalization: −14.1 to −15.3 LUFS (the 9 s clip is the
outlier; the rest land at −14.x).

## Source 2 — 16:9 acceptance composite

A 17.6 s remote-interview composite: TEDx single shot → ToS continuous
two-shot → TEDx single shot, with the TEDx transcript shifted to match.

`clip_001` is one file with three layouts:

| Shot | Layout |
|---|---|
| 0.0–5.1 s | crop |
| 5.1–13.1 s | split (375 px separation) |
| 13.1–17.6 s | crop |

Captions and the hook banner span the whole file; audio is one stream at
−14.4 LUFS.

## Bugs this caught

1. **Scene cuts were opt-in.** Without them, a clean two-shot collapsed to a
   single crop and a cut-heavy clip falsely split. Detection is now default-on;
   the threshold is 0.25 because 0.35 missed half-frame changes.
2. **A global split needs co-visibility.** Two tracks meeting for 2 s passed the
   old check. Split now requires 70% pair co-visibility.
3. **Global layout is not enough.** 0.5.0 renders per-shot segments, concatenates
   them, and burns one ASS track in the final pass. `--no-per-shot` opts out.
4. **Blur is wrong for ultra-wide sources.** On 2.35:1 footage, blur-padding
   leaves a thin strip of picture. Ultra-wide no-face/ambiguous shots now
   center-crop; 16:9 keeps blur.
5. **Single-pass loudnorm missed by 1–4 LU.** Two-pass measurement fixed it.

## Remaining limits

- True active-speaker detection/diarization: ambiguous multi-face shots fall
  back to blur (16:9) or center crop (ultra-wide). They no longer crop a
  listener by accident, but they do not follow the mouth.
- Hard cuts only; a slow dissolve can still join two layouts in one shot.
- One caption position across crop and split shots.

Workspaces and contact sheets live outside the repo at `/content/twoperson.clipkit`
and `/content/interview.clipkit`; `twoperson.clipkit/VALIDATION.md` has the full
per-clip detail.
