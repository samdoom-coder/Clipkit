from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

STAGES = ("probe", "audio", "transcribe", "features", "candidates", "render")


class Word(BaseModel):
    """A single word with millisecond-level timing."""

    model_config = ConfigDict(extra="ignore")

    text: str
    start: float
    end: float
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Segment(BaseModel):
    """A contiguous transcript segment. `words` is the source of truth for captions."""

    model_config = ConfigDict(extra="ignore")

    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    speaker: str | None = None


class Transcript(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str | None = None
    backend: str = "unknown"
    duration: float = 0.0
    alignment: Literal["native", "ctc", "estimated"] = "estimated"
    segments: list[Segment] = Field(default_factory=list)

    def iter_words(self) -> list[Word]:
        words: list[Word] = []
        for segment in self.segments:
            words.extend(segment.words)
        words.sort(key=lambda w: (w.start, w.end))
        return words

    def words_between(self, start: float, end: float) -> list[Word]:
        return [w for w in self.iter_words() if w.end > start and w.start < end]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words_between(start, end)).strip()

    def word_count(self) -> int:
        return len(self.iter_words())


class Candidate(BaseModel):
    """A proposed clip. Deterministic, inspectable, and re-rankable by an agent."""

    model_config = ConfigDict(extra="ignore")

    id: str
    rank: int = 0
    start: float
    end: float
    duration: float
    text: str
    title_hint: str = ""
    score: float = 0.0
    scores: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FeatureSeries(BaseModel):
    """Compact per-hop audio features used for scoring and agent inspection."""

    model_config = ConfigDict(extra="ignore")

    hop: float = 0.25
    rms: list[float] = Field(default_factory=list)

    def at(self, seconds: float) -> float:
        if not self.rms:
            return 0.0
        idx = int(seconds / self.hop)
        if idx < 0:
            idx = 0
        if idx >= len(self.rms):
            idx = len(self.rms) - 1
        return self.rms[idx]

    def mean(self, start: float, end: float) -> float:
        if not self.rms:
            return 0.0
        lo = max(0, int(start / self.hop))
        hi = min(len(self.rms), max(lo + 1, int(end / self.hop)))
        chunk = self.rms[lo:hi]
        return sum(chunk) / len(chunk) if chunk else 0.0

    def variance(self, start: float, end: float) -> float:
        if not self.rms:
            return 0.0
        lo = max(0, int(start / self.hop))
        hi = min(len(self.rms), max(lo + 1, int(end / self.hop)))
        chunk = self.rms[lo:hi]
        if len(chunk) < 2:
            return 0.0
        mean = sum(chunk) / len(chunk)
        return sum((x - mean) ** 2 for x in chunk) / len(chunk)


class Features(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration: float = 0.0
    audio: FeatureSeries = Field(default_factory=FeatureSeries)
    speech_regions: list[list[float]] = Field(default_factory=list)
    scene_changes: list[float] = Field(default_factory=list)
    words_per_second: list[float] = Field(default_factory=list)
    wps_hop: float = 1.0
    mean_rms: float = 0.0
    peak_rms: float = 0.0
    silent_ratio: float = 0.0


class MediaInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool = False
    size_bytes: int = 0
    rotation: int = 0


class StageState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    params_hash: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)


class ProjectState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    id: str
    name: str
    root: str
    source: str
    created_at: str
    updated_at: str
    duration: float | None = None
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    language: str | None = None
    stages: dict[str, StageState] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    def stage(self, name: str) -> StageState:
        if name not in self.stages:
            self.stages[name] = StageState()
        return self.stages[name]


class ClipResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    candidate_id: str
    path: str
    ass_path: str | None = None
    duration: float
    width: int
    height: int
    size_bytes: int = 0
    created_at: str
    title: str | None = None
    score: float | None = None
    layout: str | None = None
    reframe_plan: str | None = None


class RenderOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layout: Literal["crop", "blur", "split", "auto"] = "crop"
    captions: bool = True
    normalize_audio: bool = True
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 20
    reframe: Literal["center", "face", "track"] = "center"
    reframe_sample_fps: float = 4.0
    min_split_duration: float = 2.0
