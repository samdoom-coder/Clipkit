from __future__ import annotations

from pathlib import Path

from clipkit.asr.base import ASRBackend, ProgressFn
from clipkit.errors import BackendError


def get_backend(name: str = "auto", *, transcript_path: Path | None = None, ctc_align: bool = False) -> ASRBackend:
    """Resolve an ASR backend without importing heavy optional dependencies."""
    name = (name or "auto").lower()

    if transcript_path is not None:
        from clipkit.asr.mock import MockBackend

        return MockBackend(transcript_path)

    if name == "mock":
        raise BackendError("the mock backend needs --transcript <file.json>")

    if name == "whisper":
        from clipkit.asr.whisper import WhisperBackend

        return WhisperBackend()

    if name == "rumble":
        from clipkit.asr.rumble import RumbleBackend

        return RumbleBackend(ctc_align=ctc_align)

    if name != "auto":
        raise BackendError(f"unknown ASR backend '{name}' (choose auto, rumble, whisper, mock)")

    # auto: prefer an installed lightweight backend, then Rumble if its weights
    # are already cached. Never trigger a surprise 1 GB download.
    try:
        import faster_whisper  # noqa: F401

        from clipkit.asr.whisper import WhisperBackend

        return WhisperBackend()
    except ImportError:
        pass

    try:
        from huggingface_hub import try_to_load_from_cache

        from clipkit.asr.rumble import RumbleBackend

        model_id = RumbleBackend.model_id
        cached = try_to_load_from_cache(model_id, "config.json")
        if isinstance(cached, str):
            return RumbleBackend(ctc_align=ctc_align)
    except Exception:  # noqa: BLE001 - optional probe
        pass

    raise BackendError(
        "no ASR backend available. Install one:\n"
        "  uv pip install 'clipkit[rumble]'   # Reson8 Rumble-2-Flash (recommended, Apache-2.0)\n"
        "  uv pip install 'clipkit[whisper]'  # faster-whisper\n"
        "or pass --transcript transcript.json to use an existing transcript."
    )


__all__ = ["ASRBackend", "ProgressFn", "get_backend"]
