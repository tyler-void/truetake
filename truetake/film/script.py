"""A film's script: what a pane shows, line by line, and when.

Sources are read as they were recorded; nothing is retyped. A source becomes a list of items (an agent
message, a command and its output, an MCP call and its return), and pacing turns items into lines with film
times. The text of a line is the source's text; the changes allowed are counted in `transforms` and listed in
the report:

  home        the home directory shown as `~`
  shell_wrap  a `/bin/zsh -lc '…'` (or bash) wrapper around a command dropped
  elided      a long output cut to its first lines, with a `… N more lines` row
  hidden      an output that would show a private name replaced by one row that says so
  tabs        tabs expanded to spaces, as a terminal shows them

Real time: sources with timestamps (Codex transcripts) get a time map from film time to real time, so the
film can show a clock and how fast it runs. Sources without them (a `claude -p` stream) get no clock.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..scan import Scanner

SHELL_WRAP = re.compile(r"^/(?:usr/)?bin/(?:zsh|bash|sh) -l?c '(.*)'$", re.S)


class FilmError(RuntimeError):
    pass


@dataclass
class Item:
    kind: str  # message | command | output | call | return | reasoning | tool | prompt | hook
    text: str = ""
    real: float | None = None  # seconds since the source's first event
    lines: list[str] = field(default_factory=list)  # output rows
    seconds: float | None = None  # call duration, from the source's own timestamps


@dataclass
class Source:
    items: list[Item]
    started: dt.datetime | None  # wall-clock time of real = 0, when the source has timestamps
    origin: str  # what the report names as the source


@dataclass
class Pacing:
    message_cps: float = 24.0  # a little above reading speed: the reader follows the cursor
    command_cps: float = 40.0
    call_cps: float = 40.0
    read_wps: float = 3.3  # words per second a viewer reads
    beat_min: float = 0.5  # pause after a message, at least
    beat_max: float = 1.5  # and at most
    output_rows: int = 3  # rows of an output shown before `… N more lines`
    output_step: float = 0.12  # seconds between output rows
    after_output: float = 0.7
    after_command: float = 0.4
    after_call: float = 0.35
    after_return: float = 0.6
    idle_scale: float = 0.08  # real idle time between events, scaled
    idle_cap: float = 0.5  # and capped (the clock shows the compression)
    lead_in: float = 0.8


@dataclass
class Line:
    text: str
    role: str  # message | command | output | call | ok | dim | prompt | blank
    t0: float
    cps: float = 0.0  # 0: the whole line at once
    indent: int = 0

    def shown(self, t: float) -> int:
        if t < self.t0:
            return 0
        if not self.cps:
            return len(self.text)
        return min(len(self.text), int((t - self.t0) * self.cps) + 1)

    def char_time(self, i: int) -> float:
        return self.t0 + (i / self.cps if self.cps else 0.0)

    @property
    def t1(self) -> float:
        return self.t0 + (len(self.text) / self.cps if self.cps else 0.0)


@dataclass
class Script:
    lines: list[Line]
    timemap: list[tuple[float, float]]  # (film t, real seconds); empty when the source has no timestamps
    transforms: dict[str, int]
    started: dt.datetime | None
    origin: str

    def real_at(self, t: float) -> float | None:
        if not self.timemap:
            return None
        pts = self.timemap
        if t <= pts[0][0]:
            return pts[0][1]
        for (f0, r0), (f1, r1) in zip(pts, pts[1:]):
            if t <= f1:
                return r0 if f1 == f0 else r0 + (r1 - r0) * (t - f0) / (f1 - f0)
        return pts[-1][1]

    def rate_at(self, t: float, h: float = 0.25) -> float | None:
        """Real seconds per film second around `t` (1 is real time; 0 is held still to read)."""
        if not self.timemap:
            return None
        return (self.real_at(t + h) - self.real_at(t - h)) / (2 * h)


# ---------- sources ----------

def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def codex_transcript(path: Path) -> Source:
    """A Codex transcript (`transcript-NN.ndjson` from MTM Player): ITEM.STARTED/COMPLETED events with timestamps."""
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not events:
        raise FilmError(f"{path}: no events")
    t0 = _ts(events[0]["timestamp"])
    items: list[Item] = []
    started_call: dict[str, float] = {}
    for e in events:
        real = (_ts(e["timestamp"]) - t0).total_seconds()
        kind, d = e.get("type", ""), e.get("detail") or {}
        if kind == "ITEM.COMPLETED:AGENT_MESSAGE" and (e.get("text") or "").strip():
            items.append(Item("message", e["text"].strip(), real))
        elif kind == "ITEM.STARTED:COMMAND_EXECUTION":
            items.append(Item("command", d.get("input", ""), real))
        elif kind == "ITEM.COMPLETED:COMMAND_EXECUTION":
            items.append(Item("output", real=real, lines=[l for l in (d.get("output") or "").splitlines() if l.strip()]))
        elif kind == "ITEM.STARTED:MCP_TOOL_CALL":
            started_call[d.get("name", "")] = real
            items.append(Item("call", d.get("name", ""), real))
        elif kind == "ITEM.COMPLETED:MCP_TOOL_CALL":
            name = d.get("name", "")
            items.append(Item("return", name, real, seconds=real - started_call.pop(name, real)))
        elif kind == "ITEM.STARTED:REASONING":
            items.append(Item("reasoning", real=real))
    return Source(items, t0, f"Codex transcript {path.name}")


def claude_take(take, prompt: str, cwd: str) -> Source:
    """A recorded `claude -p` take. The stream has no per-event timestamps, so the film shows no clock."""
    from .. import cast as castmod

    items = [Item("prompt", prompt)]
    for s in take.steps:
        if s.kind == "text" and s.text.strip():
            items.append(Item("message", s.text.strip()))
        elif s.kind == "tool":
            items.append(Item("tool", castmod.header(s, cwd)))
        elif s.kind == "hook" and s.text.strip():
            items.append(Item("hook", s.text.strip()))
        elif s.kind == "prompt":
            items.append(Item("prompt", s.text))
    return Source(items, None, f"claude -p take {take.path.name}")


# ---------- pacing ----------

def wrap(text: str, width: int) -> list[str]:
    out = []
    for para in text.split("\n"):
        while len(para) > width:
            cut = para.rfind(" ", 0, width)
            cut = cut if cut > width // 2 else width
            out.append(para[:cut])
            para = para[cut:].lstrip()
        out.append(para)
    return out


def build(src: Source, cols: int, pacing: Pacing | None = None, scanner: Scanner | None = None,
          home: str | None = None, until: float | None = None) -> Script:
    """Pace `src` into lines. With `until` (the film's length), stop at the first item that would start after
    it, so the transforms counted are those of what the film shows."""
    p = pacing or Pacing()
    home = home if home is not None else os.path.expanduser("~")
    tr = {"home": 0, "shell_wrap": 0, "elided": 0, "hidden": 0, "tabs": 0}
    width = cols - 4

    def clean(s: str) -> str:
        if home and home != "/" and home in s:
            tr["home"] += s.count(home)
            s = s.replace(home, "~")
        if "\t" in s:
            tr["tabs"] += 1
            s = s.expandtabs(8)
        return s

    def private(rows: list[str]) -> bool:
        return bool(scanner and any(scanner.scan("film", r) for r in rows))

    lines: list[Line] = []
    timemap: list[tuple[float, float]] = []
    ft, last_real = p.lead_in, 0.0

    def add(text: str, role: str, cps: float = 0.0, indent: int = 0) -> None:
        nonlocal ft
        lines.append(Line(text, role, ft, cps, indent))
        if cps:
            ft += len(text) / cps

    for it in src.items:
        if until is not None and ft > until:
            break
        if it.real is not None:
            # The clock stands still while an item is on its way to the screen (it had already happened), and
            # runs fast through the idle time between items: real time passes only where it passed.
            if timemap:
                timemap.append((ft, last_real))
            ft += min((it.real - last_real) * p.idle_scale, p.idle_cap)
            last_real = it.real
            timemap.append((ft, it.real))
        if it.kind in ("message", "hook"):
            text = clean(it.text)
            typed = 0.0
            for i, part in enumerate(wrap(text, width - 2)):
                lead = ("• " if it.kind == "message" else "↳ ") if i == 0 else "  "
                add(lead + part, "message" if it.kind == "message" else "dim", p.message_cps)
                typed += len(part) / p.message_cps
            ft += min(p.beat_max, max(p.beat_min, len(text.split()) / p.read_wps - typed))
            add("", "blank")
        elif it.kind in ("command", "tool", "prompt"):
            text = it.text
            m = SHELL_WRAP.match(text)
            if m:
                text, tr["shell_wrap"] = m.group(1), tr["shell_wrap"] + 1
            lead = {"command": "$ ", "tool": "● ", "prompt": "> "}[it.kind]
            for part in wrap(lead + clean(text), width):
                add(part, "prompt" if it.kind == "prompt" else "command", p.command_cps)
            ft += p.after_command
            if it.kind != "command":
                add("", "blank")
        elif it.kind == "output":
            rows = [clean(r) for r in it.lines]
            if private(rows):  # never edit a row: hide the whole output and say so on screen
                tr["hidden"] += 1
                rows = [f"[{len(rows)} lines hidden: they contain a private name]"]
            if len(rows) > p.output_rows:
                tr["elided"] += 1
                rows = rows[:p.output_rows] + [f"… {len(rows) - p.output_rows} more lines"]
            for r in rows:
                add(r[:width], "output", indent=2)
                ft += p.output_step
            add("", "blank")
            ft += p.after_output
        elif it.kind == "call":
            add("◆ " + it.text, "call", p.call_cps)
            ft += p.after_call
        elif it.kind == "return":
            add(f"✓ {it.text} returned after {it.seconds:.1f}s", "ok", indent=2)
            add("", "blank")
            ft += p.after_return
        elif it.kind == "reasoning":
            add("reasoning", "dim", indent=2)
            ft += 0.5

    if scanner:  # the gate: anything else private on screen stops the film before a frame exists
        hits = [h for i, l in enumerate(lines) for h in scanner.scan(f"line {i + 1}", l.text)]
        if hits:
            shown = "\n".join(f"  {h.source} {h.kind}: {h.snippet}" for h in hits[:10])
            raise FilmError(f"privacy gate: {len(hits)} hit(s) in the film's text; nothing was rendered\n{shown}")
    return Script(lines, timemap, tr, src.started, src.origin)
