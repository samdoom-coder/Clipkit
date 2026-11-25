#!/usr/bin/env bash
# Build the synthetic demo and evaluate the candidate pipeline against seed labels.
# This is a regression check, not a quality benchmark.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${1:-/tmp/clipkit-seed.clipkit}"

"$ROOT/examples/demo.sh" "$WORK"
cd "$ROOT"
clipkit eval -w "$WORK" --labels "$ROOT/evals/seed_labels.json"
