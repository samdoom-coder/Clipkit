from __future__ import annotations

import os
from pathlib import Path

from clipkit.asr.base import ProgressFn
from clipkit.errors import BackendError
from clipkit.models import Segment, Transcript, Word
from clipkit.text import estimate_word_times


class WhisperBackend:
    """faster-whisper backend. Good fallback, native word timestamps."""

    name = "whisper"

    def __init__(self, model_size: str | None = None) -> None:
        self.model_size = model_size or os.environ.get("CLIPKIT_WHISPER_MODEL", "small")
        self._model = None

    def _load(self):
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise BackendError(
                "faster-whisper is not installed — run `uv pip install 'clipkit[whisper]'`"
            ) from exc

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        self._model = WhisperModel(self.model_size, device=device, compute_type=compute_type)
        return self._model

    def transcribe(
        self,
        audio: Path,
        *,
        language: str | None = None,
        duration: float | None = None,
        progress: ProgressFn | None = None,
    ) -> Transcript:
        model = self._model or self._load()
        if progress:
            progress("transcribe", 0.0, f"loading whisper {self.model_size}")

        try:
            segments_iter, info = model.transcribe(
                str(audio),
                language=language,
                beam_size=5,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"whisper transcription failed: {exc}") from exc

        total = duration or float(getattr(info, "duration", 0.0) or 0.0)
        segments: list[Segment] = []
        for segment in segments_iter:
            words: list[Word] = []
            for word in segment.words or []:
                text = word.word.strip()
                if not text:
                    continue
                words.append(
                    Word(text=text, start=round(float(word.start), 3), end=round(float(word.end), 3))
                )
            if not words:
                words = estimate_word_times(segment.text, float(segment.start), float(segment.end))
            segments.append(
                Segment(
                    start=round(float(segment.start), 3),
                    end=round(float(segment.end), 3),
                    text=segment.text.strip(),
                    words=words,
                )
            )
            if progress and total > 0:
                progress("transcribe", min(1.0, float(segment.end) / total), "transcribing audio")

        return Transcript(
            language=getattr(info, "language", language),
            backend="whisper",
            duration=total,
            alignment="native",
            segments=segments,
        )
