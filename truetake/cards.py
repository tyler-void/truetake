"""Code cards, step captions and title cards, drawn with Pillow in Catppuccin Mocha.

A code card shows real code: the excerpt comes from `git show <commit>:<file>` and every rendered line is
checked against it before anything is drawn. The only addition allowed is a "…" row between two
non-adjacent line ranges. The title bar names the file, the lines and the commit.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound

from . import fonts

SKIP = "…"
MAX_ROWS = 20

# Catppuccin Mocha
CRUST, MANTLE, BASE = "#11111b", "#181825", "#1e1e2e"
SURFACE0, SURFACE1, OVERLAY0 = "#313244", "#45475a", "#6c7086"
TEXT, SUBTEXT1, YELLOW, PEACH = "#cdd6f4", "#bac2de", "#f9e2af", "#fab387"
RED, GREEN = "#f38ba8", "#a6e3a1"
STYLE = "catppuccin-mocha"

# Code is shown character for character: ligatures (=== as one glyph, => as an arrow) would change what the
# viewer reads, so they are switched off.
NO_LIGATURES = ["-liga", "-calt", "-dlig"]


class VerbatimError(ValueError):
    """A card row does not match the source at the pinned commit."""


def git_show(repo: Path, commit: str, file: str) -> list[str]:
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{file}"], capture_output=True)
    if proc.returncode != 0:
        raise VerbatimError(f"git show {commit[:12]}:{file} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode("utf-8").split("\n")


def excerpt(source: list[str], ranges: list[tuple[int, int]]) -> list[tuple[int | None, str]]:
    """The rows of a card: (line number, text) for each line in `ranges`, with one (None, "…") row between
    ranges that are not adjacent."""
    rows: list[tuple[int | None, str]] = []
    n_lines = len(source) - (1 if source and source[-1] == "" else 0)  # a final newline is not a line
    for i, (a, b) in enumerate(ranges):
        if b > n_lines:
            raise VerbatimError(f"line range {a}-{b} is past the end of the file ({n_lines} lines)")
        if i and a != ranges[i - 1][1] + 1:
            rows.append((None, SKIP))
        rows.extend((n, source[n - 1]) for n in range(a, b + 1))
    if len(rows) > MAX_ROWS:
        raise VerbatimError(f"a card shows at most {MAX_ROWS} rows; this one has {len(rows)}")
    return rows


def verify(rows: list[tuple[int | None, str]], source: list[str], ranges: list[tuple[int, int]]) -> None:
    """Refuse any row that is not the source line it claims to be, and any "…" row that is not a gap between
    two ranges. Called on exactly the rows that are drawn."""
    expected_skips = sum(1 for i in range(1, len(ranges)) if ranges[i][0] != ranges[i - 1][1] + 1)
    skips = 0
    wanted = [n for a, b in ranges for n in range(a, b + 1)]
    numbers = [n for n, _ in rows if n is not None]
    if numbers != wanted:
        raise VerbatimError(f"card rows cover lines {numbers}, the spec asks for {wanted}")
    prev = None
    for n, text in rows:
        if n is None:
            if text != SKIP or prev is None:
                raise VerbatimError(f"only a '{SKIP}' row between ranges may be added, found {text!r}")
            skips += 1
        elif source[n - 1] != text:
            raise VerbatimError(f"line {n} differs from the source at the pinned commit")
        prev = n
    if skips != expected_skips:
        raise VerbatimError(f"expected {expected_skips} '{SKIP}' rows, found {skips}")


@dataclass
class CardImage:
    path: Path
    box: tuple[int, int, int, int]  # the card itself inside the PNG (the rest is its shadow margin)
    size: tuple[int, int]
    rows: list[tuple[int | None, str]]
    title: str


def _token_runs(text: str, lexer, style) -> list[tuple[str, str]]:
    runs = []
    for ttype, value in lexer.get_tokens(text):
        t = ttype
        while not style.style_for_token(t)["color"] and t.parent:
            t = t.parent
        colour = style.style_for_token(t)["color"]
        runs.append((value.rstrip("\n"), f"#{colour}" if colour else TEXT))
    return runs


def render_card(*, repo: Path, commit: str, file: str, ranges: list[tuple[int, int]], explain: str, out: Path,
                scale: float, fonts_cfg: dict, mono_family: str, tabsize: int = 4) -> CardImage:
    source = git_show(repo, commit, file)
    rows = excerpt(source, ranges)
    verify(rows, source, ranges)

    code_px, pitch = round(24 * scale), round(32 * scale)
    mono = fonts.load(fonts.resolve(fonts_cfg, "mono", mono_family), code_px)
    mono_b = fonts.load(fonts.resolve(fonts_cfg, "mono_bold", mono_family), round(22 * scale))
    sans_b = fonts.load(fonts.resolve(fonts_cfg, "sans_bold", mono_family, explain), round(28 * scale), "SemiBold")
    try:
        lexer = get_lexer_for_filename(file, stripnl=False, ensurenl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False, ensurenl=False)
    style = get_style_by_name(STYLE)

    spans = ", ".join(f"{a}–{b}" if a != b else f"{a}" for a, b in ranges)
    title = f"{file}  ·  lines {spans}"
    right = f"@ {commit[:7]}"
    shown = [(n, t.expandtabs(tabsize)) for n, t in rows]  # tabs are drawn as spaces; the check used the raw text
    digits = max(len(str(n)) for n, _ in rows if n is not None)
    gutter = int(mono.getlength("0") * (digits + 1)) + round(20 * scale)
    pad = round(16 * scale)
    code_w = int(max(mono.getlength(t, features=NO_LIGATURES) for _, t in shown)) + gutter + 2 * pad
    w = max(code_w, int(sans_b.getlength(explain)) + 4 * pad,
            int(mono_b.getlength(title) + mono_b.getlength(right)) + round(150 * scale), round(900 * scale))
    title_h, expl_h = round(48 * scale), round(56 * scale)
    h = title_h + expl_h + pad + pitch * len(rows) + pad
    shadow = round(24 * scale)

    img = Image.new("RGBA", (w + 2 * shadow, h + 2 * shadow), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([shadow, shadow + 8, shadow + w, shadow + h + 8], 16, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(img)
    x0, y0 = shadow, shadow
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], 16, fill=MANTLE, outline=SURFACE1, width=2)
    d.rounded_rectangle([x0 + 1, y0 + 1, x0 + w - 1, y0 + title_h], 15, fill=CRUST)
    d.rectangle([x0 + 1, y0 + title_h - 16, x0 + w - 1, y0 + title_h], fill=CRUST)
    for i, colour in enumerate((RED, YELLOW, GREEN)):
        cx, cy = x0 + round(26 * scale) + i * round(24 * scale), y0 + title_h / 2
        r = round(7 * scale)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    d.text((x0 + round(110 * scale), y0 + title_h / 2), title, font=mono_b, fill=SUBTEXT1, anchor="lm", features=NO_LIGATURES)
    d.text((x0 + w - pad, y0 + title_h / 2), right, font=mono_b, fill=OVERLAY0, anchor="rm", features=NO_LIGATURES)
    ey = y0 + title_h
    d.rectangle([x0 + 1, ey, x0 + w - 1, ey + expl_h], fill=SURFACE0)
    d.text((x0 + 2 * pad, ey + expl_h / 2), explain, font=sans_b, fill=YELLOW, anchor="lm")
    cy = ey + expl_h + pad
    for n, text in shown:
        base = cy + pitch / 2
        if n is None:
            d.text((x0 + gutter, base), text, font=mono, fill=SUBTEXT1, anchor="lm", features=NO_LIGATURES)
        else:
            d.text((x0 + gutter - round(20 * scale), base), str(n), font=mono, fill=OVERLAY0, anchor="rm", features=NO_LIGATURES)
            x = x0 + gutter
            for value, colour in _token_runs(text, lexer, style):
                d.text((x, base), value, font=mono, fill=colour, anchor="lm", features=NO_LIGATURES)
                x += mono.getlength(value, features=NO_LIGATURES)
        cy += pitch
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return CardImage(path=out, box=(shadow, shadow, shadow + w, shadow + h), size=img.size, rows=rows,
                     title=f"{title}  {right}")


def render_caption(text: str, out: Path, scale: float, fonts_cfg: dict, mono_family: str) -> tuple[int, int]:
    font = fonts.load(fonts.resolve(fonts_cfg, "sans_bold", mono_family, text), round(46 * scale), "Bold")
    padx, h = round(28 * scale), round(72 * scale)
    w = int(font.getlength(text)) + 2 * padx
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], round(18 * scale), fill=SURFACE0 + "f2", outline=SURFACE1, width=2)
    d.text((padx, h / 2), text, font=font, fill=TEXT, anchor="lm")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return img.size


def render_title(title: str, subtitle: str, out: Path, width: int, height: int, fonts_cfg: dict, mono_family: str) -> None:
    scale = height / 1080
    big = fonts.load(fonts.resolve(fonts_cfg, "sans_bold", mono_family, title), round(96 * scale), "Bold")
    small = fonts.load(fonts.resolve(fonts_cfg, "sans", mono_family, subtitle), round(40 * scale))
    img = Image.new("RGB", (width, height), BASE)
    d = ImageDraw.Draw(img)
    d.text((width / 2, height / 2 - round(40 * scale)), title, font=big, fill=TEXT, anchor="mm")
    if subtitle:
        d.text((width / 2, height / 2 + round(60 * scale)), subtitle, font=small, fill=SUBTEXT1, anchor="mm")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
