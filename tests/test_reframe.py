from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipkit.models import MediaInfo
from clipkit.reframe import (
    FaceBox,
    Track,
    _plausible_face,
    associate_tracks,
    build_crop_path,
    detect_faces,
    detector_status,
    merge_tracks,
    plan_reframe,
    sample_faces,
)
from clipkit.reframe import (
    FaceBox as Box,
)


def _box(cx: float, cy: float = 300, size: float = 120, descriptor=None) -> FaceBox:
    return FaceBox(
        x=cx - size / 2,
        y=cy - size / 2,
        w=size,
        h=size,
        descriptor=descriptor,
    )


def _appearance(index: int) -> np.ndarray:
    vector = np.zeros(1056, dtype=np.float32)
    vector[index] = 1.0
    return vector


def _samples_two_faces(duration: float = 10.0, fps: float = 4.0):
    samples = []
    count = int(duration * fps)
    for index in range(count):
        at = index / fps
        samples.append((at, [_box(420 + index * 2), _box(1500 - index * 2)]))
    return samples


def _media() -> MediaInfo:
    return MediaInfo(path="/tmp/source.mp4", duration=10.0, width=1920, height=1080, has_audio=True)


def test_face_box_iou():
    assert Box(0, 0, 10, 10).iou(Box(0, 0, 10, 10)) == 1.0
    assert Box(0, 0, 10, 10).iou(Box(20, 20, 10, 10)) == 0.0


def test_detector_status_prefers_bundled_yunet(monkeypatch):
    monkeypatch.delenv("CLIPKIT_FACE_DETECTOR", raising=False)
    status = detector_status()
    assert status["backend"] == "yunet"
    assert status["yunet_model"].endswith(".onnx")


def test_detect_faces_uses_yunet_and_maps_coordinates(monkeypatch):
    pytest.importorskip("cv2")

    class FakeDetector:
        def detect(self, frame):
            return True, np.array([[10, 20, 30, 40] + [0] * 10 + [0.91]], dtype=np.float32)

    monkeypatch.setattr("clipkit.reframe._yunet", lambda width, height: FakeDetector())
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    boxes = detect_faces(frame, max_width=100)
    assert len(boxes) == 1
    assert boxes[0].x == 20
    assert boxes[0].y == 40
    assert boxes[0].w == 60
    assert boxes[0].score == pytest.approx(0.91)


def test_associate_tracks_keeps_two_people_separate():
    tracks = associate_tracks(_samples_two_faces())
    assert len(tracks) == 2
    assert tracks[0].duration >= 9
    assert tracks[0].median_x() < tracks[1].median_x()
    assert tracks[0].hits >= 30


def test_reid_keeps_identity_through_occlusion():
    a_desc = _appearance(0)
    b_desc = _appearance(1)
    samples = []
    for index in range(10):
        samples.append((index / 4.0, [_box(400, descriptor=a_desc), _box(1200, descriptor=b_desc)]))
    for index in range(10, 15):  # A is occluded for 1.25s
        samples.append((index / 4.0, [_box(1200, descriptor=b_desc)]))
    for index in range(15, 26):
        samples.append((index / 4.0, [_box(405, descriptor=a_desc), _box(1200, descriptor=b_desc)]))

    tracks = associate_tracks(
        samples, sample_fps=4, frame_width=1920, max_lost=4.0
    )
    assert len(tracks) == 2
    a_track = min(tracks, key=lambda track: track.median_x())
    b_track = max(tracks, key=lambda track: track.median_x())
    assert a_track.duration > 5.5
    assert b_track.duration > 5.5
    assert len({track.id for track in tracks}) == 2


def test_reid_requires_appearance_match():
    a_desc = _appearance(0)
    other_desc = _appearance(2)
    samples = []
    for index in range(10):
        samples.append((index / 4.0, [_box(400, descriptor=a_desc)]))
    for index in range(10, 15):
        samples.append((index / 4.0, []))
    for index in range(15, 26):
        samples.append((index / 4.0, [_box(405, descriptor=other_desc)]))
    tracks = associate_tracks(samples, sample_fps=4, frame_width=1920, max_lost=4.0)
    assert len(tracks) == 2


def test_scene_cut_clears_lost_gallery():
    a_desc = _appearance(0)
    samples = []
    for index in range(10):
        samples.append((index / 4.0, [_box(400, descriptor=a_desc)]))
    for index in range(10, 16):
        samples.append((index / 4.0, []))
    for index in range(16, 26):
        samples.append((index / 4.0, [_box(405, descriptor=a_desc)]))
    tracks = associate_tracks(
        samples, sample_fps=4, frame_width=1920, max_lost=5.0, scene_changes=[3.0]
    )
    assert len(tracks) == 2
    assert tracks[0].id != tracks[1].id


def test_build_crop_path_is_smoothed_and_clamped():
    track = associate_tracks(_samples_two_faces())[0]
    path = build_crop_path(track, duration=10.0, width=1920, height=1080)
    assert path
    assert path[0][0] == 0.0
    assert path[-1][0] == 10.0
    # 1080x1920 crop from 1920x1080 is 608 wide -> centers stay in [304, 1616]
    assert all(304 <= x <= 1616 for _, x in path)
    # The tracked face moves slowly; consecutive crop centers must not jump wildly.
    assert all(abs(b[1] - a[1]) < 80 for a, b in zip(path, path[1:]))


def test_build_crop_path_jumps_at_scene_cut():
    boxes = [(i / 4.0, _box(400)) for i in range(20)] + [
        (5.0 + i / 4.0, _box(1200)) for i in range(20)
    ]
    from clipkit.reframe import Track

    track = Track(id=0, boxes=boxes)
    path = build_crop_path(
        track, duration=10.0, width=1920, height=1080, scene_changes=[5.0]
    )
    jumps = [abs(b[1] - a[1]) for a, b in zip(path, path[1:])]
    assert max(jumps) > 400


def test_plan_reframe_selects_split_for_two_persistent_separated_faces(monkeypatch):
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: _samples_two_faces())
    plan = plan_reframe(
        Path("/tmp/source.mp4"),
        start=0,
        duration=10,
        media=_media(),
        layout="auto",
    )
    assert plan.layout == "split"
    assert len(plan.panel_boxes) == 2
    left, right = plan.panel_boxes
    assert left[0] < right[0]
    assert left[2] == right[2] == 608
    assert left[3] == right[3] == 1080
    assert len(plan.panel_paths) == 2
    assert plan.panel_paths[0] and plan.panel_paths[1]


def test_plan_reframe_falls_back_to_single_for_one_face(monkeypatch):
    samples = [(i / 4.0, [_box(500 + i)]) for i in range(40)]
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "single"
    assert plan.static_center_x is not None
    assert plan.crop_path


def test_plan_reframe_hysteresis_ignores_brief_second_face(monkeypatch):
    samples = []
    for i in range(40):
        at = i / 4.0
        boxes = [_box(500)]
        if 2.0 <= at <= 3.0:  # only 1 second of a second face
            boxes.append(_box(1500))
        samples.append((at, boxes))
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "single"


def test_plan_reframe_shows_top_two_of_three_plus_faces(monkeypatch):
    samples = [
        (i / 4.0, [_box(300), _box(900), _box(1500)]) for i in range(40)
    ]
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "split"
    assert "3 persistent faces; showing top 2 by prominence" in plan.notes[0]
    assert len(plan.panel_boxes) == 2


def test_plan_reframe_does_not_split_sequential_speakers(monkeypatch):
    samples = []
    for i in range(40):
        at = i / 4.0
        samples.append((at, [_box(400 if at < 5 else 1500)]))
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "single"


def test_plan_reframe_requires_the_pair_to_share_the_frame(monkeypatch):
    samples = []
    for i in range(40):
        at = i / 4.0
        boxes = [_box(500)]
        if at >= 6.0:  # only 4 of 10 seconds together
            boxes.append(_box(1500))
        samples.append((at, boxes))
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "single"
    assert any("share the frame" in note for note in plan.notes)


def test_plan_reframe_without_faces_centers(monkeypatch):
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: [(0.0, [])])
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "center"


def test_plan_reframe_ignores_tiny_background_faces(monkeypatch):
    samples = [
        (i / 4.0, [_box(500, size=160), _box(1500, size=18)]) for i in range(40)
    ]
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "single"
    assert plan.static_center_x is not None
    assert plan.static_center_x < 700
    assert len(plan.tracks) == 2


def test_plan_reframe_merges_speaker_tracks_across_scene_cut(monkeypatch):
    samples = []
    for i in range(20):
        samples.append((i / 4.0, [_box(400, size=160)]))
    for i in range(20, 40):
        samples.append((i / 4.0, [_box(1500, size=160)]))
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"),
        start=0,
        duration=10,
        media=_media(),
        layout="auto",
        scene_changes=[5.0],
    )
    assert plan.layout == "single"
    assert plan.crop_path[0][1] < 700
    assert plan.crop_path[-1][1] > 1300
    jumps = [abs(b[1] - a[1]) for a, b in zip(plan.crop_path, plan.crop_path[1:])]
    assert max(jumps) > 500


def test_plan_reframe_falls_back_to_center_for_fragmented_track(monkeypatch):
    samples = [(i / 4.0, []) for i in range(40)]
    for i in range(4):  # only 0.75 s of face at the start
        samples[i] = (i / 4.0, [_box(500, size=160)])
    monkeypatch.setattr("clipkit.reframe.sample_faces", lambda *a, **k: samples)
    plan = plan_reframe(
        Path("/tmp/source.mp4"), start=0, duration=10, media=_media(), layout="auto"
    )
    assert plan.layout == "center"
    assert "low-confidence" in plan.notes[0]


def test_plausible_face_rejects_tiny_and_bad_aspect_boxes():
    assert not _plausible_face(_box(500, size=20), 1080, 0.035)
    assert _plausible_face(_box(500, size=160), 1080, 0.035)
    assert not _plausible_face(FaceBox(0, 0, 240, 40), 1080, 0.035)


def test_merge_tracks_takes_largest_face_at_each_time():
    near = Track(id=0, boxes=[(0.0, _box(400, size=100)), (0.25, _box(400, size=100))])
    far = Track(id=1, boxes=[(0.0, _box(1200, size=200)), (0.25, _box(1200, size=200))])
    merged = merge_tracks([near, far], duration=0.5)
    assert merged.hits >= 2
    assert all(box.cx > 1000 for _, box in merged.boxes)


def test_sample_faces_decodes_real_frames(tmp_path):
    """Guard the real sampling path: unit tests monkeypatch it, so a broken
    ffmpeg read loop used to pass the whole suite."""
    pytest.importorskip("cv2")
    video = tmp_path / "blank.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=160x120:d=1",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
    )

    samples = sample_faces(video, start=0, duration=1.0, sample_fps=4.0)
    assert len(samples) >= 2
    assert all(0.0 <= at <= 1.0 for at, _ in samples)
    assert all(boxes == [] for _, boxes in samples)

    plan = plan_reframe(video, start=0, duration=1.0, layout="auto", sample_fps=4.0)
    assert plan.layout == "center"
