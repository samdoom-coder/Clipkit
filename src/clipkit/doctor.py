from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys


def _version_of(command: str) -> str | None:
    if shutil.which(command) is None:
        return None
    try:
        proc = subprocess.run([command, "-version"], capture_output=True, text=True, timeout=10)
        return (proc.stdout or proc.stderr).splitlines()[0]
    except Exception:  # noqa: BLE001
        return "installed"


def doctor() -> dict:
    report: dict = {
        "python": sys.version.split()[0],
        "ffmpeg": _version_of("ffmpeg"),
        "ffprobe": _version_of("ffprobe"),
        "yt_dlp": _version_of("yt-dlp"),
        "packages": {},
        "gpu": None,
        "rumble_cached": False,
        "captions_font": None,
        "face_detector": None,
    }

    for module in ("numpy", "pydantic", "torch", "transformers", "faster_whisper", "cv2", "mcp"):
        spec = importlib.util.find_spec(module)
        if not spec:
            report["packages"][module] = None
            continue
        try:
            imported = __import__(module)
            report["packages"][module] = getattr(imported, "__version__", "installed")
        except Exception:  # noqa: BLE001
            report["packages"][module] = "installed (import failed)"

    try:
        import torch

        if torch.cuda.is_available():
            report["gpu"] = {
                "name": torch.cuda.get_device_name(0),
                "bf16": bool(torch.cuda.is_bf16_supported()),
                "vram_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1),
            }
    except Exception:  # noqa: BLE001
        pass

    try:
        from huggingface_hub import try_to_load_from_cache

        cached = try_to_load_from_cache("Reson8/Rumble-2-Flash", "config.json")
        report["rumble_cached"] = isinstance(cached, str)
    except Exception:  # noqa: BLE001
        pass

    try:
        from clipkit.captions import bundled_font_dir

        directory = bundled_font_dir()
        report["captions_font"] = bool(directory and (directory / "Montserrat-ExtraBold.ttf").exists())
    except Exception:  # noqa: BLE001
        pass

    try:
        from clipkit.reframe import detector_status

        report["face_detector"] = detector_status()
    except Exception:  # noqa: BLE001
        pass

    if not report["captions_font"] and shutil.which("fc-list"):
        try:
            proc = subprocess.run(["fc-list"], capture_output=True, text=True, timeout=10)
            names = {line.split(":", 2)[-1].strip().split(",")[0] for line in proc.stdout.splitlines() if ":" in line}
            report["captions_font"] = any("Montserrat" in name for name in names)
        except Exception:  # noqa: BLE001
            pass

    return report
