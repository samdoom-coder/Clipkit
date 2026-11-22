from __future__ import annotations

import sys
from pathlib import Path

from clipkit.asr import get_backend
from clipkit.candidates import generate_candidates
from clipkit.captions import CaptionStyle, ensure_caption_font, write_ass
from clipkit.errors import ClipkitError
from clipkit.features import compute_features
from clipkit.media import extract_audio, probe_media, read_wav, render_clip
from clipkit.models import (
    Candidate,
    ClipResult,
    Features,
    MediaInfo,
    RenderOptions,
    Transcript,
)
from clipkit.reframe import plan_reframe
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
    scenes: bool = False,
    force: bool = False,
) -> Features:
    params = {"scenes": scenes, "hop": 0.5}
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
                results.append(ClipResult.model_validate(read_json(meta_path)))
                continue

            ass_path: Path | None = None
            if options.captions:
                ensure_caption_font(ws.clips_dir)
                ass_path = ws.clips_dir / f"{clip_name}.ass"
                write_ass(
                    ass_path,
                    transcript.words_between(candidate.start, candidate.end),
                    clip_start=candidate.start,
                    clip_end=candidate.end,
                    style=style,
                )

            center_x = options.crop_center_x
            panels = None
            panel_paths = None
            crop_path = None
            render_layout = options.layout
            plan_path: Path | None = None

            if needs_plan and options.layout != "blur":
                try:
                    cuts = [
                        round(at - candidate.start, 3)
                        for at in (features.scene_changes if features else [])
                        if candidate.start <= at <= candidate.end
                    ]
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

            render_clip(
                ws.source_path,
                clip_path,
                start=candidate.start,
                duration=candidate.duration,
                ass_path=ass_path,
                layout=render_layout,
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
