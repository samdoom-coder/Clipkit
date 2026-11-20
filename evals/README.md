# Evals

The clip-selection rubric is the product. This directory keeps it honest.

## Why

OpusClip's moat is not "call an LLM on a transcript" — it is a tuned pipeline plus years of feedback about what actually retains viewers. If clipkit wants to compete, "the clips feel better" is not enough. We need a repeatable measurement.

## Golden set

Collect 20–50 videos across genres (podcast, interview, talk, vlog, stream). For each:

1. A human editor picks the 3–5 moments they would actually post.
2. Record the start/end and a one-line reason.
3. Store as `labels/<video>.json`.

```json
{
  "clips": [
    {"start": 12.3, "end": 51.8, "note": "bold claim + concrete payoff"},
    {"start": 184.0, "end": 226.5, "note": "funny disagreement"}
  ]
}
```

Do not label with clipkit's output in front of you. Label blind, then run the tool.

## Run

```bash
clipkit eval -w demo.clipkit --labels evals/labels/podcast_001.json
# or without the installed CLI:
python evals/harness.py --workspace demo.clipkit --labels evals/labels/podcast_001.json
```

## Seed regression set

`evals/seed_labels.json` labels two known moments in the synthetic 30s demo.
It is deliberately easy: it proves candidate generation, boundaries, and the
harness still work after a refactor. It is **not** a quality benchmark.

```bash
./evals/run_seed.sh /tmp/clipkit-seed.clipkit
```

A real benchmark needs 20–50 videos the user owns, blind human labels, and
per-genre reporting. Use the protocol above; the CLI and metrics are ready.

Metrics:

- **coverage recall** — a label is found if one candidate covers ≥60% of it
- **mean best IoU** — overlap quality of the best candidate per label
- **precision@k** — share of top-k candidates that overlap a label (IoU ≥ 0.25)
- **best rank** — where the best match appears, per label

## What to track

- Changes to the scoring rubric or candidate windowing should **not** reduce recall.
- Prompt/skill changes should improve **precision@3** (the agent's shortlist).
- A new ASR backend should not change clip selection much; if it does, timestamps or transcript quality regressed.
- Re-run on the same labels after every change. Keep a `results/` JSON per commit.

## Next

- Public leaderboard + dataset card.
- Human 1–5 ratings for hook/payoff, not just timestamps.
- Agent A/B: same candidates, different models/skills, same labels.
- Per-genre breakdowns: podcast, interview, gaming, sports, vlog.
