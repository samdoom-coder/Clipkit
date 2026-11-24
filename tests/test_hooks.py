import json

import pytest

from clipkit.cli import _load_hooks
from clipkit.errors import ClipkitError
from clipkit.models import Candidate, RenderOptions
from clipkit.pipeline import _resolve_hook


def _candidate(cid: str = "c0000000_0001000") -> Candidate:
    return Candidate(
        id=cid,
        start=0.0,
        end=30.0,
        duration=30.0,
        text="a standalone moment",
        score=80.0,
        title_hint="A title hint",
    )


def test_per_candidate_hook_wins_over_global_and_title():
    candidate = _candidate()
    options = RenderOptions(
        hook_text="Global hook",
        hook_from_title=True,
        hooks={candidate.id: "Per-clip hook"},
    )
    assert _resolve_hook(candidate, options) == "Per-clip hook"


def test_global_hook_wins_over_title():
    candidate = _candidate()
    options = RenderOptions(hook_text="Global hook", hook_from_title=True)
    assert _resolve_hook(candidate, options) == "Global hook"


def test_title_hook_is_the_last_fallback():
    candidate = _candidate()
    options = RenderOptions(hook_from_title=True)
    assert _resolve_hook(candidate, options) == "A title hint"
    assert _resolve_hook(candidate, RenderOptions()) is None


def test_hooks_file_loads_and_strips_blank_values(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps({"c1": "  First hook  ", "c2": "   ", "c3": "Third hook"}),
        encoding="utf-8",
    )
    assert _load_hooks(str(path)) == {"c1": "First hook", "c3": "Third hook"}
    assert _load_hooks(None) == {}


def test_hooks_file_rejects_non_object(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text('["not", "an", "object"]', encoding="utf-8")
    with pytest.raises(ClipkitError, match="JSON object"):
        _load_hooks(str(path))


def test_hooks_file_rejects_non_string_values(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text('{"c1": 42}', encoding="utf-8")
    with pytest.raises(ClipkitError, match="strings"):
        _load_hooks(str(path))
