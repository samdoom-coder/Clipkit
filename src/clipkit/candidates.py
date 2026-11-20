from __future__ import annotations

import math
import re
from dataclasses import dataclass

from clipkit.models import Candidate, Features, Transcript, Word
from clipkit.text import clean_title_hint, is_sentence_end, join_words, split_sentences
from clipkit.util import clamp

HOOK_STARTERS = {
    "how", "why", "what", "when", "where", "who", "which",
    "the", "this", "one", "you", "i", "nobody", "everyone", "most",
    "stop", "never", "here", "if", "imagine", "what if",
}
WEAK_STARTERS = {
    "and", "but", "so", "because", "then", "also", "like", "um", "uh",
    "yeah", "okay", "ok", "right", "well", "now", "anyway",
}
PRONOUN_STARTERS = {
    "it", "this", "that", "they", "he", "she", "these", "those", "we", "you",
}
REFERENCE_MARKERS = (
    "as i said", "like i said", "earlier", "before we", "go back",
    "this slide", "you can see", "like i mentioned", "in this video",
    "as you know", "previously",
)
INTENSITY_WORDS = {
    "never", "always", "insane", "crazy", "huge", "massive", "best", "worst",
    "secret", "mistake", "wrong", "truth", "nobody", "everyone", "hate",
    "love", "shocking", "unbelievable", "actually", "literally", "free",
    "million", "billion", "dangerous", "impossible",
}
PAYOFF_PHRASES = (
    "so ", "therefore", "the lesson", "the point", "that's why", "here's how",
    "the trick", "the key", "which means", "bottom line", "what you need to",
    "the reason",
)
LAUGHTER = ("haha", "laughs", "laughter", "lol", "funny")


@dataclass
class Unit:
    start: float
    end: float
    text: str
    words: list[Word]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def build_units(transcript: Transcript) -> list[Unit]:
    words = transcript.iter_words()
    if words:
        sentences = split_sentences(words)
        return [Unit(start=s[0].start, end=s[-1].end, text=join_words(s), words=s) for s in sentences if s]

    units: list[Unit] = []
    for segment in transcript.segments:
        if segment.text.strip():
            units.append(Unit(start=segment.start, end=segment.end, text=segment.text.strip(), words=segment.words))
    return units


def generate_candidates(
    transcript: Transcript,
    features: Features,
    *,
    min_seconds: float = 20.0,
    max_seconds: float = 60.0,
    top_n: int = 10,
) -> list[Candidate]:
    units = build_units(transcript)
    if not units:
        return []

    duration = features.duration or transcript.duration
    # Evaluate a handful of lengths per start, not every sentence boundary.
    # This keeps a 2-hour podcast at a few thousand candidates instead of ~70k.
    step = max(8.0, (max_seconds - min_seconds) / 4.0)
    targets: list[float] = []
    target = min_seconds
    while target < max_seconds:
        targets.append(target)
        target += step
    targets.append(max_seconds)

    raw: list[Candidate] = []
    for start_index in range(len(units)):
        window: list[Unit] = []
        target_index = 0
        for end_index in range(start_index, min(len(units), start_index + 40)):
            window.append(units[end_index])
            window_duration = window[-1].end - window[0].start
            if window_duration > max_seconds + 2:
                break
            if target_index >= len(targets) or window_duration < targets[target_index]:
                continue
            while target_index < len(targets) and window_duration >= targets[target_index]:
                target_index += 1
            previous_end = units[start_index - 1].end if start_index > 0 else None
            next_start = units[end_index + 1].start if end_index + 1 < len(units) else None
            candidate = _build_candidate(
                window,
                features,
                duration,
                previous_end=previous_end,
                next_start=next_start,
            )
            if candidate is not None:
                raw.append(candidate)

    selected = _non_max_suppression(raw, iou_threshold=0.55, limit=max(top_n * 3, 30))
    selected.sort(key=lambda c: c.score, reverse=True)
    selected = selected[:top_n]
    for rank, candidate in enumerate(selected, start=1):
        candidate.rank = rank
    return selected


def _build_candidate(
    window: list[Unit],
    features: Features,
    duration: float,
    *,
    previous_end: float | None = None,
    next_start: float | None = None,
) -> Candidate | None:
    if not window:
        return None
    start = max(0.0, window[0].start - 0.12)
    if previous_end is not None:
        start = max(start, previous_end + 0.02)
    end = min(duration or window[-1].end, window[-1].end + 0.22)
    if next_start is not None:
        end = min(end, next_start - 0.02)
    if end - start < 6:
        return None
    # Use the window's own words: scanning the whole transcript per candidate
    # turns a 2-hour podcast into an O(n^2) slog.
    words = [word for unit in window for word in unit.words]
    text = join_words(words) or " ".join(unit.text for unit in window)
    if not text.strip():
        return None

    scores, reasons, warnings = _score(window, text, words, start, end, features)
    overall = (
        scores["hook"] * 0.35
        + scores["standalone"] * 0.25
        + scores["pacing"] * 0.25
        + scores["emotion"] * 0.15
    )
    overall += (scores["payoff"] - 50) * 0.15
    overall = round(clamp(overall, 0.0, 100.0), 1)

    first_text = window[0].text
    title_source = first_text if len(first_text) >= 18 else " ".join(unit.text for unit in window[:2])
    title = clean_title_hint(title_source, max_words=12, max_chars=72)
    if title:
        title = title[0].upper() + title[1:]

    return Candidate(
        id=f"c{int(start * 1000):07d}_{int(end * 1000):07d}",
        start=round(start, 3),
        end=round(end, 3),
        duration=round(end - start, 3),
        text=text,
        title_hint=title,
        score=overall,
        scores=scores,
        reasons=reasons,
        warnings=warnings,
    )


def _score(
    window: list[Unit],
    text: str,
    words: list[Word],
    start: float,
    end: float,
    features: Features,
) -> tuple[dict[str, float], list[str], list[str]]:
    first = window[0].text
    last = window[-1].text
    first_lower = first.lower().strip()
    word_count = len(words) if words else len(text.split())
    duration = max(0.1, end - start)
    wps = word_count / duration

    hook = _hook_score(first, first_lower, word_count)
    standalone = _standalone_score(first_lower, window, text, last)
    pacing = _pacing_score(duration, wps, words, features, start, end)
    emotion = _emotion_score(text, features, start, end)
    payoff = _payoff_score(last, text)

    reasons: list[str] = []
    warnings: list[str] = []

    if hook >= 75:
        reasons.append(f"strong hook ({hook:.0f}/100)")
    elif hook < 50:
        warnings.append("weak opening line")
    if standalone >= 75:
        reasons.append(f"stands alone ({standalone:.0f}/100)")
    elif standalone < 55:
        warnings.append("may depend on earlier context")
    if 2.4 <= wps <= 4.2:
        reasons.append(f"good pacing ({wps:.1f} words/s)")
    elif wps < 1.8:
        warnings.append(f"slow pacing ({wps:.1f} words/s)")
    elif wps > 5.0:
        warnings.append(f"very fast pacing ({wps:.1f} words/s)")
    if emotion >= 70:
        reasons.append("high emotional intensity")
    if payoff >= 70:
        reasons.append("ends on a payoff")
    if 26 <= duration <= 48:
        reasons.append(f"platform-friendly length ({duration:.0f}s)")
    elif duration > 55:
        warnings.append("longer than typical Shorts/Reels")
    elif duration < 18:
        warnings.append("short clip; may feel incomplete")

    if re.search(r"\b(as i said|earlier|before we|go back)\b", text, re.IGNORECASE):
        warnings.append("contains a back-reference")

    scores = {
        "hook": round(hook, 1),
        "standalone": round(standalone, 1),
        "pacing": round(pacing, 1),
        "emotion": round(emotion, 1),
        "payoff": round(payoff, 1),
    }
    return scores, reasons, warnings


def _hook_score(first: str, first_lower: str, word_count: int) -> float:
    score = 50.0
    first_word = re.sub(r"[^a-z']", "", first_lower.split()[0] if first_lower.split() else "")
    if first_word in HOOK_STARTERS:
        score += 10
    if "?" in first:
        score += 8
    if re.search(r"\d", first):
        score += 8
    intensity = sum(1 for word in INTENSITY_WORDS if re.search(rf"\b{re.escape(word)}\b", first_lower))
    score += min(15, intensity * 4)
    first_len = len(first.split())
    if first_len <= 10:
        score += 8
    elif first_len > 22:
        score -= 8
    if first_word in WEAK_STARTERS:
        score -= 18
    if re.search(r"\b(you|your|we|i)\b", first_lower):
        score += 4
    if re.search(r"\b(but|instead|most people|everyone thinks)\b", first_lower):
        score += 6
    if word_count < 12:
        score -= 6
    return clamp(score, 0, 100)


def _standalone_score(first_lower: str, window: list[Unit], text: str, last: str) -> float:
    score = 70.0
    first_word = re.sub(r"[^a-z']", "", first_lower.split()[0] if first_lower.split() else "")
    if first_word in PRONOUN_STARTERS:
        score -= 12
    if first_word in WEAK_STARTERS:
        score -= 10
    lower = text.lower()
    references = sum(1 for marker in REFERENCE_MARKERS if marker in lower)
    score -= min(24, references * 12)
    if len(window) >= 2:
        score += 8
    if is_sentence_end(last):
        score += 8
    if len(text.split()) < 15:
        score -= 12
    if re.search(r"\b[A-Z][a-z]{2,}\b", window[0].text[1:]) or re.search(r"\d", text):
        score += 5
    return clamp(score, 0, 100)


def _pacing_score(
    duration: float,
    wps: float,
    words: list[Word],
    features: Features,
    start: float,
    end: float,
) -> float:
    score = 100.0 - abs(wps - 3.2) * 18
    score -= _silence_ratio(features, start, end) * 45
    gaps = 0
    for previous, current in zip(words, words[1:]):
        if current.start - previous.end > 1.2:
            gaps += 1
    score -= min(15, gaps * 5)
    score -= abs(duration - 36) * 0.5
    return clamp(score, 0, 100)


def _emotion_score(text: str, features: Features, start: float, end: float) -> float:
    score = 50.0
    lower = text.lower()
    if "!" in text:
        score += 10
    if "?" in text:
        score += 5
    hits = sum(1 for word in INTENSITY_WORDS if re.search(rf"\b{re.escape(word)}\b", lower))
    score += min(20, hits * 4)
    if any(marker in lower for marker in LAUGHTER):
        score += 15
    mean = features.audio.mean(start, end)
    variance = features.audio.variance(start, end)
    if mean > 1e-6:
        coefficient = math.sqrt(variance) / mean
        score += min(15, coefficient * 25)
    return clamp(score, 0, 100)


def _payoff_score(last: str, text: str) -> float:
    score = 50.0
    if is_sentence_end(last):
        score += 10
    lower = last.lower()
    if any(phrase in lower for phrase in PAYOFF_PHRASES):
        score += 15
    if len(last.split()) < 5:
        score -= 8
    if re.search(r"\d", text):
        score += 5
    return clamp(score, 0, 100)


def _silence_ratio(features: Features, start: float, end: float) -> float:
    if end <= start or not features.speech_regions:
        return 0.0
    speech = 0.0
    for region_start, region_end in features.speech_regions:
        speech += max(0.0, min(end, region_end) - max(start, region_start))
    return clamp(1.0 - speech / (end - start), 0.0, 1.0)


def _iou(left: Candidate, right: Candidate) -> float:
    overlap = max(0.0, min(left.end, right.end) - max(left.start, right.start))
    union = max(left.end, right.end) - min(left.start, right.start)
    return overlap / union if union > 0 else 0.0


def _non_max_suppression(
    candidates: list[Candidate], *, iou_threshold: float, limit: int
) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[Candidate] = []
    for candidate in ordered:
        if any(_iou(candidate, other) >= iou_threshold for other in kept):
            continue
        kept.append(candidate)
        if len(kept) >= limit:
            break
    return kept
