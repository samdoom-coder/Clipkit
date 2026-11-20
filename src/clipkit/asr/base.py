from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from clipkit.models import Transcript

# (stage, fraction 0..1, message)
ProgressFn = Callable[[str, float, str], None]


class ASRBackend(Protocol):
    name: str

    def transcribe(
        self,
        audio: Path,
        *,
        language: str | None = None,
        duration: float | None = None,
        progress: ProgressFn | None = None,
    ) -> Transcript: ...


def noop_progress(stage: str, fraction: float, message: str) -> None:
    return None
