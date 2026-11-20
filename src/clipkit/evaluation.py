from __future__ import annotations

import json
from pathlib import Path

from clipkit.models import Candidate


def iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return overlap / union if union > 0 else 0.0


def coverage(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    label = max(1e-6, b_end - b_start)
    return max(0.0, min(a_end, b_end) - max(a_start, b_start)) / label


def evaluate(candidates: list[Candidate], labels: list[dict]) -> dict:
    """Evaluate candidates against human-picked moments.

    Recall is coverage-based (a candidate covering >=60% of a label counts),
    precision@k uses temporal IoU >=0.25. These are intentionally simple and
    stable so rubric changes can be compared across commits.
    """
    if not labels:
        return {"labels": 0, "candidates": len(candidates)}

    per_label = []
    for label in labels:
        start, end = float(label["start"]), float(label["end"])
        scored = [
            (iou(candidate.start, candidate.end, start, end), candidate)
            for candidate in candidates
        ]
        best_iou, best = max(scored, key=lambda item: item[0]) if scored else (0.0, None)
        covered = any(
            coverage(candidate.start, candidate.end, start, end) >= 0.6
            for candidate in candidates
        )
        per_label.append(
            {
                "start": start,
                "end": end,
                "note": label.get("note"),
                "covered": covered,
                "best_iou": round(best_iou, 3),
                "best_rank": best.rank if best and best_iou > 0 else None,
                "best_candidate": best.id if best and best_iou > 0 else None,
            }
        )

    precision = {}
    for k in (1, 3, 5, 10):
        top = candidates[:k]
        if not top:
            precision[f"@{k}"] = 0.0
            continue
        relevant = 0
        for candidate in top:
            if any(
                iou(candidate.start, candidate.end, float(label["start"]), float(label["end"])) >= 0.25
                for label in labels
            ):
                relevant += 1
        precision[f"@{k}"] = round(relevant / len(top), 3)

    return {
        "labels": len(labels),
        "candidates": len(candidates),
        "coverage_recall": round(sum(1 for item in per_label if item["covered"]) / len(labels), 3),
        "mean_best_iou": round(sum(item["best_iou"] for item in per_label) / len(labels), 3),
        "precision": precision,
        "per_label": per_label,
    }


def load_labels(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("clips", [])


def format_report(report: dict) -> str:
    if not report.get("labels"):
        return "no labels provided"
    lines = [
        f"labels:      {report['labels']}",
        f"candidates:  {report['candidates']}",
        f"recall:      {report.get('coverage_recall', 0):.1%}",
        f"mean IoU:    {report.get('mean_best_iou', 0):.3f}",
    ]
    for key, value in report.get("precision", {}).items():
        lines.append(f"precision{key}: {value:.1%}")
    for item in report.get("per_label", []):
        status = "FOUND" if item["covered"] else "MISS "
        lines.append(
            f"  {status} {item['start']:6.1f}-{item['end']:6.1f}  "
            f"IoU={item['best_iou']:.2f}  {item.get('note') or ''}"
        )
    return "\n".join(lines)
