import shutil
import subprocess
from pathlib import Path

import pytest

from clipkit.media import probe_media
from clipkit.models import Candidate, MediaInfo, RenderOptions
from clipkit.pipeline import (
    ShotLayout,
    _render_shot_layout,
    _shot_boundaries,
    _use_per_shot,
    plan_shot_layout,
)
from clipkit.reframe import ReframePlan, TrackSummary
from clipkit.workspace import Workspace


def test_shot_boundaries_merge_tiny_shots():
    assert _shot_boundaries([1.0, 1.5], 20.0) == [(0.0, 1.5), (1.5, 20.0)]
    assert _shot_boundaries([0.5], 20.0) == [(0.0, 20.0)]
    assert _shot_boundaries([], 20.0) == [(0.0, 20.0)]


def test_plan_shot_layout_decides_per_shot(monkeypatch):
    candidate = Candidate(
        id="c0000000_0012000",
        start=100.0,
        end=112.0,
        duration=12.0,
        text="two shots",
    )
    media = MediaInfo(
        path="/tmp/source.mp4",
        duration=12.0,
        width=1920,
        height=1080,
        has_audio=True,
    )

    def fake_plan_reframe(*args, **kwargs):
        if kwargs["start"] >= 106:
            return ReframePlan(
                layout="split",
                panel_boxes=[[0, 0, 500, 1000], [1200, 0, 500, 1000]],
                panel_paths=[],
            )
        return ReframePlan(layout="single", static_center_x=500.0, crop_path=[[0.0, 500.0]])

    monkeypatch.setattr("clipkit.pipeline.plan_reframe", fake_plan_reframe)
    decisions = plan_shot_layout(
        Path("/tmp/source.mp4"),
        candidate,
        [6.0],
        RenderOptions(),
        media,
    )
    assert [decision.layout for decision in decisions] == ["crop", "split"]
    assert [(decision.start, decision.end) for decision in decisions] == [(0.0, 6.0), (6.0, 12.0)]
    assert _use_per_shot(decisions)


def test_uniform_crop_does_not_need_per_shot():
    plan = ReframePlan(layout="single", static_center_x=400.0)
    decisions = [
        ShotLayout(0.0, 5.0, "crop", plan),
        ShotLayout(5.0, 10.0, "crop", plan),
    ]
    assert _use_per_shot(decisions) is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_render_shot_layout_concatenates_crop_and_split(tmp_path):
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    ws = Workspace.create(tmp_path / "ws", video)
    media = probe_media(video)
    candidate = Candidate(
        id="c0000000_0006000",
        start=0.0,
        end=6.0,
        duration=6.0,
        text="two shots",
    )
    decisions = [
        ShotLayout(0.0, 3.0, "crop", ReframePlan(layout="center")),
        ShotLayout(
            3.0,
            6.0,
            "split",
            ReframePlan(
                layout="split",
                panel_boxes=[[0, 0, 202, 360], [438, 0, 202, 360]],
            ),
        ),
    ]
    destination = ws.clips_dir / "clip_001.mp4"
    _render_shot_layout(
        ws,
        candidate,
        destination,
        None,
        decisions,
        RenderOptions(layout="auto", captions=False, normalize_audio=False),
        media,
    )
    info = probe_media(destination)
    assert (info.width, info.height) == (1080, 1920)
    assert 5.8 <= info.duration <= 6.2
    assert info.has_audio is False


def test_plan_shot_layout_blurs_ambiguous_multi_face_shot(monkeypatch):
    candidate = Candidate(
        id="c0000000_0006000",
        start=0.0,
        end=6.0,
        duration=6.0,
        text="two faces, no split",
    )
    media = MediaInfo(
        path="/tmp/source.mp4",
        duration=6.0,
        width=1920,
        height=1080,
        has_audio=True,
    )
    plan = ReframePlan(
        layout="single",
        static_center_x=600.0,
        tracks=[
            TrackSummary(id=0, coverage=0.8, duration=5.0, hits=20, center_x=500, center_y=300),
            TrackSummary(id=1, coverage=0.7, duration=5.0, hits=18, center_x=1400, center_y=300),
        ],
    )
    monkeypatch.setattr("clipkit.pipeline.plan_reframe", lambda *a, **k: plan)
    decisions = plan_shot_layout(
        Path("/tmp/source.mp4"), candidate, [], RenderOptions(), media
    )
    assert [decision.layout for decision in decisions] == ["blur"]


def test_plan_shot_layout_crops_close_tracks_as_one_speaker(monkeypatch):
    candidate = Candidate(
        id="c0000000_0006000",
        start=0.0,
        end=6.0,
        duration=6.0,
        text="fragmented single speaker",
    )
    media = MediaInfo(
        path="/tmp/source.mp4",
        duration=6.0,
        width=1920,
        height=1080,
        has_audio=True,
    )
    plan = ReframePlan(
        layout="single",
        static_center_x=600.0,
        tracks=[
            TrackSummary(id=0, coverage=0.8, duration=5.0, hits=20, center_x=500, center_y=300),
            TrackSummary(id=1, coverage=0.7, duration=5.0, hits=18, center_x=600, center_y=300),
        ],
    )
    monkeypatch.setattr("clipkit.pipeline.plan_reframe", lambda *a, **k: plan)
    decisions = plan_shot_layout(
        Path("/tmp/source.mp4"), candidate, [], RenderOptions(), media
    )
    assert [decision.layout for decision in decisions] == ["crop"]


def test_plan_shot_layout_uses_crop_for_ultrawide_no_face(monkeypatch):
    candidate = Candidate(
        id="c0000000_0006000",
        start=0.0,
        end=6.0,
        duration=6.0,
        text="b-roll",
    )
    media = MediaInfo(
        path="/tmp/source.mp4",
        duration=6.0,
        width=1280,
        height=534,
        has_audio=True,
    )
    plan = ReframePlan(layout="center")
    monkeypatch.setattr("clipkit.pipeline.plan_reframe", lambda *a, **k: plan)
    decisions = plan_shot_layout(
        Path("/tmp/source.mp4"), candidate, [], RenderOptions(), media
    )
    assert [decision.layout for decision in decisions] == ["crop"]
    assert "ultra-wide" in decisions[0].reason
