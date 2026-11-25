"""Soft-cut detection for per-shot layout.

Hard cuts are a single frame with a large scene score. A dissolve, fade, or
fast wipe is different: the picture changes gradually over several frames, so
the hard-cut detector misses it and two layouts end up inside one shot.

This module finds *bounded* runs of frame-to-frame change. A dissolve is a
short burst of activity that starts and ends; a pan is sustained activity; a
flash is one or two isolated spikes. Candidate runs are then confirmed against
face layout changes, so a lighting change or a camera bump does not fragment a
normal talking-head shot.

The confirmation step needs face detection (the ``faces`` extra). Without it,
soft cuts are skipped rather than guessed at: hard cuts still work, and a wrong
soft cut is worse than a missed one.
"""

from __future__ import annotations

import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipkit.errors import ClipkitError, DependencyError
from clipkit.media import require_ffmpeg
from clipkit.reframe import FaceBox, sample_faces

# Downscaled probe used only to compare consecutive frames. 160x90 keeps the
# pass cheap on long sources while still exposing a dissolve's ramp.
SOFT_CUT_FPS = 8.0
SOFT_CUT_WIDTH = 160
SOFT_CUT_HEIGHT = 90

# A dissolve is typically 0.3-1.5s. Shorter runs are flashes or compression
# noise; longer runs are pans or continuous motion, not shot changes.
MIN_ACTIVITY_SECONDS = 0.25
MAX_ACTIVITY_SECONDS = 1.6
MIN_DIFF_FLOOR = 0.8
BASELINE_MULTIPLIER = 1.5

# A candidate this close to a hard cut is part of that cut's transition.
HARD_CUT_MARGIN = 0.5
# Shots shorter than this are merged by `_shot_boundaries` anyway.
MIN_CUT_SEPARATION = 0.6

# Compare face layout one second either side of the candidate. Samples inside
# the transition are ignored so a half-dissolved face does not vote.
CONFIRM_WINDOW = 1.0
CONFIRM_SAMPLE_FPS = 5.0
CONFIRM_SKIP_SECONDS = 0.5
MIN_CENTER_SHIFT = 0.15
MIN_HEIGHT_RATIO = 1.5


@dataclass(frozen=True)
class ActivityRun:
    start: float
    end: float
    midpoint: float
    strength: float


@dataclass(frozen=True)
class LayoutSignature:
    """A cheap description of what the reframer would do with a frame."""

    faces: int
    centers: tuple[float, ...] = ()
    height: float = 0.0


def frame_differences(
    source: Path,
    *,
    fps: float = SOFT_CUT_FPS,
    width: int = SOFT_CUT_WIDTH,
    height: int = SOFT_CUT_HEIGHT,
    timeout: int = 180,
) -> tuple[list[float], list[float]]:
    """Return (times, mean absolute pixel difference) for downscaled frames.

    Frames are streamed, not buffered, so an hour-long source stays cheap in
    memory. This is a best-effort signal: the caller treats failures as "no
    soft cuts", not as a pipeline error.
    """
    require_ffmpeg()
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-an",
        "-vf",
        f"fps={fps},scale={width}:{height}:flags=fast_bilinear,format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
    frame_bytes = width * height
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    times: list[float] = []
    diffs: list[float] = []
    previous: np.ndarray | None = None
    index = 0
    deadline = time.monotonic() + timeout
    try:
        assert proc.stdout is not None
        while time.monotonic() <= deadline:
            buffer = proc.stdout.read(frame_bytes)
            if len(buffer) < frame_bytes:
                break
            frame = np.frombuffer(buffer, dtype=np.uint8).reshape((height, width))
            if previous is not None:
                times.append(round(index / fps, 3))
                diffs.append(float(np.abs(frame.astype(np.float32) - previous).mean()))
            previous = frame.astype(np.float32)
            index += 1
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return times, diffs


def activity_runs(
    times: list[float],
    diffs: list[float],
    *,
    min_seconds: float = MIN_ACTIVITY_SECONDS,
    max_seconds: float = MAX_ACTIVITY_SECONDS,
) -> list[ActivityRun]:
    """Find bounded bursts of frame change that look like a dissolve.

    The threshold is relative to the source: a mostly static talking head has a
    low baseline, so a gentle dissolve still stands out. A single isolated spike
    is rejected (hard cut or flash); a run that never returns to baseline is
    rejected (pan/zoom).
    """
    if len(times) != len(diffs) or len(diffs) < 3:
        return []
    baseline = statistics.median(diffs)
    threshold = max(MIN_DIFF_FLOOR, baseline * BASELINE_MULTIPLIER)
    step = statistics.median(
        [later - earlier for earlier, later in zip(times, times[1:])] or [1.0 / SOFT_CUT_FPS]
    )

    runs: list[ActivityRun] = []
    start_index: int | None = None
    for index, value in enumerate(diffs):
        if value > threshold:
            if start_index is None:
                start_index = index
            continue
        if start_index is not None:
            _append_run(runs, times, diffs, start_index, index - 1, step, baseline, min_seconds, max_seconds)
            start_index = None
    if start_index is not None:
        _append_run(runs, times, diffs, start_index, len(diffs) - 1, step, baseline, min_seconds, max_seconds)
    return runs


def _append_run(
    runs: list[ActivityRun],
    times: list[float],
    diffs: list[float],
    first: int,
    last: int,
    step: float,
    baseline: float,
    min_seconds: float,
    max_seconds: float,
) -> None:
    # A run that starts at the first sample or ends at the last has no quiet
    # frame on one side, so we cannot say it returned to normal.
    if first <= 0 or last >= len(diffs) - 1 or last - first + 1 < 2:
        return
    duration = times[last] - times[first] + step
    if duration < min_seconds or duration > max_seconds:
        return
    peak = max(diffs[first : last + 1])
    runs.append(
        ActivityRun(
            start=times[first],
            end=times[last],
            midpoint=round((times[first] + times[last]) / 2.0, 3),
            strength=round(peak / max(1e-6, baseline), 3),
        )
    )


def _side_signature(
    samples: list[list[FaceBox]], *, width: int, height: int
) -> LayoutSignature:
    """Median face count plus the positions of a representative frame."""
    if not samples:
        return LayoutSignature(0)
    counts = [len(boxes) for boxes in samples]
    # median_low always returns a count that actually occurred, so the
    # representative frame lookup below cannot miss.
    faces = int(statistics.median_low(counts))
    if faces == 0:
        return LayoutSignature(0)
    representative = next(boxes for boxes in samples if len(boxes) == faces)
    centers = tuple(
        round(box.cx / max(1, width), 3)
        for box in sorted(representative, key=lambda box: box.cx)[:2]
    )
    heights = [
        max(box.h for box in boxes) / max(1, height) for boxes in samples if boxes
    ]
    return LayoutSignature(
        faces=faces,
        centers=centers,
        height=round(statistics.median(heights), 3) if heights else 0.0,
    )


def layout_changed(before: LayoutSignature, after: LayoutSignature) -> bool:
    """True when the two sides of a candidate need different framing.

    A count change is the strong signal (single -> two-shot). A large move or
    size change catches a camera-angle change. Small jitter around one centred
    face is ignored, which is what keeps a lighting change from splitting a
    steady shot.
    """
    if before.faces == 0 and after.faces == 0:
        return False
    if before.faces != after.faces:
        return True
    if not before.centers or not after.centers:
        return True
    if len(before.centers) != len(after.centers):
        return True
    if any(
        abs(left - right) >= MIN_CENTER_SHIFT
        for left, right in zip(before.centers, after.centers)
    ):
        return True
    if before.height > 0 and after.height > 0:
        ratio = max(before.height, after.height) / min(before.height, after.height)
        if ratio >= MIN_HEIGHT_RATIO:
            return True
    return False


def _layout_changes_at(
    source: Path,
    at: float,
    *,
    duration: float,
    width: int,
    height: int,
) -> bool:
    start = max(0.0, at - CONFIRM_WINDOW)
    end = min(duration, at + CONFIRM_WINDOW)
    if end - start < 2 * CONFIRM_SKIP_SECONDS:
        return False
    sample_width = min(max(1, width), 960)
    sample_height = max(2, round(height * sample_width / max(1, width)))
    samples = sample_faces(
        source,
        start=start,
        duration=end - start,
        sample_fps=CONFIRM_SAMPLE_FPS,
        width=sample_width,
        height=sample_height,
    )
    before = [
        boxes
        for offset, boxes in samples
        if start + offset <= at - CONFIRM_SKIP_SECONDS
    ]
    after = [
        boxes
        for offset, boxes in samples
        if start + offset >= at + CONFIRM_SKIP_SECONDS
    ]
    if not before or not after:
        return False
    return layout_changed(
        _side_signature(before, width=sample_width, height=sample_height),
        _side_signature(after, width=sample_width, height=sample_height),
    )


def _has_opencv() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def detect_soft_cuts(
    source: Path,
    *,
    hard_cuts: list[float],
    duration: float,
    width: int,
    height: int,
    timeout: int = 180,
) -> list[float]:
    """Return confirmed soft-cut times, separate from `hard_cuts`.

    Best-effort: a probe/decoder failure or missing OpenCV yields an empty list
    so features and rendering still complete.
    """
    if duration <= 2 * CONFIRM_SKIP_SECONDS or width <= 0 or height <= 0:
        return []
    if not _has_opencv():
        return []
    try:
        times, diffs = frame_differences(source, timeout=timeout)
    except (ClipkitError, DependencyError):
        return []

    confirmed: list[float] = []
    for run in activity_runs(times, diffs):
        if any(abs(run.midpoint - cut) <= HARD_CUT_MARGIN for cut in hard_cuts):
            continue
        try:
            changed = _layout_changes_at(
                source,
                run.midpoint,
                duration=duration,
                width=width,
                height=height,
            )
        except (ClipkitError, DependencyError):
            return []
        if changed:
            confirmed.append(run.midpoint)
    return _merge_close(confirmed)


def _merge_close(cuts: list[float], *, min_separation: float = MIN_CUT_SEPARATION) -> list[float]:
    merged: list[float] = []
    for cut in sorted(cuts):
        if merged and cut - merged[-1] < min_separation:
            merged[-1] = round((merged[-1] + cut) / 2.0, 3)
        else:
            merged.append(round(cut, 3))
    return merged
