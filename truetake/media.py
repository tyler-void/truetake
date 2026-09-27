"""Thin wrappers around ffmpeg, ffprobe and other external programs."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ToolMissing(Exception):
    """An external program truetake needs is not on PATH."""


def need(program: str, hint: str) -> str:
    path = shutil.which(program)
    if not path:
        raise ToolMissing(f"`{program}` not found on PATH. {hint}")
    return path


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, raising with its stderr when it fails."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    proc = subprocess.run(argv, **kw)
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode(errors="replace")
        raise RuntimeError(f"{Path(argv[0]).name} failed ({proc.returncode}): {err.strip()[-2000:]}")
    return proc


def ffmpeg(*args: str) -> None:
    need("ffmpeg", "Install ffmpeg from your package manager.")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


def duration(path: Path) -> float:
    need("ffprobe", "ffprobe ships with ffmpeg.")
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout
    return float(out.strip())


def frames(seconds: float, fps: int) -> float:
    """Round a length up to a whole number of frames."""
    import math

    return math.ceil(round(seconds * fps, 6)) / fps
