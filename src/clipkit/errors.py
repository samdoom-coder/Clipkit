from __future__ import annotations


class ClipkitError(Exception):
    """Base error with an actionable, human-readable message."""


class WorkspaceError(ClipkitError):
    """The workspace is missing, corrupt, or a path escapes it."""


class StageError(ClipkitError):
    """A pipeline stage failed."""


class BackendError(ClipkitError):
    """An ASR backend is unavailable or failed."""


class DependencyError(ClipkitError):
    """An optional dependency is missing."""
