from __future__ import annotations

import re

from clipkit.models import Word

_SENTENCE_END = re.compile(r"[.!?。！？][\"')\]]*$")
_TOKEN = re.compile(r"\S+")
_FILLER_PREFIX = re.compile(
    r"^(?:um+|uh+|erm+|ah+|like|you know|i mean|so|okay|ok|right|well)[,.\s]+",
    re.IGNORECASE,
)


def is_sentence_end(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.strip())


def word_weight(token: str) -> float:
    """Approximate spoken duration by syllable-ish character count."""
    letters = sum(ch.isalnum() for ch in token)
    punctuation = 0.25 if re.search(r"[,.;:!?]", token) else 0.0
    return max(1.0, letters * 1.0) + punctuation


def estimate_word_times(text: str, start: float, end: float) -> list[Word]:
    """Distribute a segment's duration across its words.

    This is the fallback when a backend has no word-level alignment. It is
    intentionally simple and deterministic; CTC alignment or Whisper word
    timestamps should replace it whenever available.
    """
    tokens = tokenize(text)
    if not tokens:
        return []
    duration = max(0.12 * len(tokens), end - start)
    weights = [word_weight(token) for token in tokens]
    total = sum(weights)
    cursor = start
    words: list[Word] = []
    for index, (token, weight) in enumerate(zip(tokens, weights)):
        share = duration * (weight / total)
        word_end = end if index == len(tokens) - 1 else min(end, cursor + share)
        words.append(Word(text=token, start=round(cursor, 3), end=round(word_end, 3)))
        cursor = word_end
    return words


def split_sentences(
    words: list[Word],
    *,
    max_words: int = 32,
    max_gap: float = 0.7,
) -> list[list[Word]]:
    """Group word timings into sentence-like units used for clip boundaries."""
    units: list[list[Word]] = []
    current: list[Word] = []

    for word in words:
        if current and word.start - current[-1].end > max_gap:
            units.append(current)
            current = []
        current.append(word)
        if is_sentence_end(word.text) or len(current) >= max_words:
            units.append(current)
            current = []

    if current:
        units.append(current)

    # Merge tiny fragments (< 3 words) into neighbours so a clip never starts
    # on a dangling "Yeah." or "Right."
    merged: list[list[Word]] = []
    for unit in units:
        if merged and len(unit) < 3:
            merged[-1].extend(unit)
        elif merged and len(merged[-1]) < 3:
            merged[-1].extend(unit)
        else:
            merged.append(list(unit))
    return [unit for unit in merged if unit]


def join_words(words: list[Word]) -> str:
    text = " ".join(word.text.strip() for word in words if word.text.strip())
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title_hint(text: str, max_words: int = 12, max_chars: int = 72) -> str:
    text = _FILLER_PREFIX.sub("", text.strip())
    # ASR often leaves one half of a quotation pair when a clip starts or ends
    # mid-quote ("...myself?\""); a lone quote reads as a typo in titles/banners.
    text = text.strip("\"'“”‘’")
    text = re.sub(r"\s+", " ", text).strip(" -–—")
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(",;:") + "…"
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def clamp_words(words: list[Word], start: float, end: float) -> list[Word]:
    """Trim a word list to a clip range, clipping edge words rather than dropping them."""
    out: list[Word] = []
    for word in words:
        if word.end <= start or word.start >= end:
            continue
        out.append(
            Word(
                text=word.text,
                start=max(start, word.start),
                end=min(end, word.end),
                speaker=word.speaker,
            )
        )
    return out


def word_span(words: list[Word]) -> tuple[float, float]:
    if not words:
        return 0.0, 0.0
    return words[0].start, words[-1].end
