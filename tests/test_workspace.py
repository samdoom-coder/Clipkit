import pytest

from clipkit.errors import WorkspaceError
from clipkit.workspace import Workspace


def test_create_load_and_stage_cache(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"not really a video")
    ws = Workspace.create(tmp_path / "ws", source, name="demo")

    assert ws.project_file.exists()
    loaded = Workspace.load(tmp_path / "ws")
    assert loaded.state.name == "demo"
    assert loaded.source_path == source.resolve()

    loaded.stage_begin("transcribe", {"backend": "mock"})
    loaded.stage_done("transcribe")
    assert loaded.stage_is_valid("transcribe", {"backend": "mock"})
    assert not loaded.stage_is_valid("transcribe", {"backend": "rumble"})


def test_duplicate_workspace_raises(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"x")
    Workspace.create(tmp_path / "ws", source)
    with pytest.raises(WorkspaceError):
        Workspace.create(tmp_path / "ws", source)


def test_list_clips_ignores_reframe_sidecars(tmp_path):
    from clipkit.pipeline import list_clips

    source = tmp_path / "video.mp4"
    source.write_bytes(b"x")
    ws = Workspace.create(tmp_path / "ws", source)
    (ws.clips_dir / "clip_001.reframe.json").write_text("{}", encoding="utf-8")
    assert list_clips(ws) == []


def test_path_escape_is_rejected(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"x")
    ws = Workspace.create(tmp_path / "ws", source)
    with pytest.raises(ValueError):
        ws.path("../../etc/passwd")
