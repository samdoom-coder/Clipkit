from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from clipkit.errors import ClipkitError, DependencyError
from clipkit.models import MediaInfo
from clipkit.util import clamp


def require_ffmpeg() -> None:
    missing = [binary for binary in ("ffmpeg", "ffprobe") if shutil.which(binary) is None]
    if missing:
        raise DependencyError(
            f"missing {', '.join(missing)} on PATH — install FFmpeg (https://ffmpeg.org/download.html)"
        )


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise DependencyError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipkitError(f"command timed out after {timeout}s: {' '.join(cmd[:4])} ...") from exc

    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        raise ClipkitError(
            f"command failed ({proc.returncode}): {' '.join(cmd[:6])}\n" + "\n".join(tail)
        )
    return proc


def ffprobe(path: Path) -> dict:
    require_ffmpeg()
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(proc.stdout or "{}")


def _fps(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_media(path: Path) -> MediaInfo:
    data = ffprobe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    duration = 0.0
    for candidate in (fmt.get("duration"), (video or {}).get("duration"), (audio or {}).get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    rotation = 0
    if video:
        try:
            rotation = int(video.get("tags", {}).get("rotate", 0))
        except (TypeError, ValueError):
            rotation = 0
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                try:
                    rotation = int(float(side["rotation"]))
                except (TypeError, ValueError):
                    pass

    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    if rotation in (90, -90, 270, -270):
        width, height = height, width

    return MediaInfo(
        path=str(path),
        duration=max(0.0, duration),
        width=width,
        height=height,
        fps=_fps((video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate")),
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        has_audio=audio is not None,
        size_bytes=int(fmt.get("size") or path.stat().st_size),
        rotation=rotation,
    )


def extract_audio(source: Path, destination: Path) -> Path:
    require_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )
    return destination


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        channels = fh.getnchannels()
        width = fh.getsampwidth()
        rate = fh.getframerate()
        frames = fh.readframes(fh.getnframes())
    if width != 2:
        raise ClipkitError(f"expected 16-bit PCM WAV, got {width * 8}-bit")
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16).tobytes()
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm)
    return path


def center_crop_box(
    width: int,
    height: int,
    out_width: int = 1080,
    out_height: int = 1920,
    center_x: float | None = None,
) -> tuple[int, int, int, int]:
    """Return an even-pixel crop box that fills the target aspect ratio."""
    target = out_width / out_height
    source = width / height if height else target
    if source > target:
        crop_h = height
        crop_w = int(round(height * target))
        crop_w -= crop_w % 2
        max_x = max(0, width - crop_w)
        if center_x is None:
            x = max_x // 2
        else:
            x = int(round(clamp(center_x - crop_w / 2, 0, max_x)))
    else:
        crop_w = width
        crop_h = int(round(width / target))
        crop_h -= crop_h % 2
        max_y = max(0, height - crop_h)
        x = 0
        y = max_y // 2
        return x, y, crop_w, crop_h

    return x, 0, crop_w, crop_h


def _blur_filter(out_width: int, out_height: int, fps: int) -> str:
    # A close-up of the source is used as the fill, so a very strong blur and a
    # darkening pass are deliberate: without them a 16:9 source in a 9:16 frame
    # leaves a recognisable half-face in the background.
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={out_width}:{out_height}:force_original_aspect_ratio=increase,"
        f"crop={out_width}:{out_height},boxblur=80:4,"
        f"eq=brightness=-0.35:saturation=0.65[bg2];"
        f"[fg]scale={out_width}:{out_height}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,fps={fps}[v]"
    )


def _split_filter(
    panels: list,
    out_width: int,
    out_height: int,
    fps: int,
    ass_name: str | None,
    command_names: list[str] | None = None,
) -> str:
    panel_height = out_height // 2
    parts = []
    for index, panel in enumerate(panels):
        x, y, w, h = (int(value) for value in panel)
        if command_names:
            source = f"[0:v]sendcmd=f={command_names[index]},crop@panel{index}="
        else:
            source = "[0:v]crop="
        # Keep the 9:16 panel crop's aspect: a centered foreground over a
        # blurred fill. This is the OpusClip-style split panel, and unlike a
        # 9:8 crop it leaves room for the tracker to actually pan.
        parts.append(f"{source}{w}:{h}:{x}:{y},split=2[fg{index}][bg{index}]")
        # Same reasoning as _blur_filter: the fill is a close-up, so blur it
        # hard and darken it instead of leaving a half-face beside the panel.
        parts.append(
            f"[bg{index}]scale={out_width}:{panel_height}:force_original_aspect_ratio=increase,"
            f"crop={out_width}:{panel_height},boxblur=80:4,"
            f"eq=brightness=-0.35:saturation=0.65,setsar=1[bgb{index}]"
        )
        parts.append(
            f"[fg{index}]scale=-2:{panel_height}:flags=lanczos,setsar=1[fgs{index}]"
        )
        parts.append(
            f"[bgb{index}][fgs{index}]overlay=(W-w)/2:(H-h)/2[p{index}]"
        )
    parts.append(f"[p0][p1]vstack=inputs=2,fps={fps}[v0]")
    if ass_name:
        parts.append(f"[v0]ass=filename={ass_name}:fontsdir=.[v]")
    else:
        parts.append("[v0]null[v]")
    return ";".join(parts)


def _write_crop_commands(
    path: Path,
    crop_path: list,
    *,
    duration: float,
    crop_width: int,
    source_width: int,
    command_fps: float = 30.0,
    target: str = "crop",
) -> None:
    """Interpolate the sampled crop path to per-frame sendcmd updates.

    The tracker samples at 4 fps; writing only those keyframes makes pans look
    steppy. Interpolating at 30 fps is cheap and much smoother.
    """
    points = sorted((max(0.0, float(at)), float(center)) for at, center in crop_path)
    if not points:
        return
    if points[0][0] > 0:
        points.insert(0, (0.0, points[0][1]))
    if points[-1][0] < duration:
        points.append((duration, points[-1][1]))
    if len(points) == 1:
        points.append((points[0][0] + 1.0, points[0][1]))

    step = 1.0 / max(1.0, command_fps)
    max_x = max(0, source_width - crop_width)
    lines: list[str] = []
    segment = 0
    at = 0.0
    previous_center = points[0][1]
    jump_threshold = 0.12 * max(1, source_width)
    while at <= duration + 1e-6:
        while segment + 1 < len(points) - 1 and at > points[segment + 1][0]:
            segment += 1
        left_t, left_c = points[segment]
        right_t, right_c = points[min(segment + 1, len(points) - 1)]
        span = max(1e-6, right_t - left_t)
        ratio = clamp((at - left_t) / span, 0.0, 1.0)
        center = left_c + (right_c - left_c) * ratio
        # A scene cut (or a very fast pan) should snap, not glide across the
        # frame. The tracker already resets at cuts; this keeps interpolation
        # from smearing the reset over a quarter second.
        if abs(center - previous_center) > jump_threshold:
            center = right_c
        previous_center = center
        x = clamp(center - crop_width / 2, 0, max_x)
        lines.append(f"{at:.3f} {target} x {x:.2f};")
        at += step
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_clip(
    source: Path,
    destination: Path,
    *,
    start: float,
    duration: float,
    ass_path: Path | None = None,
    layout: str = "crop",
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    crf: int = 20,
    normalize_audio: bool = True,
    has_audio: bool = True,
    source_width: int = 0,
    source_height: int = 0,
    crop_center_x: float | None = None,
    crop_path: list | None = None,
    panels: list | None = None,
    panel_paths: list | None = None,
) -> Path:
    require_ffmpeg()
    source = source.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    work_dir = ass_path.parent if ass_path is not None else destination.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-i",
        str(source),
        "-t",
        f"{max(0.1, duration):.3f}",
    ]

    if layout == "split" and panels and len(panels) == 2:
        command_names: list[str] = []
        if panel_paths and len(panel_paths) == 2:
            for index, path in enumerate(panel_paths):
                name = f"{destination.stem}.p{index}.cmd"
                _write_crop_commands(
                    work_dir / name,
                    path,
                    duration=duration,
                    crop_width=int(panels[index][2]),
                    source_width=source_width or width,
                    target=f"crop@panel{index}",
                )
                command_names.append(name)
        filter_complex = _split_filter(
            panels,
            width,
            height,
            fps,
            ass_path.name if ass_path is not None else None,
            command_names=command_names or None,
        )
        cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
    elif layout == "blur":
        filter_complex = _blur_filter(width, height, fps)
        if ass_path is not None:
            filter_complex = filter_complex.replace(
                "[v]", f",ass=filename={ass_path.name}:fontsdir=.[v]"
            )
        cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
    else:
        crop_x, crop_y, crop_w, crop_h = center_crop_box(
            source_width or width,
            source_height or height,
            width,
            height,
            center_x=crop_center_x,
        )
        if crop_path:
            command_name = f"{destination.stem}.crop.cmd"
            _write_crop_commands(
                work_dir / command_name,
                crop_path,
                duration=duration,
                crop_width=crop_w,
                source_width=source_width or width,
            )
            vf = (
                f"sendcmd=f={command_name},crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"scale={width}:{height}:flags=lanczos,fps={fps}"
            )
        else:
            vf = (
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"scale={width}:{height}:flags=lanczos,fps={fps}"
            )
        if ass_path is not None:
            vf += f",ass=filename={ass_path.name}:fontsdir=."
        cmd += ["-vf", vf, "-map", "0:v:0"]

    if has_audio:
        cmd += ["-map", "0:a:0?"]

    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
        if normalize_audio:
            cmd += [
                "-af",
                loudnorm_filter(
                    measure_loudness(source, start=start, duration=duration)
                ),
            ]
    cmd += ["-movflags", "+faststart", str(destination)]

    try:
        run(cmd, cwd=work_dir)
    except ClipkitError as exc:
        # sendcmd is widely available but not universal; fall back to a static
        # crop rather than failing the whole clip on an exotic FFmpeg build.
        if layout == "crop" and crop_path and "-vf" in cmd:
            index = cmd.index("-vf") + 1
            cmd[index] = cmd[index].replace(f"sendcmd=f={destination.stem}.crop.cmd,", "")
            run(cmd, cwd=work_dir)
        elif layout == "split" and panel_paths and "-filter_complex" in cmd and panels:
            index = cmd.index("-filter_complex") + 1
            cmd[index] = _split_filter(
                panels,
                width,
                height,
                fps,
                ass_path.name if ass_path is not None else None,
            )
            run(cmd, cwd=work_dir)
        else:
            raise ClipkitError(f"render failed for {destination.name}: {exc}") from exc
    return destination


def measure_loudness(
    source: Path,
    *,
    start: float,
    duration: float,
    target: float = -14.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
) -> dict | None:
    """Run the loudnorm analysis pass and return its JSON measurements.

    Single-pass loudnorm routinely misses the target by 1–4 LU on clips with
    dynamic source audio. The measured values feed a second pass, which lands
    within a few tenths of a LU. Returns None for silence or failed analysis so
    callers can fall back to single-pass.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{max(0.1, duration):.3f}",
        "-i",
        str(source),
        "-af",
        f"loudnorm=I={target}:TP={true_peak}:LRA={lra}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = run(cmd, check=False)
    except ClipkitError:
        return None
    if proc.returncode != 0:
        return None
    for raw in reversed(re.findall(r"\{[^{}]*\}", proc.stderr or "")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "input_i" not in data:
            continue
        try:
            if float(data["input_i"]) <= -70.0:
                return None
        except (TypeError, ValueError):
            return None
        return data
    return None


def loudnorm_filter(
    measurement: dict | None,
    *,
    target: float = -14.0,
    true_peak: float = -1.5,
    lra: float = 11.0,
) -> str:
    base = f"loudnorm=I={target}:TP={true_peak}:LRA={lra}"
    if not measurement:
        return base
    try:
        measured = {
            "measured_I": measurement["input_i"],
            "measured_LRA": measurement["input_lra"],
            "measured_TP": measurement["input_tp"],
            "measured_thresh": measurement["input_thresh"],
            "offset": measurement["target_offset"],
        }
    except KeyError:
        return base
    extra = ":".join(f"{key}={value}" for key, value in measured.items())
    return f"{base}:{extra}:linear=true:print_format=summary"


def concat_clips(
    segments: list[Path],
    destination: Path,
    *,
    fps: int = 30,
    crf: int = 20,
) -> Path:
    """Concatenate same-codec video-only segments, re-encoding only if needed."""
    if not segments:
        raise ClipkitError("cannot concatenate zero segments")
    destination.parent.mkdir(parents=True, exist_ok=True)
    list_path = destination.with_suffix(".concat.txt")
    list_path.write_text(
        "\n".join(f"file '{segment.resolve()}'" for segment in segments) + "\n",
        encoding="utf-8",
    )
    base = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-an",
        "-pix_fmt",
        "yuv420p",
    ]
    try:
        run(base + ["-c:v", "copy", "-movflags", "+faststart", str(destination)])
    except ClipkitError:
        run(
            base
            + [
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(crf),
                "-profile:v",
                "high",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
    return destination


def finalize_clip(
    video: Path,
    source: Path,
    destination: Path,
    *,
    start: float,
    duration: float,
    ass_path: Path | None = None,
    has_audio: bool = True,
    normalize_audio: bool = True,
    fps: int = 30,
    crf: int = 20,
) -> Path:
    """Mux source audio and burn captions onto a pre-rendered video timeline."""
    require_ffmpeg()
    video = video.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    work_dir = ass_path.parent if ass_path is not None else destination.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video),
    ]
    if has_audio:
        cmd += [
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.1, duration):.3f}",
            "-i",
            str(source),
        ]
    cmd += ["-map", "0:v:0"]
    if ass_path is not None:
        cmd += [
            "-vf",
            f"ass=filename={ass_path.name}:fontsdir=.",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        cmd += ["-c:v", "copy"]
    if has_audio:
        cmd += ["-map", "1:a:0?", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
        if normalize_audio:
            cmd += [
                "-af",
                loudnorm_filter(
                    measure_loudness(source, start=start, duration=duration)
                ),
            ]
    cmd += [
        "-t",
        f"{max(0.1, duration):.3f}",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    run(cmd, cwd=work_dir)
    return destination


DEFAULT_SCENE_THRESHOLD = 0.25


def detect_scene_changes(
    source: Path, threshold: float = DEFAULT_SCENE_THRESHOLD, timeout: int = 180
) -> list[float]:
    """Best-effort hard-cut detection using FFmpeg's scene score.

    Returns an empty list instead of failing the pipeline: scene cuts improve
    reframing, but they are not required for a valid clip.
    """
    require_ffmpeg()
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(source),
        "-vf",
        f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = run(cmd, timeout=timeout)
    except ClipkitError:
        return []
    times: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", proc.stderr or ""):
        times.append(float(match.group(1)))
    return sorted(set(times))


def contact_sheet(clip: Path, destination: Path, *, count: int = 9, columns: int = 3) -> Path:
    require_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = probe_media(clip)
    interval = max(0.5, info.duration / max(1, count))
    rows = max(1, (count + columns - 1) // columns)
    vf = (
        f"fps=1/{interval:.3f},scale=360:-2:flags=lanczos,"
        f"tile={columns}x{rows}:padding=6:margin=6"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(clip),
            "-vf",
            vf,
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(destination),
        ]
    )
    return destination


def extract_frame(clip: Path, at: float, destination: Path) -> Path:
    require_ffmpeg()
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, at):.3f}",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(destination),
        ]
    )
    return destination
