"""The film's look: colour tokens and three fonts (serif for the clock, sans for labels, mono for panes).

A theme is a set of named colours, so a film can take on a team's own design system instead of a default
"AI video" look. Two are built in; `film.theme` in truetake.yaml can also be a mapping that overrides any
token of the one it names in `base`.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path

from .. import fonts
from .script import FilmError

THEMES = {
    # Marginalia, Paper: ink on paper, quill blue as the one accent, gilt for the cursor
    "marginalia": dict(stage=0xFFF8F2E9, ink=0xFF2B2018, ink_soft=0xFF5E4F44, rule=0xFFDFD6C9, accent=0xFF2E567A,
                       pane=0xFF2B2018, text=0xFFF3EADB, dim=0xFF9C8B7C, command=0xFFEFE0BC, call=0xFF8FB3D4,
                       ok=0xFFA9C79A, prompt=0xFFEFE0BC, cursor=0xFFEFE0BC, shadow=0xFF2B2018),
    # Catppuccin Mocha, as truetake's terminal scenes
    "mocha": dict(stage=0xFF11111B, ink=0xFFCDD6F4, ink_soft=0xFFA6ADC8, rule=0xFF313244, accent=0xFFFAB387,
                  pane=0xFF1E1E2E, text=0xFFCDD6F4, dim=0xFF7F849C, command=0xFFF9E2AF, call=0xFF89B4FA,
                  ok=0xFFA6E3A1, prompt=0xFF94E2D5, cursor=0xFFF5E0DC, shadow=0xFF000000),
}


@dataclass(frozen=True)
class Theme:
    stage: int
    ink: int
    ink_soft: int
    rule: int
    accent: int
    pane: int
    text: int
    dim: int
    command: int
    call: int
    ok: int
    prompt: int
    cursor: int
    shadow: int
    serif: str = ""
    sans: str = ""
    mono: str = ""

    def role(self, role: str) -> int:
        return {"message": self.text, "output": self.dim, "dim": self.dim, "command": self.command,
                "call": self.call, "ok": self.ok, "prompt": self.prompt, "blank": self.text}[role]


def _colour(v) -> int:
    s = str(v).lstrip("#")
    if len(s) == 6:
        s = "FF" + s
    try:
        return int(s, 16)
    except ValueError:
        raise FilmError(f"theme colour {v!r} is not #RRGGBB")


def _font(path: str, role: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise FilmError(f"film font `{role}`: {p} not found")
    if p.suffix.lower() in (".woff", ".woff2"):
        raise FilmError(f"film font `{role}`: {p.name} is a web font, which Skia cannot read; convert it once: "
                        f"python -c \"from fontTools.ttLib import TTFont; t=TTFont('{p}'); t.flavor=None; "
                        f"t.save('{p.with_suffix('.ttf')}')\"")
    return str(p)


def load(spec, fonts_cfg: dict[str, str], mono_family: str) -> Theme:
    spec = spec or "marginalia"
    if isinstance(spec, str):
        spec = {"base": spec}
    base = spec.get("base", "marginalia")
    if base not in THEMES:
        raise FilmError(f"film.theme: unknown base {base!r}; use one of {sorted(THEMES)}")
    names = {f.name for f in fields(Theme)} - {"serif", "sans", "mono"}
    unknown = set(spec) - names - {"base"}
    if unknown:
        raise FilmError(f"film.theme: unknown token(s) {sorted(unknown)}; tokens are {sorted(names)}")
    tokens = dict(THEMES[base], **{k: _colour(v) for k, v in spec.items() if k != "base"})
    serif = _font(fonts_cfg["serif"], "serif") if "serif" in fonts_cfg else fonts.find("serif:style=Regular")
    sans = _font(fonts_cfg["sans"], "sans") if "sans" in fonts_cfg else fonts.find("sans-serif:style=Regular")
    mono = _font(fonts_cfg["mono"], "mono") if "mono" in fonts_cfg else fonts.find(f"{mono_family}:style=Regular")
    return replace(Theme(**tokens), serif=serif, sans=sans, mono=mono)
