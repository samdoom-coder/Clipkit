from clipkit.evaluation import coverage, evaluate, iou
from clipkit.models import Candidate


def _candidate(rank: int, start: float, end: float) -> Candidate:
    return Candidate(
        id=f"c{rank}",
        rank=rank,
        start=start,
        end=end,
        duration=end - start,
        text="example",
    )


def test_iou_and_coverage():
    assert iou(0, 10, 5, 15) == 1 / 3
    assert coverage(0, 10, 0, 10) == 1.0
    assert coverage(0, 5, 0, 10) == 0.5


def test_evaluate_finds_labels_and_precision():
    candidates = [_candidate(1, 10, 20), _candidate(2, 40, 50), _candidate(3, 80, 90)]
    labels = [{"start": 11, "end": 19, "note": "hit"}, {"start": 70, "end": 80, "note": "miss"}]
    report = evaluate(candidates, labels)
    assert report["coverage_recall"] == 0.5
    assert report["precision"]["@1"] == 1.0
    assert report["precision"]["@3"] == 0.333
    assert report["per_label"][0]["covered"] is True
    assert report["per_label"][0]["best_rank"] == 1
    assert report["per_label"][1]["covered"] is False
