from __future__ import annotations

from pathlib import Path

from clipkit.asr.base import ProgressFn
from clipkit.errors import BackendError
from clipkit.models import Segment, Transcript, Word
from clipkit.text import estimate_word_times
from clipkit.util import read_json


class MockBackend:
    """Use an existing transcript instead of running speech recognition.

    Accepts clipkit's transcript JSON, a list of segments, or a flat word list.
    Useful for tests, demos, and agents that already have a transcript.
    """

    name = "mock"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def transcribe(
        self,
        audio: Path,
        *,
        language: str | None = None,
        duration: float | None = None,
        progress: ProgressFn | None = None,
    ) -> Transcript:
        if not self.path.exists():
            raise BackendError(f"transcript not found: {self.path}")
        data = read_json(self.path)

        if isinstance(data, dict) and "segments" in data:
            transcript = Transcript.model_validate(data)
        elif isinstance(data, list):
            transcript = Transcript(segments=self._segments_from_list(data))
        elif isinstance(data, dict) and "words" in data:
            transcript = Transcript(segments=self._segments_from_words(data["words"]))
        else:
            raise BackendError(
                "unsupported transcript shape: expected {segments:[...]}, {words:[...]}, or a list of segments"
            )

        for segment in transcript.segments:
            if not segment.words:
                segment.words = estimate_word_times(segment.text, segment.start, segment.end)
        transcript.backend = "mock"
        if all(segment.words for segment in transcript.segments):
            provided = isinstance(data, dict) and (
                data.get("alignment") in ("native", "ctc")
                or any(segment.get("words") for segment in data.get("segments", []))
            )
            transcript.alignment = "native" if provided else "estimated"
        transcript.duration = duration or max(
            (segment.end for segment in transcript.segments), default=0.0
        )
        if language:
            transcript.language = language
        return transcript

    @staticmethod
    def _segments_from_list(items: list[dict]) -> list[Segment]:
        segments = []
        for item in items:
            words = [Word.model_validate(word) for word in item.get("words", [])]
            segments.append(
                Segment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=str(item.get("text", "")),
                    words=words,
                    speaker=item.get("speaker"),
                )
            )
        return segments

    @staticmethod
    def _segments_from_words(items: list[dict]) -> list[Segment]:
        words = [Word.model_validate(word) for word in items]
        if not words:
            return []
        text = " ".join(word.text for word in words)
        return [Segment(start=words[0].start, end=words[-1].end, text=text, words=words)]
