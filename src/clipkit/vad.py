from __future__ import annotations

import numpy as np

from clipkit.util import clamp


def compute_rms(
    samples: np.ndarray,
    sample_rate: int,
    *,
    hop: float = 0.25,
    window: float = 0.5,
) -> tuple[list[float], list[float]]:
    """Return (times, rms) on a fixed hop grid. RMS is in normalized amplitude."""
    if samples.size == 0 or sample_rate <= 0:
        return [], []
    hop_samples = max(1, int(hop * sample_rate))
    win_samples = max(1, int(window * sample_rate))
    times: list[float] = []
    values: list[float] = []
    for start in range(0, samples.size, hop_samples):
        chunk = samples[start : start + win_samples]
        if chunk.size == 0:
            break
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        times.append(round(start / sample_rate, 3))
        values.append(rms)
    return times, values


def detect_speech_regions(
    times: list[float],
    rms: list[float],
    *,
    hop: float = 0.25,
    min_speech: float = 0.25,
    min_gap: float = 0.35,
) -> list[list[float]]:
    """Adaptive energy VAD. Dependency-free, good enough for clip boundaries."""
    if not rms:
        return []
    arr = np.asarray(rms, dtype=np.float64)
    noise = float(np.percentile(arr, 20))
    peak = float(np.percentile(arr, 95))
    # Never let the adaptive floor exceed half the peak: a constant loud signal
    # (tone, music bed) is "speech" for our purposes, not silence.
    threshold = min(peak * 0.5, max(noise * 2.5, peak * 0.08, 0.0035))

    flags = arr > threshold
    # Fill short drops inside speech.
    gap_frames = int(round(min_gap / hop))
    i = 0
    while i < len(flags):
        if not flags[i]:
            j = i
            while j < len(flags) and not flags[j]:
                j += 1
            if i > 0 and j < len(flags) and (j - i) <= gap_frames:
                flags[i:j] = True
            i = j
        else:
            i += 1

    regions: list[list[float]] = []
    start_idx: int | None = None
    for index, flag in enumerate(flags):
        if flag and start_idx is None:
            start_idx = index
        elif not flag and start_idx is not None:
            start = times[start_idx]
            end = times[index - 1] + hop
            if end - start >= min_speech:
                regions.append([round(start, 3), round(end, 3)])
            start_idx = None
    if start_idx is not None:
        end = times[-1] + hop
        if end - times[start_idx] >= min_speech:
            regions.append([round(times[start_idx], 3), round(end, 3)])
    return regions


def merge_regions(regions: list[list[float]], gap: float = 0.25) -> list[list[float]]:
    if not regions:
        return []
    ordered = sorted(([float(a), float(b)] for a, b in regions), key=lambda r: r[0])
    merged = [ordered[0][:]]
    for start, end in ordered[1:]:
        if start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def chunk_regions(
    regions: list[list[float]],
    *,
    max_seconds: float = 28.0,
    pad: float = 0.2,
    duration: float | None = None,
) -> list[list[float]]:
    """Group speech into ASR-sized chunks without cutting through speech."""
    expanded = []
    for start, end in merge_regions(regions):
        lo = max(0.0, start - pad)
        hi = end + pad
        if duration is not None:
            hi = min(hi, duration)
        expanded.append([lo, hi])

    chunks: list[list[float]] = []
    current_start: float | None = None
    current_end = 0.0
    for start, end in expanded:
        if current_start is None:
            current_start, current_end = start, end
        elif end - current_start <= max_seconds:
            current_end = end
        else:
            chunks.append([round(current_start, 3), round(current_end, 3)])
            current_start, current_end = start, end
    if current_start is not None:
        chunks.append([round(current_start, 3), round(current_end, 3)])

    # A single long speech region still needs splitting.
    out: list[list[float]] = []
    for start, end in chunks:
        cursor = start
        while end - cursor > max_seconds:
            out.append([round(cursor, 3), round(cursor + max_seconds, 3)])
            cursor += max_seconds
        out.append([round(cursor, 3), round(end, 3)])
    return out


def silent_ratio(regions: list[list[float]], duration: float) -> float:
    if duration <= 0:
        return 0.0
    speech = sum(max(0.0, b - a) for a, b in regions)
    return clamp(1.0 - speech / duration, 0.0, 1.0)
