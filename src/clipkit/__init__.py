"""clipkit — an open, agent-native clip studio for long-form video.

The package is deliberately split into a deterministic media core and a thin
agent surface (CLI + MCP). The core never calls an LLM: agents bring their own.
"""

__version__ = "0.4.4"

from clipkit.models import (  # noqa: F401
    Candidate,
    ClipResult,
    Features,
    MediaInfo,
    ProjectState,
    Segment,
    Transcript,
    Word,
)

__all__ = [
    "__version__",
    "Candidate",
    "ClipResult",
    "Features",
    "MediaInfo",
    "ProjectState",
    "Segment",
    "Transcript",
    "Word",
]
