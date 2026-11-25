#!/usr/bin/env bash
# End-to-end demo with a synthetic video and a hand-written transcript.
# No model download, no GPU required.
set -euo pipefail

WORK="${1:-./demo.clipkit}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$WORK"
ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "testsrc2=size=1280x720:rate=30" \
  -f lavfi -i "sine=frequency=220:sample_rate=48000" \
  -t 30 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest \
  "$WORK/source.mp4"

python3 - <<'PY' "$WORK/transcript.json"
import json, sys
texts = [
    "The biggest mistake founders make is ignoring the obvious signal.",
    "They chase a new feature instead of talking to ten users this week.",
    "That sounds boring but it is the fastest way to find product market fit.",
    "Most teams learn this lesson after burning a year and a lot of money.",
    "So here is the rule I use before building anything new.",
    "Talk to users first and the roadmap writes itself.",
]
segments = [{"start": i * 5.0, "end": i * 5.0 + 4.9, "text": t} for i, t in enumerate(texts)]
json.dump({"language": "en", "segments": segments}, open(sys.argv[1], "w"), indent=2)
PY

cd "$ROOT"
clipkit auto "$WORK/source.mp4" -w "$WORK" \
  --transcript "$WORK/transcript.json" \
  --min-seconds 15 --max-seconds 25 --top 2 --hook-from-title
clipkit inspect -w "$WORK" --top 1
