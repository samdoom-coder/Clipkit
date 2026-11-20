from clipkit.candidates import _hook_score, build_units, generate_candidates
from clipkit.models import Features, FeatureSeries, Segment, Transcript
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
