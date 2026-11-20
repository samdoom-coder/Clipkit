from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from clipkit.models import Word


def ctc_viterbi(log_probs: np.ndarray, labels: list[int], blank: int) -> list[tuple[int, int]] | None:
    """Force-align a label sequence to CTC frame posteriors.

    Args:
        log_probs: [frames, classes] log-probabilities (already log-softmaxed).
        labels: target class ids in order, excluding blank.
        blank: blank class id.

    Returns:
        One inclusive (start_frame, end_frame) per label, or None if no path.
    """
    if not labels:
        return []
    frames, classes = log_probs.shape
    if frames == 0 or any(label < 0 or label >= classes for label in labels):
        return None

    length = len(labels)
    states = 2 * length + 1
    ext = np.full(states, blank, dtype=np.int64)
    ext[1::2] = labels

    neg_inf = -1e30
    dp = np.full((frames, states), neg_inf, dtype=np.float64)
    back = np.zeros((frames, states), dtype=np.int8)

    dp[0, 0] = log_probs[0, blank]
    if states > 1:
        dp[0, 1] = log_probs[0, labels[0]]

    for t in range(1, frames):
        previous = dp[t - 1]
        stay = previous
        advance = np.full(states, neg_inf)
        advance[1:] = previous[:-1]
        skip = np.full(states, neg_inf)
        if states > 2:
            skip[2:] = previous[:-2]
        # A skip is only legal across a blank between two different labels.
        allowed = np.zeros(states, dtype=bool)
        allowed[2:] = (ext[2:] != blank) & (ext[2:] != ext[:-2])
        skip = np.where(allowed, skip, neg_inf)

        stacked = np.stack([stay, advance, skip], axis=0)
        choice = np.argmax(stacked, axis=0).astype(np.int8)
        best = stacked[choice, np.arange(states)]
        dp[t] = best + log_probs[t, ext]
        back[t] = choice

    final = states - 1
    if states > 1 and dp[-1, states - 2] > dp[-1, final]:
        final = states - 2
    if dp[-1, final] <= neg_inf / 2:
        return None

    path = np.zeros(frames, dtype=np.int64)
    state = final
    for t in range(frames - 1, -1, -1):
        path[t] = state
        if t > 0:
            state = state - int(back[t, state])

    spans: list[tuple[int, int]] = []
    for index in range(length):
        state = 2 * index + 1
        hits = np.flatnonzero(path == state)
        if hits.size == 0:
            spans.append((0, 0))
        else:
            spans.append((int(hits[0]), int(hits[-1])))
    return spans


def encode_words(
    words: list[str],
    encode: Callable[[str], list[int]],
) -> tuple[list[int], list[int]]:
    """Tokenize words and remember which word each token belongs to."""
    token_ids: list[int] = []
    token_word: list[int] = []
    for index, word in enumerate(words):
        ids = encode(word)
        for token_id in ids:
            token_ids.append(int(token_id))
            token_word.append(index)
    return token_ids, token_word


def assign_word_times(
    words: list[str],
    token_word: list[int],
    spans: list[tuple[int, int]],
    *,
    frame_rate: float,
    chunk_start: float,
    chunk_end: float,
) -> list[Word]:
    """Convert CTC frame spans into word timings, interpolating any gaps.

    Timestamps are absolute: `chunk_start` is where this audio chunk begins in
    the source video.
    """
    raw: list[tuple[float, float] | None] = [None] * len(words)
    for token_index, (start_frame, end_frame) in enumerate(spans):
        if token_index >= len(token_word):
            break
        word_index = token_word[token_index]
        start = chunk_start + start_frame / frame_rate
        end = chunk_start + (end_frame + 1) / frame_rate
        if raw[word_index] is None:
            raw[word_index] = (start, end)
        else:
            prev = raw[word_index]
            raw[word_index] = (min(prev[0], start), max(prev[1], end))

    # Fill words that received no token (rare) by spreading between neighbours.
    result: list[Word] = []
    last_end = chunk_start
    for index, text in enumerate(words):
        span = raw[index]
        if span is None:
            next_start = chunk_end
            for later in raw[index + 1 :]:
                if later is not None:
                    next_start = later[0]
                    break
            span = (last_end, max(last_end + 0.08, min(next_start, chunk_end)))
        start = max(chunk_start, min(span[0], chunk_end))
        end = max(start + 0.05, min(span[1], chunk_end))
        start = max(start, last_end)
        end = max(end, start + 0.05)
        result.append(Word(text=text, start=round(start, 3), end=round(end, 3)))
        last_end = end
    return result


@dataclass
class CTCWord:
    text: str
    start: float
    end: float


def ctc_greedy_words(
    class_ids: np.ndarray,
    tokenizer,
    token_ids: list[int],
    blank: int,
    *,
    frame_rate: float = 13.0,
) -> list[CTCWord]:
    """Decode greedy CTC ids into words with timings.

    The CTC head is less accurate than the LLM decoder, but its frame peaks are
    excellent timing evidence. We use these words only to time the accurate LLM
    transcript, never as the transcript itself.
    """
    words: list[CTCWord] = []
    current: CTCWord | None = None
    previous = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            text = current.text.strip()
            if text:
                words.append(CTCWord(text=text, start=current.start, end=current.end))
            current = None

    for frame, class_id in enumerate(class_ids.tolist()):
        if class_id == previous:
            continue
        previous = class_id
        if class_id == blank:
            continue
        token_id = token_ids[class_id] if 0 <= class_id < len(token_ids) else None
        if token_id is None:
            continue
        piece = tokenizer.decode(
            [int(token_id)], skip_special_tokens=False, clean_up_tokenization_spaces=False
        )
        if not piece:
            continue
        at = frame / frame_rate
        end = (frame + 1) / frame_rate
        if not piece.strip():
            flush()
            continue
        starts_word = piece[:1].isspace()
        if starts_word and current is not None:
            flush()
        if current is None:
            current = CTCWord(text=piece.strip(), start=at, end=end)
        else:
            current.text += piece
            current.end = end
    flush()
    return words


def _normalize_word(text: str) -> str:
    return re.sub(r"[^a-z0-9']", "", text.lower())


def align_words_to_ctc(
    words: list[str],
    ctc_words: list[CTCWord],
    *,
    chunk_start: float,
    chunk_end: float,
) -> list[Word] | None:
    """Map accurate LLM words onto CTC word timings with sequence alignment.

    CTC Viterbi is brittle when the LLM transcript has insertions/deletions
    (a repeated word, a missing filler). SequenceMatcher handles those gaps and
    interpolates only the unmatched words.
    """
    if not words or not ctc_words:
        return None
    left = [_normalize_word(word) for word in words]
    right = [_normalize_word(word.text) for word in ctc_words]
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    matching = [block for block in matcher.get_matching_blocks() if block.size]

    matched = sum(block.size for block in matching)
    if matched < max(2, int(0.35 * len(words))):
        return None

    spans: list[tuple[float, float] | None] = [None] * len(words)
    for block in matching:
        for offset in range(block.size):
            ctc = ctc_words[block.b + offset]
            spans[block.a + offset] = (ctc.start, ctc.end)

    # Interpolate unmatched runs between anchors.
    index = 0
    while index < len(spans):
        if spans[index] is not None:
            index += 1
            continue
        start_index = index
        while index < len(spans) and spans[index] is None:
            index += 1
        end_index = index  # exclusive
        previous_end = spans[start_index - 1][1] if start_index > 0 else 0.0
        next_start = spans[end_index][0] if end_index < len(spans) else max(previous_end, chunk_end - chunk_start)
        if next_start < previous_end:
            next_start = previous_end
        weights = [max(1, len(word)) for word in words[start_index:end_index]]
        total = sum(weights) or 1
        cursor = previous_end
        for offset, weight in enumerate(weights):
            share = (next_start - previous_end) * weight / total
            start = cursor
            end = cursor + share
            spans[start_index + offset] = (start, end)
            cursor = end

    # Monotonic pass, then convert to absolute source time.
    result: list[Word] = []
    cursor = 0.0
    for text, span in zip(words, spans):
        assert span is not None
        start = max(cursor, span[0])
        end = max(start + 0.05, span[1])
        start = min(start, max(0.0, chunk_end - chunk_start - 0.05))
        end = min(end, chunk_end - chunk_start)
        if end <= start:
            end = min(chunk_end - chunk_start, start + 0.05)
        result.append(
            Word(
                text=text,
                start=round(chunk_start + start, 3),
                end=round(chunk_start + end, 3),
            )
        )
        cursor = end
    return result
