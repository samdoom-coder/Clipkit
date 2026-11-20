from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from clipkit.errors import WorkspaceError
from clipkit.models import STAGES, ProjectState, StageState
from clipkit.util import (
    atomic_write_json,
    ensure_within,
    now_iso,
    params_hash,
    read_json,
)

PROJECT_FILE = "project.json"


class Workspace:
    """A directory that owns one source video and all derived artifacts.

    Everything is file-based on purpose: an agent can inspect it, a human can
    delete it, and no database is required for the local-first workflow.
    """

    def __init__(self, root: Path, state: ProjectState) -> None:
        self.root = root.resolve()
        self.state = state

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(
        cls,
        root: str | Path,
        source: str | Path,
        *,
        name: str | None = None,
        copy_source: bool = False,
    ) -> Workspace:
        root_path = Path(root).expanduser().resolve()
        if (root_path / PROJECT_FILE).exists():
            raise WorkspaceError(
                f"workspace already initialized at {root_path} (use --force to overwrite project.json)"
            )
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            raise WorkspaceError(f"source does not exist: {source_path}")

        root_path.mkdir(parents=True, exist_ok=True)

        stored_source = source_path
        if copy_source:
            target = root_path / f"source{source_path.suffix.lower()}"
            if target.exists():
                target.unlink()
            try:
                os.link(source_path, target)
            except OSError:
                shutil.copy2(source_path, target)
            stored_source = target

        project_name = name or source_path.stem
        now = now_iso()
        state = ProjectState(
            id=uuid.uuid4().hex[:12],
            name=project_name,
            root=str(root_path),
            source=str(stored_source),
            created_at=now,
            updated_at=now,
            stages={stage: StageState() for stage in STAGES},
        )
        ws = cls(root_path, state)
        ws.save()
        return ws

    @classmethod
    def load(cls, root: str | Path = ".clipkit") -> Workspace:
        root_path = Path(root).expanduser().resolve()
        project_path = root_path / PROJECT_FILE
        if not project_path.exists():
            raise WorkspaceError(
                f"no clipkit workspace at {root_path} — run `clipkit init <video>` first"
            )
        try:
            raw = read_json(project_path)
            state = ProjectState.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - surface a useful message
            raise WorkspaceError(f"could not read {project_path}: {exc}") from exc

        state.root = str(root_path)
        for stage in STAGES:
            state.stages.setdefault(stage, StageState())
        return cls(root_path, state)

    def save(self) -> None:
        self.state.updated_at = now_iso()
        self.state.root = str(self.root)
        atomic_write_json(self.root / PROJECT_FILE, self.state.model_dump(mode="json"))

    # ------------------------------------------------------------------ paths
    @property
    def project_file(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def clips_dir(self) -> Path:
        path = self.root / "clips"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cache_dir(self) -> Path:
        path = self.root / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def source_path(self) -> Path:
        path = Path(self.state.source).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def path(self, relative: str) -> Path:
        return ensure_within(self.root, self.root / relative)

    def artifact(self, name: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def store(self, name: str, data: Any) -> Path:
        path = self.artifact(name)
        atomic_write_json(path, data)
        return path

    # ------------------------------------------------------------------ stages
    def stage_is_valid(self, name: str, params: dict[str, Any] | None = None) -> bool:
        stage = self.state.stage(name)
        if stage.status != "done":
            return False
        if params is not None and stage.params_hash != params_hash(params):
            return False
        return True

    def stage_begin(self, name: str, params: dict[str, Any] | None = None) -> None:
        stage = self.state.stage(name)
        stage.status = "running"
        stage.params_hash = params_hash(params or {})
        stage.started_at = now_iso()
        stage.finished_at = None
        stage.error = None
        self.save()

    def stage_done(self, name: str, artifacts: list[Path] | None = None) -> None:
        stage = self.state.stage(name)
        stage.status = "done"
        stage.finished_at = now_iso()
        stage.error = None
        if artifacts is not None:
            stage.artifacts = [str(p.relative_to(self.root)) if p.is_relative_to(self.root) else str(p) for p in artifacts]
        self.save()

    def stage_fail(self, name: str, error: str) -> None:
        stage = self.state.stage(name)
        stage.status = "failed"
        stage.finished_at = now_iso()
        stage.error = error[:4000]
        self.save()

    def stage_reset(self, name: str) -> None:
        self.state.stages[name] = StageState()
        self.save()

    def require_stage(self, name: str) -> None:
        stage = self.state.stage(name)
        if stage.status != "done":
            raise WorkspaceError(
                f"stage '{name}' is {stage.status}; run the earlier pipeline steps first "
                f"(try `clipkit status`)"
            )

    # ------------------------------------------------------------------ summaries
    def summary(self) -> dict[str, Any]:
        return {
            "workspace": str(self.root),
            "id": self.state.id,
            "name": self.state.name,
            "source": str(self.source_path),
            "duration": self.state.duration,
            "resolution": f"{self.state.width}x{self.state.height}" if self.state.width else None,
            "fps": self.state.fps,
            "language": self.state.language,
            "stages": {
                name: {
                    "status": stage.status,
                    "error": stage.error,
                    "artifacts": stage.artifacts,
                }
                for name, stage in self.state.stages.items()
            },
        }
