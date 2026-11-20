from clipkit.models import Word
from clipkit.text import (
    clamp_words,
    clean_title_hint,
    estimate_word_times,
    join_words,
    split_sentences,
)


def test_estimate_word_times_is_monotonic_and_within_range():
    words = estimate_word_times("Most founders ignore the obvious signal.", 10.0, 14.0)
    assert len(words) == 6
    assert words[0].start == 10.0
    assert abs(words[-1].end - 14.0) < 1e-6
    for previous, current in zip(words, words[1:]):
        assert current.start >= previous.start
        assert current.start >= previous.end - 1e-6
        assert current.end >= current.start


def test_split_sentences_breaks_on_punctuation_and_gaps():
    words = [
        Word(text="This", start=0.0, end=0.3),
        Word(text="is", start=0.3, end=0.5),
        Word(text="one.", start=0.5, end=1.0),
        Word(text="This", start=2.5, end=2.8),
        Word(text="is", start=2.8, end=3.0),
        Word(text="two.", start=3.0, end=3.4),
    ]
    units = split_sentences(words, max_gap=0.7)
    assert len(units) == 2
    assert join_words(units[0]) == "This is one."
    assert join_words(units[1]) == "This is two."


def test_clamp_words_clips_edges():
    words = [Word(text="one", start=0.0, end=2.0), Word(text="two", start=2.0, end=4.0)]
    clipped = clamp_words(words, 1.0, 3.0)
    assert clipped[0].start == 1.0
    assert clipped[1].end == 3.0


def test_clean_title_hint_strips_filler():
    assert clean_title_hint("So, the real reason founders fail is this").startswith("the real")
    assert len(clean_title_hint("word " * 40)) <= 72
