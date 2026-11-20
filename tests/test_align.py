import numpy as np

from clipkit.align import (
    CTCWord,
    align_words_to_ctc,
    assign_word_times,
    ctc_greedy_words,
    ctc_viterbi,
    encode_words,
)
from clipkit.util import format_ass_time


class FakeTokenizer:
    def __init__(self, mapping):
        self.mapping = mapping

    def decode(self, ids, **kwargs):
        return self.mapping.get(int(ids[0]), "")


def _log_probs(frames: int, classes: int) -> np.ndarray:
    return np.log(np.full((frames, classes), 0.001, dtype=np.float64))


def test_ctc_viterbi_single_label():
    frames = 6
    log_probs = _log_probs(frames, 3)
    log_probs[:, 0] = np.log(0.97)
    log_probs[1:4, 1] = np.log(0.97)
    log_probs[1:4, 0] = np.log(0.01)
    spans = ctc_viterbi(log_probs, [1], blank=0)
    assert spans is not None
    assert spans[0] == (1, 3)


def test_ctc_viterbi_two_labels():
    frames = 10
    log_probs = _log_probs(frames, 4)
    log_probs[:, 0] = np.log(0.97)
    log_probs[1:3, 1] = np.log(0.97)
    log_probs[1:3, 0] = np.log(0.01)
    log_probs[5:7, 2] = np.log(0.97)
    log_probs[5:7, 0] = np.log(0.01)
    spans = ctc_viterbi(log_probs, [1, 2], blank=0)
    assert spans is not None
    assert spans[0][0] <= 1 and spans[0][1] >= 2
    assert spans[1][0] <= 5 and spans[1][1] >= 6


def test_ctc_viterbi_rejects_impossible_alignment():
    log_probs = _log_probs(2, 3)
    spans = ctc_viterbi(log_probs, [1, 2, 1], blank=0)
    assert spans is None


def test_encode_words_maps_tokens_back_to_words():
    token_ids, token_word = encode_words(["hello", "world"], lambda word: list(range(len(word))))
    assert token_ids == [0, 1, 2, 3, 4, 0, 1, 2, 3, 4]
    assert token_word[:5] == [0, 0, 0, 0, 0]
    assert token_word[5:] == [1, 1, 1, 1, 1]


def test_assign_word_times_is_monotonic():
    words = ["most", "founders", "ignore"]
    spans = [(1, 2), (3, 5), (6, 8)]
    result = assign_word_times(
        words,
        [0, 1, 2],
        spans,
        frame_rate=13.0,
        chunk_start=10.0,
        chunk_end=12.0,
    )
    assert len(result) == 3
    assert result[0].start >= 10.0
    assert result[-1].end <= 12.0
    for previous, current in zip(result, result[1:]):
        assert current.start >= previous.start


def test_ass_time_format():
    assert format_ass_time(0) == "0:00:00.00"
    assert format_ass_time(61.23) == "0:01:01.23"
    assert format_ass_time(3661.5) == "1:01:01.50"


def test_ctc_greedy_words_groups_by_leading_space():
    tokenizer = FakeTokenizer({100: " Hello", 101: " world"})
    class_ids = np.array([2, 0, 0, 2, 1, 1])
    words = ctc_greedy_words(class_ids, tokenizer, [100, 101], blank=2, frame_rate=13.0)
    assert [word.text for word in words] == ["Hello", "world"]
    assert abs(words[0].start - 1 / 13) < 1e-6
    assert abs(words[1].start - 4 / 13) < 1e-6


def test_align_words_to_ctc_maps_and_interpolates_insertions():
    ctc_words = [CTCWord("Hello", 1.0, 1.4), CTCWord("world", 2.0, 2.4)]
    words = align_words_to_ctc(
        ["Hello", "big", "world"], ctc_words, chunk_start=10.0, chunk_end=20.0
    )
    assert words is not None
    assert [word.text for word in words] == ["Hello", "big", "world"]
    assert words[0].start == 11.0
    assert words[2].start == 12.0
    assert words[0].end <= words[1].start <= words[1].end <= words[2].start


def test_align_words_to_ctc_rejects_no_matches():
    ctc_words = [CTCWord("Hello", 1.0, 1.4), CTCWord("world", 2.0, 2.4)]
    assert align_words_to_ctc(["completely", "different"], ctc_words, chunk_start=0, chunk_end=3) is None
