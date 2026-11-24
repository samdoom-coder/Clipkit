from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from clipkit.asr import get_backend
from clipkit.candidates import generate_candidates
from clipkit.captions import CaptionStyle, ensure_caption_font, write_ass
from clipkit.errors import ClipkitError
from clipkit.features import compute_features
from clipkit.media import (
    DEFAULT_SCENE_THRESHOLD,
    concat_clips,
    extract_audio,
    finalize_clip,
    probe_media,
    read_wav,
    render_clip,
)
from clipkit.models import (
    Candidate,
    ClipResult,
    Features,
    MediaInfo,
    RenderOptions,
    Transcript,
)
from clipkit.reframe import ReframePlan, plan_reframe
from clipkit.util import file_sha256, now_iso, read_json
from clipkit.workspace import Workspace


def _log(stage: str, fraction: float, message: str) -> None:
    print(f"[{stage}] {fraction * 100:5.1f}%  {message}", file=sys.stderr, flush=True)


def _source_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


# --------------------------------------------------------------------------- probe
def ensure_probe(ws: Workspace, *, force: bool = False) -> MediaInfo:
    params = _source_signature(ws.source_path)
    if not force and ws.stage_is_valid("probe", params):
        path = ws.root / "media.json"
        if path.exists():
            return MediaInfo.model_validate(read_json(path))
    ws.stage_begin("probe", params)
    try:
        info = probe_media(ws.source_path)
        ws.state.duration = info.duration
        ws.state.width = info.width
        ws.state.height = info.height
        ws.state.fps = info.fps
        ws.state.has_audio = info.has_audio
        ws.store("media.json", info.model_dump(mode="json"))
        ws.stage_done("probe", [ws.root / "media.json"])
        return info
    except Exception as exc:
        ws.stage_fail("probe", str(exc))
        raise


# --------------------------------------------------------------------------- audio
def ensure_audio(ws: Workspace, *, force: bool = False) -> Path:
    destination = ws.root / "audio.wav"
    params = _source_signature(ws.source_path)
    if not force and ws.stage_is_valid("audio", params) and destination.exists():
        return destination
    ws.stage_begin("audio", params)
    try:
        extract_audio(ws.source_path, destination)
        ws.stage_done("audio", [destination])
        return destination
    except Exception as exc:
        ws.stage_fail("audio", str(exc))
        raise


# --------------------------------------------------------------------------- transcript
def ensure_transcript(
    ws: Workspace,
    *,
    backend: str = "auto",
    language: str | None = None,
    transcript_path: Path | None = None,
    ctc_align: bool = False,
    force: bool = False,
) -> Transcript:
    params = {
        "backend": backend,
        "language": language,
        "ctc_align": ctc_align,
        "transcript": file_sha256(transcript_path) if transcript_path else None,
    }
    destination = ws.root / "transcript.json"
    if not force and ws.stage_is_valid("transcribe", params) and destination.exists():
        transcript = Transcript.model_validate(read_json(destination))
        ws.state.language = transcript.language
        return transcript

    # `auto` means "use whatever is already here"; only an explicit backend or
    # --force should replace a good transcript.
    if (
        not force
        and backend == "auto"
        and transcript_path is None
        and destination.exists()
        and ws.state.stage("transcribe").status == "done"
    ):
        transcript = Transcript.model_validate(read_json(destination))
        ws.state.language = transcript.language
        return transcript

    ensure_audio(ws, force=force)
    ws.stage_begin("transcribe", params)
    try:
        impl = get_backend(backend, transcript_path=transcript_path, ctc_align=ctc_align)
        transcript = impl.transcribe(
            ws.root / "audio.wav",
            language=language,
            duration=ws.state.duration,
            progress=_log,
        )
        if not transcript.segments:
            raise ClipkitError(
                "no speech detected — clipkit currently needs a transcript to find candidates"
            )
        ws.store("transcript.json", transcript.model_dump(mode="json"))
        ws.state.language = transcript.language
        ws.stage_done("transcribe", [destination])
        return transcript
    except Exception as exc:
        ws.stage_fail("transcribe", str(exc))
        raise


def load_transcript(ws: Workspace) -> Transcript:
    ws.require_stage("transcribe")
    return Transcript.model_validate(read_json(ws.root / "transcript.json"))


# --------------------------------------------------------------------------- features
def ensure_features(
    ws: Workspace,
    *,
    transcript: Transcript | None = None,
    scenes: bool = True,
    force: bool = False,
) -> Features:
    params = {
        "scenes": scenes,
        "hop": 0.5,
        "scene_threshold": DEFAULT_SCENE_THRESHOLD,
    }
    destination = ws.root / "features.json"
    if not force and ws.stage_is_valid("features", params) and destination.exists():
        return Features.model_validate(read_json(destination))

    transcript = transcript or load_transcript(ws)
    audio = ws.root / "audio.wav"
    if not audio.exists():
        raise ClipkitError("audio.wav is missing — run transcribe again")
    ws.stage_begin("features", params)
    try:
        media = ensure_probe(ws)
        features = compute_features(audio, transcript, media, scenes=scenes)
        ws.store("features.json", features.model_dump(mode="json"))
        ws.stage_done("features", [destination])
        return features
    except Exception as exc:
        ws.stage_fail("features", str(exc))
        raise


def load_features(ws: Workspace) -> Features:
    ws.require_stage("features")
    return Features.model_validate(read_json(ws.root / "features.json"))


# --------------------------------------------------------------------------- candidates
def ensure_candidates(
    ws: Workspace,
    *,
    min_seconds: float = 20.0,
    max_seconds: float = 60.0,
    top_n: int = 10,
    force: bool = False,
) -> list[Candidate]:
    params = {"min": min_seconds, "max": max_seconds, "top": top_n}
    destination = ws.root / "candidates.json"
    if not force and ws.stage_is_valid("candidates", params) and destination.exists():
        return [Candidate.model_validate(item) for item in read_json(destination)]

    transcript = load_transcript(ws)
    features = load_features(ws)
    ws.stage_begin("candidates", params)
    try:
        candidates = generate_candidates(
            transcript, features, min_seconds=min_seconds, max_seconds=max_seconds, top_n=top_n
        )
        ws.store("candidates.json", [candidate.model_dump(mode="json") for candidate in candidates])
        ws.stage_done("candidates", [destination])
        return candidates
    except Exception as exc:
        ws.stage_fail("candidates", str(exc))
        raise


def load_candidates(ws: Workspace) -> list[Candidate]:
    ws.require_stage("candidates")
    return [Candidate.model_validate(item) for item in read_json(ws.root / "candidates.json")]


# --------------------------------------------------------------------------- render
def _resolve_hook(candidate: Candidate, options: RenderOptions) -> str | None:
    """Per-candidate hook wins, then an explicit global hook, then the title hint."""
    hook = options.hooks.get(candidate.id)
    if hook:
        return hook
    if options.hook_text:
        return options.hook_text
    if options.hook_from_title:
        return candidate.title_hint or None
    return None


# Shots shorter than this are merged into a neighbour. Scene detectors fire on
# flashes and one-frame transitions; rendering those as separate segments adds
# overhead and gives the layout planner too little signal.
MIN_SHOT_SECONDS = 0.6


@dataclass
class ShotLayout:
    start: float
    end: float
    layout: str
    plan: ReframePlan
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def _shot_boundaries(
    cuts: list[float],
    duration: float,
    *,
    min_shot: float = MIN_SHOT_SECONDS,
) -> list[tuple[float, float]]:
    points = sorted(
        {0.0, max(0.0, duration)}
        | {max(0.0, min(duration, float(cut))) for cut in cuts}
    )
    merged: list[list[float]] = []
    for start, end in zip(points, points[1:]):
        if end - start <= 1e-3:
            continue
        if merged and (
            end - start < min_shot or end - merged[-1][0] < min_shot
        ):
            merged[-1][1] = end
        else:
            merged.append([start, end])
    if len(merged) > 1 and merged[0][1] - merged[0][0] < min_shot:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def _has_ambiguous_faces(plan: ReframePlan, frame_width: int) -> bool:
    """True when a shot has two separated persistent faces but no split decision.

    A single crop in that situation can follow the listener. Keeping the full
    frame (blur-padded) is the conservative fallback until real active-speaker
    selection exists. Tracks closer than the split separation threshold are
    usually duplicate fragments of one person, so they stay on the crop path.
    """
    strong = sorted(
        [
            track
            for track in plan.tracks
            if track.coverage >= 0.25 and track.duration >= 2.0
        ],
        key=lambda track: track.prominence,
        reverse=True,
    )
    if len(strong) < 2:
        return False
    separation = abs(strong[0].center_x - strong[1].center_x)
    return separation >= 0.18 * max(1, frame_width)


def plan_shot_layout(
    source: Path,
    candidate: Candidate,
    cuts: list[float],
    options: RenderOptions,
    media: MediaInfo,
) -> list[ShotLayout]:
    """Decide crop/split/blur independently for every shot in the clip."""
    decisions: list[ShotLayout] = []
    for start, end in _shot_boundaries(cuts, candidate.duration):
        plan = plan_reframe(
            source,
            start=candidate.start + start,
            duration=end - start,
            media=media,
            scene_changes=[],
            layout="auto",
            sample_fps=options.reframe_sample_fps,
            min_split_duration=options.min_split_duration,
        )
        ambiguous = _has_ambiguous_faces(plan, media.width)
        # On an ultra-wide source, blur-padding a 9:16 frame leaves a thin strip
        # of picture and huge blurred bands. Crop fills the frame and is the
        # better fallback; 16:9 sources still get the softer blur treatment.
        ultra_wide = media.height > 0 and media.width / media.height >= 2.0
        if plan.used_split:
            layout = "split"
            reason = "two persistent, separated faces"
        elif plan.layout == "single" and not ambiguous:
            layout = "crop"
            reason = "single persistent face track"
        elif ambiguous:
            layout = "crop" if ultra_wide else "blur"
            reason = (
                "multiple separated faces without a split decision; "
                + ("ultra-wide source uses a center crop" if ultra_wide else "blur keeps both visible")
            )
        else:
            layout = "crop" if ultra_wide else "blur"
            reason = (
                "no reliable face track; "
                + ("ultra-wide source uses a center crop" if ultra_wide else "blur keeps the source context")
            )
        decisions.append(
            ShotLayout(start=start, end=end, layout=layout, plan=plan, reason=reason)
        )
    return decisions


def _use_per_shot(decisions: list[ShotLayout]) -> bool:
    if len(decisions) < 2:
        return False
    # A uniform crop clip is cheaper and steadier as one merged path. Split
    # shots must be segmented even when every shot splits, because the global
    # coverage guard would reject the clip.
    return len({decision.layout for decision in decisions}) > 1 or any(
        decision.layout == "split" for decision in decisions
    )


def _store_shot_plan(ws: Workspace, clip_name: str, decisions: list[ShotLayout]) -> Path:
    return ws.store(
        f"clips/{clip_name}.reframe.json",
        {
            "layout": "per-shot",
            "notes": [
                f"{len(decisions)} shots",
                "layout decided independently per shot",
            ],
            "shots": [
                {
                    "start": decision.start,
                    "end": decision.end,
                    "duration": round(decision.duration, 3),
                    "layout": decision.layout,
                    "reason": decision.reason,
                    "plan": decision.plan.model_dump(mode="json"),
                }
                for decision in decisions
            ],
        },
    )


def _render_shot_layout(
    ws: Workspace,
    candidate: Candidate,
    clip_path: Path,
    ass_path: Path | None,
    decisions: list[ShotLayout],
    options: RenderOptions,
    media: MediaInfo,
) -> None:
    """Render each shot, concatenate, then mux audio and burn global captions."""
    shots_dir = ws.clips_dir / f".{clip_path.stem}.shots"
    shutil.rmtree(shots_dir, ignore_errors=True)
    shots_dir.mkdir(parents=True, exist_ok=True)
    try:
        segments: list[Path] = []
        for index, decision in enumerate(decisions):
            segment = shots_dir / f"shot_{index:03d}.mp4"
            plan = decision.plan
            crop_path = plan.crop_path if options.reframe == "track" else None
            center_x = options.crop_center_x
            if options.reframe in ("face", "track") and plan.static_center_x is not None:
                center_x = plan.static_center_x
            render_clip(
                ws.source_path,
                segment,
                start=candidate.start + decision.start,
                duration=decision.duration,
                ass_path=None,
                layout=decision.layout,
                width=options.width,
                height=options.height,
                fps=options.fps,
                crf=options.crf,
                normalize_audio=False,
                has_audio=False,
                source_width=media.width,
                source_height=media.height,
                crop_center_x=center_x,
                crop_path=crop_path,
                panels=plan.panel_boxes if decision.layout == "split" else None,
                panel_paths=plan.panel_paths if decision.layout == "split" else None,
            )
            segments.append(segment)
        combined = shots_dir / "combined.mp4"
        concat_clips(segments, combined, fps=options.fps, crf=options.crf)
        finalize_clip(
            combined,
            ws.source_path,
            clip_path,
            start=candidate.start,
            duration=candidate.duration,
            ass_path=ass_path,
            has_audio=media.has_audio,
            normalize_audio=options.normalize_audio,
            fps=options.fps,
            crf=options.crf,
        )
    finally:
        shutil.rmtree(shots_dir, ignore_errors=True)


def _render_single_clip(
    ws: Workspace,
    candidate: Candidate,
    clip_path: Path,
    ass_path: Path | None,
    options: RenderOptions,
    media: MediaInfo,
    *,
    layout: str,
    center_x: float | None,
    crop_path: list | None,
    panels: list | None,
    panel_paths: list | None,
) -> None:
    render_clip(
        ws.source_path,
        clip_path,
        start=candidate.start,
        duration=candidate.duration,
        ass_path=ass_path,
        layout=layout,
        width=options.width,
        height=options.height,
        fps=options.fps,
        crf=options.crf,
        normalize_audio=options.normalize_audio,
        has_audio=media.has_audio,
        source_width=media.width,
        source_height=media.height,
        crop_center_x=center_x,
        crop_path=crop_path,
        panels=panels,
        panel_paths=panel_paths,
    )


def render_candidates(
    ws: Workspace,
    candidates: list[Candidate],
    *,
    options: RenderOptions | None = None,
    style: CaptionStyle | None = None,
    force: bool = False,
) -> list[ClipResult]:
    options = options or RenderOptions()
    style = style or CaptionStyle()
    transcript = load_transcript(ws)
    media = ensure_probe(ws)
    needs_plan = options.layout in ("auto", "split") or options.reframe in ("face", "track")
    features = load_features(ws) if needs_plan else None
    ws.stage_begin(
        "render",
        {
            "ids": [candidate.id for candidate in candidates],
            "options": options.model_dump(mode="json"),
            "style": style.__dict__,
        },
    )
    results: list[ClipResult] = []
    try:
        for candidate in candidates:
            clip_name = f"clip_{candidate.rank:03d}"
            clip_path = ws.clips_dir / f"{clip_name}.mp4"
            meta_path = ws.clips_dir / f"{clip_name}.json"
            if clip_path.exists() and meta_path.exists() and not force:
                existing = ClipResult.model_validate(read_json(meta_path))
                existing.skipped = True
                results.append(existing)
                continue

            hook_text = None
            ass_path: Path | None = None
            if options.captions or options.hook_text or options.hook_from_title or options.hooks:
                hook_text = _resolve_hook(candidate, options)
                if options.captions or hook_text:
                    ensure_caption_font(ws.clips_dir)
                    ass_path = ws.clips_dir / f"{clip_name}.ass"
                    write_ass(
                        ass_path,
                        transcript.words_between(candidate.start, candidate.end),
                        clip_start=candidate.start,
                        clip_end=candidate.end,
                        style=style,
                        hook_text=hook_text,
                        captions=options.captions,
                    )

            center_x = options.crop_center_x
            panels = None
            panel_paths = None
            crop_path = None
            render_layout = options.layout
            plan_path: Path | None = None
            per_shot = False
            shot_decisions: list[ShotLayout] = []

            if needs_plan and options.layout != "blur":
                try:
                    cuts = [
                        round(at - candidate.start, 3)
                        for at in (features.scene_changes if features else [])
                        if candidate.start <= at <= candidate.end
                    ]
                    if options.layout == "auto" and options.per_shot_layout and cuts:
                        decisions = plan_shot_layout(
                            ws.source_path, candidate, cuts, options, media
                        )
                        if _use_per_shot(decisions):
                            per_shot = True
                            shot_decisions = decisions
                            plan_path = _store_shot_plan(ws, clip_name, decisions)
                            render_layout = "per-shot"
                    if not per_shot:
                        plan = plan_reframe(
                            ws.source_path,
                            start=candidate.start,
                            duration=candidate.duration,
                            media=media,
                            scene_changes=cuts,
                            layout=options.layout,
                            sample_fps=options.reframe_sample_fps,
                            min_split_duration=options.min_split_duration,
                        )
                        plan_path = ws.store(
                            f"clips/{clip_name}.reframe.json", plan.model_dump(mode="json")
                        )
                        if options.layout in ("auto", "split") and plan.used_split:
                            render_layout = "split"
                            panels = plan.panel_boxes
                            panel_paths = plan.panel_paths
                        else:
                            render_layout = "crop"
                            if options.layout == "split":
                                print(
                                    f"[render] {clip_name}: split requested but plan is "
                                    f"'{plan.layout}'; using single crop",
                                    file=sys.stderr,
                                )
                        if options.reframe in ("face", "track") and plan.static_center_x is not None:
                            center_x = plan.static_center_x
                        if options.reframe == "track" and plan.crop_path:
                            crop_path = plan.crop_path
                except Exception as exc:  # noqa: BLE001 - reframe is best-effort
                    print(f"[render] face tracking unavailable: {exc}", file=sys.stderr)
                    render_layout = "crop" if options.layout != "blur" else "blur"

            if per_shot:
                try:
                    _render_shot_layout(
                        ws, candidate, clip_path, ass_path, shot_decisions, options, media
                    )
                except Exception as exc:  # noqa: BLE001 - fall back to a safe crop
                    print(
                        f"[render] {clip_name}: per-shot layout failed ({exc}); "
                        "falling back to single crop",
                        file=sys.stderr,
                    )
                    render_layout = "crop"
                    per_shot = False
            if not per_shot:
                _render_single_clip(
                    ws,
                    candidate,
                    clip_path,
                    ass_path,
                    options,
                    media,
                    layout=render_layout,
                    center_x=center_x,
                    crop_path=crop_path,
                    panels=panels,
                    panel_paths=panel_paths,
                )
            info = probe_media(clip_path)
            result = ClipResult(
                id=clip_name,
                candidate_id=candidate.id,
                path=str(clip_path.relative_to(ws.root)),
                ass_path=str(ass_path.relative_to(ws.root)) if ass_path else None,
                duration=info.duration,
                width=info.width,
                height=info.height,
                size_bytes=info.size_bytes,
                created_at=now_iso(),
                title=candidate.title_hint,
                score=candidate.score,
                layout=render_layout,
                reframe_plan=str(plan_path.relative_to(ws.root)) if plan_path else None,
                hook_text=hook_text,
            )
            ws.store(f"clips/{clip_name}.json", result.model_dump(mode="json"))
            results.append(result)
            print(
                f"[render] {clip_name}: {candidate.duration:.1f}s, {candidate.score:.1f}/100 → {result.path}",
                file=sys.stderr,
            )

        ws.stage_done("render", [ws.root / result.path for result in results])
        return results
    except Exception as exc:
        ws.stage_fail("render", str(exc))
        raise


def list_clips(ws: Workspace) -> list[ClipResult]:
    results = []
    # Exclude sidecars such as clip_001.reframe.json.
    for path in sorted(ws.clips_dir.glob("clip_[0-9][0-9][0-9].json")):
        results.append(ClipResult.model_validate(read_json(path)))
    return results


def audio_stats(ws: Workspace) -> dict:
    audio = ws.root / "audio.wav"
    if not audio.exists():
        return {}
    samples, rate = read_wav(audio)
    return {
        "samples": int(samples.size),
        "sample_rate": rate,
        "seconds": round(samples.size / rate, 2) if rate else 0,
    }
