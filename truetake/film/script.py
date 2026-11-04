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
    lapse_step: float = 0.05  # time-lapse: film seconds per item; lines land whole, nothing is typed
    lapse_idle_scale: float = 0.004
    lapse_idle_cap: float = 0.12
    settle: float = 0.6  # a pause when the film enters or leaves a beat


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
    panes: dict[str, list[Line]]  # pane id -> its lines, in order of first appearance
    timemap: list[tuple[float, float]]  # (film t, real seconds); empty when the sources have no timestamps
    transforms: dict[str, int]
    started: dt.datetime | None
    origin: str
    pane_transforms: dict[str, dict[str, int]] = field(default_factory=dict)
    lapses: list[tuple[float, float]] = field(default_factory=list)  # film-time spans run as a time-lapse

    @property
    def lines(self) -> list[Line]:
        """The first pane's lines (the only pane of a single-pane film)."""
        return next(iter(self.panes.values()), [])

    @property
    def end(self) -> float:
        return max((l.t1 for ls in self.panes.values() for l in ls), default=0.0)

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
    """One source in one pane."""
    return build_set([("main", src)], cols, pacing, scanner, home, until)


def build_set(sources: list[tuple[str, Source]], cols: int, pacing: Pacing | None = None,
              scanner: Scanner | None = None, home: str | None = None, until: float | None = None,
              beats: list[tuple[float, float]] | None = None) -> Script:
    """Pace several sources into panes on one timeline.

    Items from every source are merged in real-time order, so one clock is true for every pane. `beats` are
    real-time windows (seconds since the earliest source starts) played at reading pace; outside them the film
    runs as a time-lapse: lines land whole, a few hundredths of a second apart, and the clock races. Without
    beats, everything is read. With `until` (the film's length), pacing stops at the first item that would
    start after it, so the transforms counted are those of what the film shows.
    """
    p = pacing or Pacing()
    home = home if home is not None else os.path.expanduser("~")
    width = cols - 4
    timed = [s.started is not None for _, s in sources]
    if len(sources) > 1 and not all(timed):
        raise FilmError("several panes need sources with timestamps (Codex transcripts), so one clock is true for all")
    t_zero = min((s.started for _, s in sources if s.started), default=None)
    pane_tr = {pid: {"home": 0, "shell_wrap": 0, "elided": 0, "hidden": 0, "tabs": 0} for pid, _ in sources}
    panes: dict[str, list[Line]] = {pid: [] for pid, _ in sources}

    # (real seconds since t_zero, order, pane, item); a source without timestamps keeps its own order
    merged = []
    for k, (pid, src) in enumerate(sources):
        off = (src.started - t_zero).total_seconds() if src.started and t_zero else 0.0
        for j, it in enumerate(src.items):
            merged.append(((off + it.real) if it.real is not None else None, k, j, pid, it))
    if all(timed):
        merged.sort(key=lambda m: (m[0], m[1], m[2]))

    timemap: list[tuple[float, float]] = []
    lapses: list[tuple[float, float]] = []
    ft, last_real = p.lead_in, None
    mode = None  # "read" or "lapse"

    def in_beat(real: float | None) -> bool:
        return not beats or real is None or any(b0 <= real <= b1 for b0, b1 in beats)

    for real, _, _, pid, it in merged:
        if until is not None and ft > until:
            break
        tr = pane_tr[pid]
        lines = panes[pid]

        def clean(s: str) -> str:
            if home and home != "/" and home in s:
                tr["home"] += s.count(home)
                s = s.replace(home, "~")
            if "\t" in s:
                tr["tabs"] += 1
                s = s.expandtabs(8)
            return s

        def add(text: str, role: str, cps: float = 0.0, indent: int = 0) -> None:
            nonlocal ft
            lines.append(Line(text, role, ft, cps, indent))
            if cps:
                ft += len(text) / cps

        reading = in_beat(real)
        now = "read" if reading else "lapse"
        if real is not None:
            # The clock stands still while an item is on its way to the screen (it had already happened), and
            # runs fast through the idle time between items: real time passes only where it passed.
            if timemap:
                timemap.append((ft, last_real))
            if mode is not None and now != mode:
                ft += p.settle
                timemap.append((ft, last_real))
            gap = real - (last_real if last_real is not None else real)
            ft += min(gap * p.idle_scale, p.idle_cap) if reading else min(gap * p.lapse_idle_scale, p.lapse_idle_cap)
            last_real = real
            timemap.append((ft, real))
        if now == "lapse" and mode != "lapse":
            lapses.append((ft, ft))
        mode = now
        start = ft

        if it.kind in ("message", "hook"):
            text = clean(it.text)
            typed = 0.0
            for i, part in enumerate(wrap(text, width - 2)):
                lead = ("• " if it.kind == "message" else "↳ ") if i == 0 else "  "
                role = "message" if it.kind == "message" else "dim"
                if reading:
                    add(lead + part, role, p.message_cps)
                    typed += len(part) / p.message_cps
                else:
                    add(lead + part, role)
            if reading:
                ft += min(p.beat_max, max(p.beat_min, len(text.split()) / p.read_wps - typed))
            add("", "blank")
        elif it.kind in ("command", "tool", "prompt"):
            text = it.text
            m = SHELL_WRAP.match(text)
            if m:
                text, tr["shell_wrap"] = m.group(1), tr["shell_wrap"] + 1
            lead = {"command": "$ ", "tool": "● ", "prompt": "> "}[it.kind]
            for part in wrap(lead + clean(text), width):
                add(part, "prompt" if it.kind == "prompt" else "command", p.command_cps if reading else 0.0)
            if reading:
                ft += p.after_command
            if it.kind != "command":
                add("", "blank")
        elif it.kind == "output":
            rows = [clean(r) for r in it.lines]
            if scanner and any(scanner.scan("film", r) for r in rows):
                tr["hidden"] += 1  # never edit a row: hide the whole output and say so on screen
                rows = [f"[{len(rows)} lines hidden: they contain a private name]"]
            if len(rows) > p.output_rows:
                tr["elided"] += 1
                rows = rows[:p.output_rows] + [f"… {len(rows) - p.output_rows} more lines"]
            for r in rows:
                add(r[:width], "output", indent=2)
                if reading:
                    ft += p.output_step
            add("", "blank")
            if reading:
                ft += p.after_output
        elif it.kind == "call":
            add("◆ " + it.text, "call", p.call_cps if reading else 0.0)
            if reading:
                ft += p.after_call
        elif it.kind == "return":
            add(f"✓ {it.text} returned after {it.seconds:.1f}s", "ok", indent=2)
            add("", "blank")
            if reading:
                ft += p.after_return
        elif it.kind == "reasoning":
            if reading:
                add("reasoning", "dim", indent=2)
                ft += 0.5
        if not reading:
            ft = max(ft, start + p.lapse_step)
            lapses[-1] = (lapses[-1][0], ft)

    if scanner:  # the gate: anything else private on screen stops the film before a frame exists
        hits = [h for pid, ls in panes.items() for i, l in enumerate(ls) for h in scanner.scan(f"{pid} line {i + 1}", l.text)]
        if hits:
            shown = "\n".join(f"  {h.source} {h.kind}: {h.snippet}" for h in hits[:10])
            raise FilmError(f"privacy gate: {len(hits)} hit(s) in the film's text; nothing was rendered\n{shown}")
    total = {k: sum(t[k] for t in pane_tr.values()) for k in next(iter(pane_tr.values()))}
    origin = "; ".join(s.origin for _, s in sources)
    return Script(panes, timemap, total, t_zero, origin, pane_tr, lapses)
