"""Find font files. Nothing is bundled: fonts come from the system through fontconfig (`fc-match`), or from
explicit paths under `fonts:` in truetake.yaml (`mono`, `mono_bold`, `sans`, `sans_bold`)."""
from __future__ import annotations

import shutil
import subprocess

from PIL import ImageFont

_cache: dict[tuple, str] = {}


def find(pattern: str, chars: str = "") -> str:
    """The file fontconfig picks for `pattern` (e.g. "JetBrains Mono:style=Bold"). With `chars`, prefer a font
    that has every one of those characters (fontconfig's charset match)."""
    key = (pattern, chars)
    if key in _cache:
        return _cache[key]
    if not shutil.which("fc-match"):
        raise RuntimeError("fc-match not found: install fontconfig, or set font paths under `fonts:` in truetake.yaml")
    q = pattern
    wanted = sorted({ord(c) for c in chars if ord(c) > 0x7F})
    if wanted:
        q += ":charset=" + " ".join(f"{c:x}" for c in wanted)
    out = subprocess.run(["fc-match", "-f", "%{file}", q], capture_output=True, text=True).stdout.strip()
    if not out:
        raise RuntimeError(f"fontconfig found no font for {pattern!r}")
    _cache[key] = out
    return out


def load(path: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    if weight:
        try:  # variable fonts carry their weights as named instances
            font.set_variation_by_name(weight)
        except (OSError, ValueError):
            pass
    return font


def resolve(fonts_cfg: dict[str, str], role: str, family: str, chars: str = "") -> str:
    if role in fonts_cfg:
        return fonts_cfg[role]
    style = "Bold" if role.endswith("_bold") else "Regular"
    base = family if role.startswith("mono") else "sans-serif"
    return find(f"{base}:style={style}", chars)
