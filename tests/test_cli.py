from types import SimpleNamespace

from clipkit.cli import _resolve_clip_arg


def _workspace(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    return SimpleNamespace(root=tmp_path, clips_dir=clips)


def test_resolve_clip_accepts_a_clip_id(tmp_path):
    ws = _workspace(tmp_path)
    target = ws.clips_dir / "clip_002.mp4"
    target.write_bytes(b"x")
    assert _resolve_clip_arg(ws, "clip_002") == target


def test_resolve_clip_accepts_a_workspace_relative_path(tmp_path):
    ws = _workspace(tmp_path)
    target = ws.clips_dir / "clip_002.mp4"
    target.write_bytes(b"x")
    assert _resolve_clip_arg(ws, "clips/clip_002.mp4") == target
