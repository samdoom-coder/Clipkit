import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipkit.features import compute_features
from clipkit.media import write_wav
from clipkit.models import MediaInfo, Segment, Transcript
from clipkit.reframe import FaceBox
from clipkit.softcuts import (
    _merge_close,
    _side_signature,
    activity_runs,
    detect_soft_cuts,
    frame_differences,
    layout_changed,
)


def _times(count: int, fps: float = 8.0) -> list[float]:
    # frame_differences stamps a diff with the time of the later frame.
    return [round((i + 1) / fps, 3) for i in range(count)]


def test_activity_runs_detects_a_bounded_dissolve():
    times = _times(39)
    diffs = [1.0] * 39
    diffs[16:24] = [6.0] * 8
    runs = activity_runs(times, diffs)
    assert len(runs) == 1
    assert runs[0].start == 2.125
    assert runs[0].end == 3.0
    assert 2.4 <= runs[0].midpoint <= 2.7


def test_activity_runs_ignores_an_isolated_spike():
    times = _times(39)
    diffs = [1.0] * 39
    diffs[16] = 30.0  # a hard cut or a one-frame flash
    assert activity_runs(times, diffs) == []


def test_activity_runs_rejects_sustained_motion():
    times = _times(39)
    diffs = [1.0] * 39
    diffs[6:34] = [6.0] * 28  # a pan, not a transition
    assert activity_runs(times, diffs) == []


def test_activity_runs_rejects_a_run_without_quiet_edges():
    times = _times(39)
    diffs = [1.0] * 39
    diffs[:4] = [6.0] * 4
    assert activity_runs(times, diffs) == []


def _box(x: float, *, size: float = 160, y: float = 200) -> FaceBox:
    return FaceBox(x - size / 2, y, size, size)


def test_layout_changed_on_face_count():
    before = _side_signature([[_box(500)], [_box(500)]], width=1920, height=1080)
    after = _side_signature(
        [[_box(400), _box(1500)], [_box(410), _box(1510)]], width=1920, height=1080
    )
    assert before.faces == 1
    assert after.faces == 2
    assert layout_changed(before, after)


def test_layout_changed_on_center_jump():
    before = _side_signature([[_box(400)], [_box(410)]], width=1920, height=1080)
    after = _side_signature([[_box(1400)], [_box(1410)]], width=1920, height=1080)
    assert layout_changed(before, after)


def test_layout_changed_ignores_small_jitter():
    before = _side_signature([[_box(900)], [_box(910)]], width=1920, height=1080)
    after = _side_signature([[_box(930)], [_box(940)]], width=1920, height=1080)
    assert not layout_changed(before, after)


def test_layout_changed_on_face_size_change():
    before = _side_signature([[_box(900, size=120)]], width=1920, height=1080)
    after = _side_signature([[_box(900, size=360)]], width=1920, height=1080)
    assert layout_changed(before, after)


def test_layout_changed_false_without_faces():
    empty = _side_signature([[], []], width=1920, height=1080)
    assert not layout_changed(empty, empty)


def test_merge_close_collapses_duplicate_candidates():
    assert _merge_close([2.0, 2.2, 5.0]) == [2.1, 5.0]


def _synthetic_diffs() -> tuple[list[float], list[float]]:
    times = _times(39)
    diffs = [1.0] * 39
    diffs[16:24] = [6.0] * 8
    return times, diffs


def test_detect_soft_cuts_requires_confirmation(monkeypatch):
    times, diffs = _synthetic_diffs()
    monkeypatch.setattr("clipkit.softcuts._has_opencv", lambda: True)
    monkeypatch.setattr(
        "clipkit.softcuts.frame_differences", lambda *a, **k: (times, diffs)
    )
    monkeypatch.setattr("clipkit.softcuts._layout_changes_at", lambda *a, **k: True)
    cuts = detect_soft_cuts(
        Path("/tmp/source.mp4"), hard_cuts=[], duration=10.0, width=1920, height=1080
    )
    assert len(cuts) == 1
    assert 2.4 <= cuts[0] <= 2.7


def test_detect_soft_cuts_skips_candidate_next_to_hard_cut(monkeypatch):
    times, diffs = _synthetic_diffs()
    monkeypatch.setattr("clipkit.softcuts._has_opencv", lambda: True)
    monkeypatch.setattr(
        "clipkit.softcuts.frame_differences", lambda *a, **k: (times, diffs)
    )
    monkeypatch.setattr("clipkit.softcuts._layout_changes_at", lambda *a, **k: True)
    cuts = detect_soft_cuts(
        Path("/tmp/source.mp4"), hard_cuts=[2.5], duration=10.0, width=1920, height=1080
    )
    assert cuts == []


def test_compute_features_records_soft_cuts(tmp_path, monkeypatch):
    audio = write_wav(tmp_path / "audio.wav", np.zeros(16000, dtype=np.float32), 16000)
    transcript = Transcript(
        language="en", segments=[Segment(start=0, end=1, text="hello")]
    )
    media = MediaInfo(path="/tmp/source.mp4", duration=2.0, width=1280, height=720)
    monkeypatch.setattr("clipkit.features.detect_scene_changes", lambda *a, **k: [1.0])
    monkeypatch.setattr("clipkit.features.detect_soft_cuts", lambda *a, **k: [0.5])
    features = compute_features(audio, transcript, media)
    assert features.scene_changes == [1.0]
    assert features.soft_cuts == [0.5]


def test_detect_soft_cuts_without_opencv_is_a_no_op(monkeypatch):
    monkeypatch.setattr("clipkit.softcuts._has_opencv", lambda: False)
    cuts = detect_soft_cuts(
        Path("/tmp/source.mp4"), hard_cuts=[], duration=10.0, width=1920, height=1080
    )
    assert cuts == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_frame_differences_finds_a_synthetic_dissolve(tmp_path):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    dissolve = tmp_path / "dissolve.mp4"
    for source, pattern in ((first, "testsrc2"), (second, "smptebars")):
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"{pattern}=size=640x360:rate=30",
                "-t",
                "3",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ],
            check=True,
        )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(first),
            "-i",
            str(second),
            "-filter_complex",
            "[0:v][1:v]xfade=transition=fade:duration=1:offset=2,format=yuv420p[v]",
            "-map",
            "[v]",
            "-t",
            "5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(dissolve),
        ],
        check=True,
    )
    times, diffs = frame_differences(dissolve)
    runs = activity_runs(times, diffs)
    assert runs, "expected one dissolve candidate"
    assert any(2.0 <= run.midpoint <= 3.1 for run in runs)
