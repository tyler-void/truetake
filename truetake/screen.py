"""Replay an asciicast on a virtual terminal to find, exactly, when text first reaches a rectangle.

The code card sits over the empty lower part of the terminal while the agent works. It must be gone before
any character is under it. That moment comes from replaying the cast character by character on a terminal
emulator ([pyte](https://github.com/selectel/pyte)), never from an estimate. Text can reach the card in two
ways, and both are caught: a character drawn into it, or text that a scroll moves up into it.

The same emulator follows full-screen programs (cursor moves, erases, the alternate screen), so a recorded
TUI session is checked the way a rebuilt agent session is.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Callable, Iterable

import pyte

CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SPLIT = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z])")
ALT_SCREEN = (47, 1047, 1049)  # private modes that switch to and from the alternate screen

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in output pixels


@dataclass(frozen=True)
class Hit:
    t: float
    row: int
    col: int
    cause: str  # "draw" or "scroll"


class Term(pyte.Screen):
    """A pyte screen with the alternate screen (which pyte leaves out) and a flag set when the screen scrolls."""

    def __init__(self, cols: int, rows: int):
        super().__init__(cols, rows)
        self.scrolled = False
        self._main = None

    def index(self) -> None:
        if self.cursor.y == self.margins_bottom():
            self.scrolled = True
        super().index()

    def margins_bottom(self) -> int:
        return self.margins.bottom if self.margins else self.lines - 1

    def set_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and any(m in ALT_SCREEN for m in modes):
            if self._main is None:
                self._main = (copy.deepcopy(self.buffer), copy.copy(self.cursor))
                self.erase_in_display(2)
            modes = tuple(m for m in modes if m not in ALT_SCREEN)
            if not modes:
                return
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and any(m in ALT_SCREEN for m in modes):
            if self._main is not None:
                self.buffer, self.cursor = self._main
                self._main = None
                self.dirty.update(range(self.lines))
            modes = tuple(m for m in modes if m not in ALT_SCREEN)
            if not modes:
                return
        super().reset_mode(*modes, **kwargs)

    def rows_text(self) -> list[str]:
        return [line.rstrip() for line in self.display]

    def visible(self, row: int, col: int) -> bool:
        """A cell shows something: a character, or a space with a background colour (or reversed)."""
        ch = self.buffer[row][col]
        return ch.data.strip() != "" or ch.bg != "default" or ch.reverse


def _replay(events: Iterable[list], rows: int, cols: int):
    """Feed the cast one character at a time; yield (time, screen) after each character."""
    term = Term(cols, rows)
    stream = pyte.Stream(term)
    for t, kind, data in events:
        if kind != "o":
            continue
        for ch in data:
            term.scrolled = False
            stream.feed(ch)
            yield float(t), term


def first_hit(events: Iterable[list], rows: int, cols: int, rect: Rect,
              cell_rect: Callable[[int, int], Rect]) -> Hit | None:
    """The first moment a visible cell is inside `rect`, or None if it never is."""
    rx0, ry0, rx1, ry1 = rect
    covered: dict[int, list[int]] = {}
    for r in range(rows):
        for c in range(cols):
            x0, y0, x1, y1 = cell_rect(r, c)
            if x1 > rx0 and x0 < rx1 and y1 > ry0 and y0 < ry1:
                covered.setdefault(r, []).append(c)
    for t, term in _replay(events, rows, cols):
        if not term.dirty:
            continue
        for r in sorted(term.dirty & covered.keys()):
            for c in covered[r]:
                if term.visible(r, c):
                    return Hit(t, r, c, "scroll" if term.scrolled else "draw")
        term.dirty.clear()
    return None


def snapshots(events: Iterable[list], rows: int, cols: int) -> list[tuple[float, list[str]]]:
    """The visible screen after each moment of the cast: (time, one string per row). A callout box follows its
    line through these as the terminal scrolls or a full-screen program redraws."""
    term = Term(cols, rows)
    stream = pyte.Stream(term)
    out: list[tuple[float, list[str]]] = []
    last_t = None
    for t, kind, data in events:
        if kind != "o" or not data:
            continue
        if last_t is not None and float(t) != last_t:
            out.append((last_t, term.rows_text()))
        last_t = float(t)
        stream.feed(data)
    if last_t is not None:
        out.append((last_t, term.rows_text()))
    return out


def seen_rows(events: Iterable[list], rows: int, cols: int) -> list[str]:
    """Every distinct non-blank row the screen ever shows, in order of first appearance. The privacy scan of a
    recorded session, where text is placed by cursor moves rather than printed line by line."""
    seen: dict[str, None] = {}
    for _, screen_rows in snapshots(events, rows, cols):
        for row in screen_rows:
            if row.strip():
                seen.setdefault(row, None)
    return list(seen)


def screen_text(events: Iterable[list], rows: int, cols: int) -> list[str]:
    """Every line the cast ever prints, as plain text (colour codes removed). Used by the privacy scan of a
    rebuilt agent session, which only ever prints line by line."""
    lines, cur = [], ""
    for t, kind, data in events:
        if kind != "o":
            continue
        for tok in SPLIT.split(data):
            if not tok or CSI.fullmatch(tok):
                continue
            for ch in tok:
                if ch == "\n":
                    lines.append(cur)
                    cur = ""
                elif ch not in "\r\x1b":
                    cur += ch
    if cur:
        lines.append(cur)
    return lines
