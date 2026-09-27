"""Replay an asciicast on a virtual screen to find, exactly, when text first reaches a rectangle.

The code card sits over the empty lower part of the terminal while the agent works. It must be gone before
any character is under it. That moment comes from replaying the cast character by character on a grid that
scrolls like a terminal, never from an estimate. Text can reach the card in two ways, and both are caught:
a character drawn into it, or text drawn below it (after blank lines) that a scroll moves up into it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SPLIT = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z])")

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in output pixels


@dataclass(frozen=True)
class Hit:
    t: float
    row: int
    col: int
    cause: str  # "draw" or "scroll"


def _sgr_background(params: list[str], bg: bool) -> bool:
    """Walk SGR parameters in order; 38/48 take a 5;n or 2;r;g;b argument that must not be read as codes."""
    i = 0
    while i < len(params):
        p = params[i]
        if p in ("", "0", "49"):
            bg = False
        elif p in ("38", "48"):
            if p == "48":
                bg = True
            nxt = params[i + 1] if i + 1 < len(params) else ""
            i += 4 if nxt == "2" else 2 if nxt == "5" else 0
        elif p.isdigit() and (40 <= int(p) <= 47 or 100 <= int(p) <= 107):
            bg = True
        i += 1
    return bg


def _chars(events: Iterable[list]):
    """(time, character, has_background) for every character; a space with a background colour is visible."""
    bg = False
    for t, kind, data in events:
        if kind != "o":
            continue
        for tok in SPLIT.split(data):
            if not tok:
                continue
            if CSI.fullmatch(tok):
                if tok.endswith("m"):
                    bg = _sgr_background(tok[2:-1].split(";"), bg)
                continue
            for ch in tok:
                yield float(t), ch, bg


def first_hit(events: Iterable[list], rows: int, cols: int, rect: Rect,
              cell_rect: Callable[[int, int], Rect]) -> Hit | None:
    """The first moment a non-blank character is inside `rect`, or None if it never is."""
    rx0, ry0, rx1, ry1 = rect
    covered = set()
    for r in range(rows):
        for c in range(cols):
            x0, y0, x1, y1 = cell_rect(r, c)
            if x1 > rx0 and x0 < rx1 and y1 > ry0 and y0 < ry1:
                covered.add((r, c))
    occupied: set[tuple[int, int]] = set()
    row = col = 0

    def newline(t: float) -> Hit | None:
        nonlocal row, occupied
        row += 1
        if row < rows:
            return None
        row = rows - 1
        occupied = {(r - 1, c) for (r, c) in occupied if r > 0}  # scroll up one row; the bottom row is blank
        moved = sorted(occupied & covered)
        return Hit(t, moved[0][0], moved[0][1], "scroll") if moved else None

    for t, ch, bg in _chars(events):
        if ch == "\r":
            col = 0
        elif ch == "\n":
            if hit := newline(t):
                return hit
        elif ch == "\x1b":
            continue
        else:
            if col >= cols:  # auto-wrap at the right margin
                col = 0
                if hit := newline(t):
                    return hit
            if ch.isspace() and not bg:
                occupied.discard((row, col))
            else:
                occupied.add((row, col))
                if (row, col) in covered:
                    return Hit(t, row, col, "draw")
            col += 1
    return None


def screen_text(events: Iterable[list], rows: int, cols: int) -> list[str]:
    """Every line the cast ever prints, as plain text (colour codes removed). Used by the privacy scan."""
    lines, cur = [], ""
    for _, ch, _bg in _chars(events):
        if ch == "\n":
            lines.append(cur)
            cur = ""
        elif ch not in "\r\x1b":
            cur += ch
    if cur:
        lines.append(cur)
    return lines
