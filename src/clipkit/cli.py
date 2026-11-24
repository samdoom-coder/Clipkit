from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from clipkit import __version__
from clipkit.captions import CaptionStyle
from clipkit.doctor import doctor
from clipkit.errors import ClipkitError
from clipkit.evaluation import evaluate, format_report, load_labels
from clipkit.ingest import download, is_url
from clipkit.media import contact_sheet, probe_media
from clipkit.models import Candidate, RenderOptions
from clipkit.pipeline import (
    ensure_candidates,
    ensure_features,
    ensure_probe,
    ensure_transcript,
    list_clips,
    load_candidates,
    render_candidates,
)
from clipkit.skill import bundled_skill, install_skill
from clipkit.util import read_json
from clipkit.workspace import PROJECT_FILE, Workspace


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _workspace(args) -> Workspace:
    return Workspace.load(getattr(args, "workspace", ".clipkit"))


def _load_hooks(path: str | None) -> dict[str, str]:
    """Load a candidate-id -> hook-text JSON object."""
    if not path:
        return {}
    data = read_json(Path(path).expanduser())
    if not isinstance(data, dict):
        raise ClipkitError("--hooks must be a JSON object mapping candidate ids to hook text")
    hooks: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ClipkitError("--hooks keys and values must be strings")
        if value.strip():
            hooks[key] = value.strip()
    return hooks


def _warn_unknown_hooks(hooks: dict[str, str], candidates) -> None:
    known = {candidate.id for candidate in candidates}
    unknown = sorted(set(hooks) - known)
    if unknown:
        print(
            f"warning: hooks for unknown candidate id(s): {', '.join(unknown)}",
            file=sys.stderr,
        )


def _reset_workspace(root: Path) -> None:
    for name in ("project.json", "media.json", "transcript.json", "features.json", "candidates.json", "audio.wav"):
        path = root / name
        if path.exists():
            path.unlink()
    for directory in ("clips", "inspect", "cache"):
        path = root / directory
        if path.exists():
            shutil.rmtree(path)


# --------------------------------------------------------------------------- init
def cmd_init(args) -> int:
    root = Path(args.workspace).expanduser().resolve()
    if (root / PROJECT_FILE).exists():
        if not args.force:
            raise ClipkitError(f"workspace already exists at {root} (use --force to reset it)")
        _reset_workspace(root)

    if is_url(args.source):
        root.mkdir(parents=True, exist_ok=True)
        print(f"[init] downloading {args.source}", file=sys.stderr)
        source = download(
            args.source,
            root,
            name=args.name,
            cookies=args.cookies,
            proxy=args.proxy,
            section=args.section,
        )
    else:
        source = Path(args.source).expanduser()

    ws = Workspace.create(root, source, name=args.name, copy_source=args.copy)
    info = ensure_probe(ws)
    print(f"initialized {ws.root}")
    print(f"  name:     {ws.state.name}")
    print(f"  source:   {ws.source_path}")
    print(f"  duration: {_clock(info.duration)}")
    print(f"  video:    {info.width}x{info.height} @ {info.fps:.2f} fps")
    print(f"  audio:    {'yes' if info.has_audio else 'no'}")
    return 0


# --------------------------------------------------------------------------- probe
def cmd_probe(args) -> int:
    ws = _workspace(args)
    info = ensure_probe(ws)
    if args.json:
        _print_json(info.model_dump(mode="json"))
    else:
        print(f"{ws.state.name}")
        print(f"  source:   {ws.source_path}")
        print(f"  duration: {_clock(info.duration)} ({info.duration:.2f}s)")
        print(f"  size:     {info.width}x{info.height} @ {info.fps:.2f} fps")
        print(f"  codecs:   {info.video_codec} / {info.audio_codec or 'none'}")
        print(f"  bytes:    {info.size_bytes:,}")
    return 0


# --------------------------------------------------------------------------- transcribe
def cmd_transcribe(args) -> int:
    ws = _workspace(args)
    transcript = ensure_transcript(
        ws,
        backend=args.backend,
        language=args.language,
        transcript_path=Path(args.transcript).expanduser() if args.transcript else None,
        ctc_align=args.ctc_align,
        force=args.force,
    )
    if args.json:
        _print_json(transcript.model_dump(mode="json"))
    else:
        print(f"backend:   {transcript.backend}")
        print(f"language:  {transcript.language or 'auto'}")
        print(f"alignment: {transcript.alignment}")
        print(f"segments:  {len(transcript.segments)}")
        print(f"words:     {transcript.word_count()}")
        print(f"duration:  {_clock(transcript.duration)}")
    return 0


# --------------------------------------------------------------------------- analyze
def cmd_analyze(args) -> int:
    ws = _workspace(args)
    transcript = ensure_transcript(
        ws,
        backend=args.backend,
        language=args.language,
        transcript_path=Path(args.transcript).expanduser() if args.transcript else None,
        ctc_align=args.ctc_align,
        force=False,
    )
    features = ensure_features(ws, transcript=transcript, scenes=args.scenes, force=args.force)
    candidates = ensure_candidates(
        ws,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        top_n=args.top,
        force=args.force,
    )
    if args.json:
        _print_json([candidate.model_dump(mode="json") for candidate in candidates])
    else:
        print(f"features:   {len(features.audio.rms)} audio frames, {len(features.speech_regions)} speech regions")
        _print_candidates(candidates, limit=args.top)
    return 0


# --------------------------------------------------------------------------- candidates
def cmd_candidates(args) -> int:
    ws = _workspace(args)
    candidates = load_candidates(ws)
    selected = candidates[: args.top]
    if args.json:
        _print_json([candidate.model_dump(mode="json") for candidate in selected])
    else:
        _print_candidates(selected, limit=args.top)
    return 0


def _print_candidates(candidates: list[Candidate], *, limit: int = 10) -> None:
    if not candidates:
        print("no candidates — run `clipkit analyze` first")
        return
    print(f"{'#':<3} {'id':<22} {'time':<17} {'score':>5}  title")
    for candidate in candidates[:limit]:
        window = f"{_clock(candidate.start)}–{_clock(candidate.end)}"
        print(f"{candidate.rank:<3} {candidate.id:<22} {window:<17} {candidate.score:>5.1f}  {candidate.title_hint}")
        if candidate.warnings:
            print(f"    ! {'; '.join(candidate.warnings)}")
    print("\nUse `clipkit candidates --json` for the full transcript excerpt and score breakdown.")


# --------------------------------------------------------------------------- render
def cmd_render(args) -> int:
    ws = _workspace(args)
    candidates = load_candidates(ws)
    if not candidates:
        raise ClipkitError("no candidates — run `clipkit analyze` first")
    hooks = _load_hooks(args.hooks)
    _warn_unknown_hooks(hooks, candidates)

    selected: list[Candidate]
    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        selected = [c for c in candidates if c.id in wanted or str(c.rank) in wanted]
        if not selected:
            raise ClipkitError(f"no candidates matched: {args.ids}")
    else:
        selected = candidates[: args.top]

    style = CaptionStyle()
    if args.style:
        style = CaptionStyle.from_dict(read_json(Path(args.style)))
    options = RenderOptions(
        layout=args.layout,
        captions=not args.no_captions,
        normalize_audio=not args.no_normalize,
        reframe=args.reframe,
        reframe_sample_fps=args.reframe_fps,
        min_split_duration=args.min_split,
        crop_center_x=args.crop_center,
        width=args.width,
        height=args.height,
        fps=args.fps,
        crf=args.crf,
        hook_text=args.hook_text,
        hook_from_title=args.hook_from_title,
        hooks=hooks,
    )
    results = render_candidates(ws, selected, options=options, style=style, force=args.force)
    if args.json:
        _print_json([result.model_dump(mode="json") for result in results])
    else:
        rendered = [result for result in results if not result.skipped]
        skipped = [result for result in results if result.skipped]
        if rendered:
            print(f"rendered {len(rendered)} clip(s) in {ws.clips_dir}")
        if skipped:
            print(f"skipped {len(skipped)} existing clip(s) — use --force to re-render")
        for result in results:
            status = "skipped" if result.skipped else f"{result.duration:.1f}s"
            hook = f"  hook={result.hook_text!r}" if result.hook_text else ""
            print(f"  {result.id}  {status}  {result.path}{hook}")
    return 0


# --------------------------------------------------------------------------- auto
def cmd_auto(args) -> int:
    root = Path(args.workspace).expanduser().resolve()
    if args.source:
        if (root / PROJECT_FILE).exists():
            if args.force:
                _reset_workspace(root)
            else:
                print(f"[auto] using existing workspace {root}", file=sys.stderr)
        if not (root / PROJECT_FILE).exists():
            if is_url(args.source):
                root.mkdir(parents=True, exist_ok=True)
                source = download(
                    args.source,
                    root,
                    name=args.name,
                    cookies=args.cookies,
                    proxy=args.proxy,
                    section=args.section,
                )
            else:
                source = Path(args.source).expanduser()
            Workspace.create(root, source, name=args.name, copy_source=args.copy)
    ws = _workspace(args)

    ensure_probe(ws)
    transcript = ensure_transcript(
        ws,
        backend=args.backend,
        language=args.language,
        transcript_path=Path(args.transcript).expanduser() if args.transcript else None,
        ctc_align=args.ctc_align,
    )
    ensure_features(ws, transcript=transcript, scenes=args.scenes)
    candidates = ensure_candidates(
        ws,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        top_n=args.top,
    )
    style = CaptionStyle()
    if args.style:
        style = CaptionStyle.from_dict(read_json(Path(args.style)))
    hooks = _load_hooks(args.hooks)
    _warn_unknown_hooks(hooks, candidates)
    options = RenderOptions(
        layout=args.layout,
        captions=not args.no_captions,
        normalize_audio=not args.no_normalize,
        reframe=args.reframe,
        reframe_sample_fps=args.reframe_fps,
        min_split_duration=args.min_split,
        crop_center_x=args.crop_center,
        width=args.width,
        height=args.height,
        fps=args.fps,
        crf=args.crf,
        hook_text=args.hook_text,
        hook_from_title=args.hook_from_title,
        hooks=hooks,
    )
    results = render_candidates(ws, candidates[: args.top], options=options, style=style)
    rendered = [result for result in results if not result.skipped]
    skipped = [result for result in results if result.skipped]
    print(f"\ndone — {len(rendered)} rendered, {len(skipped)} skipped in {ws.clips_dir}")
    for result in results:
        status = "skipped" if result.skipped else f"score={result.score:.1f}"
        print(f"  {result.id}  {result.duration:.1f}s  {status}  {result.path}")
    return 0


# --------------------------------------------------------------------------- status
def cmd_status(args) -> int:
    ws = _workspace(args)
    clips = list_clips(ws)
    if args.json:
        data = ws.summary()
        data["clips"] = [clip.model_dump(mode="json") for clip in clips]
        _print_json(data)
        return 0
    summary = ws.summary()
    print(f"{summary['name']}  ({summary['id']})")
    print(f"  workspace: {summary['workspace']}")
    print(f"  source:    {summary['source']}")
    if summary["duration"]:
        print(f"  duration:  {_clock(summary['duration'])}")
    for name, stage in summary["stages"].items():
        detail = ""
        if stage["error"]:
            detail = f"  — {stage['error'].splitlines()[0][:100]}"
        elif stage["artifacts"]:
            detail = f"  ({len(stage['artifacts'])} artifact(s))"
        print(f"  {name:<12} {stage['status']}{detail}")
    if clips:
        print(f"  clips:      {len(clips)}")
        for clip in clips:
            print(f"    {clip.id}  {clip.duration:.1f}s  {clip.path}")
    return 0


# --------------------------------------------------------------------------- eval
def cmd_eval(args) -> int:
    ws = _workspace(args)
    candidates = sorted(load_candidates(ws), key=lambda candidate: candidate.rank)
    report = evaluate(candidates, load_labels(args.labels))
    if args.json:
        _print_json(report)
    else:
        print(format_report(report))
    return 0


# --------------------------------------------------------------------------- inspect
def _resolve_clip_arg(ws, value: str) -> Path:
    """Accept a clip id (`clip_002`), a workspace-relative path, or an absolute path."""
    value = value.strip()
    if re.fullmatch(r"clip_\d{3,}", value):
        return ws.clips_dir / f"{value}.mp4"
    clip = Path(value).expanduser()
    if not clip.is_absolute():
        candidate = ws.root / clip
        clip = candidate if candidate.exists() else Path.cwd() / clip
    return clip


def cmd_inspect(args) -> int:
    ws = _workspace(args)
    if args.clip:
        clip = _resolve_clip_arg(ws, args.clip)
    else:
        clips = list_clips(ws)
        if not clips:
            raise ClipkitError("no rendered clips — run `clipkit render` first")
        clips.sort(key=lambda item: item.id)
        clip = ws.root / clips[max(0, args.top - 1)].path

    if not clip.exists():
        raise ClipkitError(f"clip not found: {clip}")
    destination = ws.root / "inspect" / f"{clip.stem}.jpg"
    contact_sheet(clip, destination, count=args.count)
    info = probe_media(clip)
    print(f"{clip}")
    print(f"  duration: {info.duration:.2f}s")
    print(f"  size:     {info.width}x{info.height} @ {info.fps:.2f} fps")
    print(f"  sheet:    {destination}")
    return 0


# --------------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    report = doctor()
    if args.json:
        _print_json(report)
        return 0
    print(f"python      {report['python']}")
    print(f"ffmpeg      {report['ffmpeg'] or 'MISSING'}")
    print(f"ffprobe     {report['ffprobe'] or 'MISSING'}")
    print(f"yt-dlp      {report['yt_dlp'] or 'not installed (URL ingest disabled)'}")
    print("packages:")
    for name, version in report["packages"].items():
        print(f"  {name:<14} {version or 'not installed'}")
    if report["gpu"]:
        gpu = report["gpu"]
        print(f"gpu         {gpu['name']} ({gpu['vram_gb']} GB, bf16={gpu['bf16']})")
    else:
        print("gpu         none")
    print(f"rumble      {'cached' if report['rumble_cached'] else 'not cached (first run downloads ~1 GB)'}")
    print(f"montserrat  {'found' if report['captions_font'] else 'not found — libass will substitute a font'}")
    detector = report.get("face_detector") or {}
    if detector:
        print(
            f"face det.   {detector.get('backend')} "
            f"(requested {detector.get('requested')}; model {detector.get('yunet_model') or 'none'})"
        )
    return 0


# --------------------------------------------------------------------------- skill
def cmd_skill(args) -> int:
    if args.install:
        target = install_skill(args.install)
        print(f"installed skill: {target}")
        return 0
    source = bundled_skill()
    if source is None:
        raise ClipkitError("SKILL.md is not bundled with this installation")
    if args.print:
        print(source.read_text(encoding="utf-8"))
    else:
        print(source)
        print("\nInstall it for your agent, for example:")
        print("  clipkit skill --install ~/.claude/skills/clipkit")
    return 0


# --------------------------------------------------------------------------- mcp
def cmd_mcp(args) -> int:
    try:
        from clipkit.mcp_server import main as mcp_main
    except ImportError as exc:
        raise ClipkitError("MCP support needs the mcp package — run `uv pip install 'clipkit[mcp]'`") from exc
    mcp_main()
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-w", "--workspace", default=argparse.SUPPRESS, help="workspace directory (default: .clipkit)")

    parser = argparse.ArgumentParser(
        prog="clipkit",
        description="Agent-native clip studio: long video in, captioned vertical shorts out.",
    )
    parser.add_argument("--version", action="version", version=f"clipkit {__version__}")
    parser.add_argument("-w", "--workspace", default=".clipkit", help="workspace directory (default: .clipkit)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], help="create a workspace and probe a video")
    p.add_argument("source", help="local video path or http(s) URL")
    p.add_argument("--name", help="project name")
    p.add_argument("--copy", action="store_true", help="copy/hardlink the source into the workspace")
    p.add_argument("--cookies", help="Netscape cookies.txt for authenticated yt-dlp downloads")
    p.add_argument("--proxy", help="proxy URL passed to yt-dlp")
    p.add_argument("--section", help="yt-dlp section, e.g. '*0-1200' for the first 20 minutes")
    p.add_argument("--force", action="store_true", help="reset an existing workspace")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("probe", parents=[common], help="inspect the source video")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("transcribe", parents=[common], help="run speech recognition")
    p.add_argument("--backend", default="auto", choices=["auto", "rumble", "whisper", "mock"])
    p.add_argument("--language", help="force a language (e.g. English, Spanish)")
    p.add_argument("--transcript", help="use an existing transcript JSON instead of ASR")
    p.add_argument("--ctc-align", action="store_true", help="force-align Rumble text with its CTC head")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("analyze", parents=[common], help="compute features and candidate clips")
    p.add_argument("--backend", default="auto", choices=["auto", "rumble", "whisper", "mock"])
    p.add_argument("--language")
    p.add_argument("--transcript")
    p.add_argument("--ctc-align", action="store_true")
    p.add_argument("--min-seconds", type=float, default=20.0)
    p.add_argument("--max-seconds", type=float, default=60.0)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--scenes", action="store_true", help="detect hard cuts (slower)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("candidates", parents=[common], help="list candidate clips")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_candidates)

    p = sub.add_parser("render", parents=[common], help="render captioned vertical clips")
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--ids", help="comma-separated candidate ids or ranks")
    p.add_argument("--layout", default="crop", choices=["crop", "blur", "split", "auto"])
    p.add_argument("--reframe", default="center", choices=["center", "face", "track"])
    p.add_argument("--reframe-fps", type=float, default=4.0, help="face sampling rate for tracking")
    p.add_argument("--min-split", type=float, default=2.0, help="seconds two faces must persist for split-screen")
    p.add_argument(
        "--crop-center",
        type=float,
        help="source x-coordinate for a static crop center (pixels; default frame center)",
    )
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--no-normalize", action="store_true")
    p.add_argument("--hook-text", help="hook banner text shown at the top of the clip")
    p.add_argument(
        "--hook-from-title",
        action="store_true",
        help="use each candidate's title as its hook banner",
    )
    p.add_argument(
        "--hooks",
        help="JSON object mapping candidate ids to per-clip hook text",
    )
    p.add_argument("--style", help="caption style JSON")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("auto", parents=[common], help="init + transcribe + analyze + render in one go")
    p.add_argument("source", nargs="?", help="video path or URL (optional if workspace exists)")
    p.add_argument("--name")
    p.add_argument("--copy", action="store_true")
    p.add_argument("--cookies", help="Netscape cookies.txt for authenticated yt-dlp downloads")
    p.add_argument("--proxy", help="proxy URL passed to yt-dlp")
    p.add_argument("--section", help="yt-dlp section, e.g. '*0-1200' for the first 20 minutes")
    p.add_argument("--backend", default="auto", choices=["auto", "rumble", "whisper", "mock"])
    p.add_argument("--language")
    p.add_argument("--transcript")
    p.add_argument("--ctc-align", action="store_true")
    p.add_argument("--min-seconds", type=float, default=20.0)
    p.add_argument("--max-seconds", type=float, default=60.0)
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--scenes", action="store_true")
    p.add_argument("--layout", default="crop", choices=["crop", "blur", "split", "auto"])
    p.add_argument("--reframe", default="center", choices=["center", "face", "track"])
    p.add_argument("--reframe-fps", type=float, default=4.0, help="face sampling rate for tracking")
    p.add_argument("--min-split", type=float, default=2.0, help="seconds two faces must persist for split-screen")
    p.add_argument(
        "--crop-center",
        type=float,
        help="source x-coordinate for a static crop center (pixels; default frame center)",
    )
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--no-normalize", action="store_true")
    p.add_argument("--hook-text", help="hook banner text shown at the top of the clip")
    p.add_argument(
        "--hook-from-title",
        action="store_true",
        help="use each candidate's title as its hook banner",
    )
    p.add_argument(
        "--hooks",
        help="JSON object mapping candidate ids to per-clip hook text",
    )
    p.add_argument("--style")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("status", parents=[common], help="show workspace state")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("eval", parents=[common], help="evaluate candidates against human labels")
    p.add_argument("--labels", required=True, help="labels JSON with human-picked moments")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("inspect", parents=[common], help="create a contact sheet for a clip")
    p.add_argument("--clip", help="clip id (clip_002) or path")
    p.add_argument(
        "--top",
        type=int,
        default=1,
        help="Nth rendered clip by id when --clip is omitted (default: 1)",
    )
    p.add_argument("--count", type=int, default=9)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("doctor", parents=[common], help="check the local toolchain")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("skill", parents=[common], help="locate or install the agent skill")
    p.add_argument("--install", help="directory to copy SKILL.md into")
    p.add_argument("--print", action="store_true", help="print the skill contents")
    p.set_defaults(func=cmd_skill)

    p = sub.add_parser("mcp", parents=[common], help="run the MCP stdio server")
    p.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ClipkitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
