from clipkit.candidates import (
    _content_jaccard,
    _hook_score,
    _non_max_suppression,
    _overlap_over_min,
    _warning_penalty,
    build_units,
    generate_candidates,
)
from clipkit.models import Candidate, Features, FeatureSeries, Segment, Transcript
from clipkit.text import estimate_word_times


def _transcript() -> Transcript:
    texts = [
        "The biggest mistake founders make is ignoring the obvious signal.",
        "They chase a new feature instead of talking to ten users this week.",
        "That sounds boring, but it is the fastest way to find product market fit.",
        "Most teams learn this lesson after burning a year and a lot of money.",
        "So here is the rule I use before building anything new.",
    ]
    segments = []
    cursor = 0.0
    for index, text in enumerate(texts):
        duration = 6.0
        words = estimate_word_times(text, cursor, cursor + duration - 0.3)
        segments.append(
            Segment(start=cursor, end=cursor + duration - 0.3, text=text, words=words)
        )
        cursor += duration
    return Transcript(backend="test", duration=cursor, alignment="estimated", segments=segments)


def _features() -> Features:
    return Features(
        duration=30.0,
        audio=FeatureSeries(hop=0.5, rms=[0.08] * 60),
        speech_regions=[[0.0, 30.0]],
        words_per_second=[3.0] * 30,
        mean_rms=0.08,
        peak_rms=0.08,
    )


def test_build_units_uses_sentence_boundaries():
    units = build_units(_transcript())
    assert len(units) == 5
    assert units[0].text.endswith("signal.")


def test_generate_candidates_returns_scored_non_overlapping_windows():
    candidates = generate_candidates(_transcript(), _features(), min_seconds=12, max_seconds=20, top_n=5)
    assert candidates
    assert all(0 <= candidate.score <= 100 for candidate in candidates)
    assert all(candidate.duration <= 21 for candidate in candidates)
    assert [candidate.rank for candidate in candidates] == list(range(1, len(candidates) + 1))
    assert len({candidate.id for candidate in candidates}) == len(candidates)
    for left in candidates:
        for right in candidates:
            if left is right:
                continue
            overlap = max(0.0, min(left.end, right.end) - max(left.start, right.start))
            union = max(left.end, right.end) - min(left.start, right.start)
            assert overlap / union < 0.56
            shortest = min(left.duration, right.duration)
            assert overlap / shortest < 0.56


def test_candidates_do_not_bleed_into_neighbouring_sentences():
    units = build_units(_transcript())
    candidates = generate_candidates(_transcript(), _features(), min_seconds=12, max_seconds=20, top_n=5)
    for candidate in candidates:
        for unit in units:
            assert not (unit.start + 0.05 < candidate.start < unit.end - 0.05)
            assert not (unit.start + 0.05 < candidate.end < unit.end - 0.05)


def test_strong_hook_scores_higher_than_weak_hook():
    strong = _hook_score("The biggest mistake founders make is ignoring this.", "the biggest mistake founders make is ignoring this.", 40)
    weak = _hook_score("And then we went to the store for a while.", "and then we went to the store for a while.", 40)
    assert strong > weak


def _candidate(cid: str, start: float, end: float, text: str, score: float = 80.0) -> Candidate:
    return Candidate(
        id=cid,
        start=start,
        end=end,
        duration=end - start,
        text=text,
        score=score,
    )


def test_overlap_over_min_catches_containment():
    outer = _candidate("outer", 0.0, 40.0, "outer")
    inner = _candidate("inner", 20.0, 40.0, "inner")
    assert _overlap_over_min(inner, outer) == 1.0


def test_nms_suppresses_nested_and_duplicate_text_windows():
    outer = _candidate("outer", 0.0, 40.0, "the biggest mistake founders make is ignoring the obvious signal", 95)
    nested = _candidate("nested", 20.0, 40.0, "they chase a new feature instead of talking to users", 90)
    repeat = _candidate(
        "repeat",
        60.0,
        90.0,
        "the biggest mistake founders make is ignoring the obvious signal today",
        85,
    )
    other = _candidate("other", 100.0, 130.0, "most teams learn this after burning a year and money", 70)
    kept = _non_max_suppression(
        [outer, nested, repeat, other],
        overlap_threshold=0.55,
        text_threshold=0.60,
        limit=10,
    )
    ids = {candidate.id for candidate in kept}
    assert "nested" not in ids
    assert "repeat" not in ids
    assert {"outer", "other"} <= ids


def test_content_jaccard_ignores_function_words():
    left = _candidate("a", 0.0, 10.0, "the biggest mistake founders make is ignoring the signal")
    right = _candidate("b", 10.0, 20.0, "biggest mistake founders make is ignoring signal")
    assert _content_jaccard(left, right) > 0.8


def test_warning_penalties_reduce_the_overall_score():
    assert _warning_penalty([]) == 0.0
    assert _warning_penalty(["weak opening line"]) == 4.0
    assert _warning_penalty(["slow pacing (1.2 words/s)"]) == 2.0
    assert _warning_penalty(["weak opening line", "may depend on earlier context"]) == 8.0
