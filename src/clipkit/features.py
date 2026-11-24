from __future__ import annotations

import math
from pathlib import Path

from clipkit.media import detect_scene_changes, read_wav
from clipkit.models import Features, FeatureSeries, MediaInfo, Transcript
from clipkit.vad import compute_rms, detect_speech_regions, silent_ratio


def compute_features(
    audio: Path,
    transcript: Transcript,
    media: MediaInfo,
    *,
    scenes: bool = True,
    hop: float = 0.5,
) -> Features:
    samples, sample_rate = read_wav(audio)
    times, rms = compute_rms(samples, sample_rate, hop=hop, window=max(0.5, hop))
    regions = detect_speech_regions(times, rms, hop=hop)

    duration = media.duration or transcript.duration or (samples.size / sample_rate if sample_rate else 0.0)
    bucket_count = max(1, math.ceil(duration))
    buckets = [0] * bucket_count
    for word in transcript.iter_words():
        midpoint = (word.start + word.end) / 2.0
        index = min(bucket_count - 1, max(0, int(midpoint)))
        buckets[index] += 1
    words_per_second = [round(count / 1.0, 3) for count in buckets]

    scene_changes: list[float] = []
    if scenes:
        scene_changes = detect_scene_changes(Path(media.path))

    return Features(
        duration=round(duration, 3),
        audio=FeatureSeries(hop=hop, rms=[round(value, 6) for value in rms]),
        speech_regions=regions,
        scene_changes=scene_changes,
        words_per_second=words_per_second,
        wps_hop=1.0,
        mean_rms=round(sum(rms) / len(rms), 6) if rms else 0.0,
        peak_rms=round(max(rms), 6) if rms else 0.0,
        silent_ratio=round(silent_ratio(regions, duration), 4),
    )
