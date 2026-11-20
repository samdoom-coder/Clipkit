from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from clipkit.errors import ClipkitError, DependencyError
from clipkit.util import slugify


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def assert_public_url(url: str) -> None:
    """Block obvious SSRF targets before handing a URL to yt-dlp.

    This is a guardrail, not a sandbox: a determined attacker can still use
    DNS rebinding. Run downloads in a network-restricted worker if you expose
    this to untrusted users.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ClipkitError("only http(s) URLs are supported")
    if not parsed.hostname:
        raise ClipkitError("URL has no hostname")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ClipkitError(f"could not resolve {parsed.hostname}: {exc}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ClipkitError(f"refusing to download from a private address: {address}")


def download(url: str, directory: Path, *, name: str | None = None) -> Path:
    assert_public_url(url)
    if shutil.which("yt-dlp") is None:
        raise DependencyError(
            "downloading URLs needs yt-dlp — install it with `uv pip install yt-dlp` "
            "or download the file yourself and pass a local path"
        )
    directory.mkdir(parents=True, exist_ok=True)
    stem = slugify(name or "source", max_len=40)
    template = str(directory / f"{stem}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--max-filesize",
        "8G",
        "-f",
        "bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "-o",
        template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        raise ClipkitError("download failed:\n" + "\n".join(tail))

    candidates = sorted(directory.glob(f"{stem}.*"), key=lambda p: p.stat().st_size, reverse=True)
    for candidate in candidates:
        if candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".wav"}:
            return candidate
    raise ClipkitError("download finished but no media file was found")
