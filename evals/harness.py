#!/usr/bin/env python3
"""Thin CLI wrapper around clipkit.evaluation.

Usage:
    python evals/harness.py --workspace demo.clipkit --labels evals/seed_labels.json
    python evals/harness.py --candidates candidates.json --labels labels.json --json

Labels are human-picked moments:
    {"clips": [{"start": 12.3, "end": 51.8, "note": "best hook"}]}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clipkit.evaluation import evaluate, format_report, load_labels  # noqa: E402
from clipkit.models import Candidate  # noqa: E402
from clipkit.util import read_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate clipkit candidates against human labels")
    parser.add_argument("--workspace")
    parser.add_argument("--candidates")
    parser.add_argument("--labels", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.candidates:
        raw = read_json(Path(args.candidates))
    elif args.workspace:
        raw = read_json(Path(args.workspace) / "candidates.json")
    else:
        parser.error("pass --workspace or --candidates")

    candidates = sorted(
        (Candidate.model_validate(item) for item in raw), key=lambda item: item.rank
    )
    report = evaluate(candidates, load_labels(args.labels))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
