"""Turn a recorded take into an asciicast v2 file.

Honesty: every word the agent wrote appears verbatim, in order. The only changes are:
  * pacing: typing and streaming speeds and the pauses between beats (all in `Pacing`);
  * Markdown emphasis and heading markers dropped from agent text (`**`, backticks, leading `#`);
  * paths in tool-call headers made relative to the sandbox;
  * the home directory written as `~`, anywhere.
Each change is counted in `Transforms` and listed in the report. Word-wrapping to the terminal width is
layout, not a change. Tool calls become one-line headers; the hook's added context is shown verbatim as a
highlighted block right after the tool call it followed.
"""
from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .takes import Step, Take

RESET, BOLD = "\x1b[0m", "\x1b[1m"
CYAN, GREEN = "\x1b[36m", "\x1b[32m"
PEACH = "\x1b[38;2;250;179;135m"  # Catppuccin Mocha peach
OVERLAY1 = "\x1b[38;2;127;132;156m"
SURFACE0_BG = "\x1b[48;2;49;50;68m"

HOOK_HEADER = "⎿ PostToolUse hook added to the agent's context:"
HOOK_NOTHING = "⎿ PostToolUse hook: nothing added"


@dataclass
class Pacing:
    prompt_cps: float = 30.0
    reply_cps: float = 40.0
    after_tool: float = 0.9
    hook_delay: float = 0.9
    after_hook: float = 4.0
    after_nothing: float = 2.5
    after_text: float = 0.8
    end_hold: float = 3.0
    first_tool_at: float = 0.0  # hold the first tool call until this moment (room for a code card)


@dataclass
class Transforms:
    markdown_markers_dropped: int = 0
    paths_relativized: int = 0
    home_replaced: int = 0


@dataclass
class Cast:
    cols: int
    rows: int
    t: float = 0.0
    events: list[list] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)
    line_times: list[tuple[float, str]] = field(default_factory=list)  # (first visible character, plain text)
    reply_chars: int = 0
    transforms: Transforms = field(default_factory=Transforms)
    pacing: Pacing | None = None
    widest: int = 0
    _cur: str = ""
    _cur_t: float | None = None

    def out(self, s: str) -> None:
        self.events.append([round(self.t, 3), "o", s])

    def pause(self, sec: float) -> None:
        self.t += sec

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, round(self.t, 3))

    def _text(self, visible: str) -> None:
        if visible.strip() and self._cur_t is None:
            self._cur_t = self.t
        self._cur += visible

    def _eol(self) -> None:
        self.widest = max(self.widest, len(self._cur))
        self.line_times.append((round(self._cur_t if self._cur_t is not None else self.t, 3), self._cur))
        self._cur, self._cur_t = "", None

    def line(self, visible: str = "", pre: str = "", post: str = RESET) -> None:
        """A whole line at once. `pre`/`post` are colour codes and are not counted as text."""
        self._text(visible)
        self.out(pre + visible + (post if pre else "") + "\r\n")
        self._eol()

    def stream(self, visible: str, cps: float, indent: str = "", pre: str = "", post: str = RESET) -> None:
        """Type a line character by character at `cps` characters per second."""
        self._text(indent)
        self.out(indent + pre)
        for ch in visible:
            self.t += 1.0 / cps
            self._text(ch)
            self.out(ch)
        self.out((post if pre else "") + "\r\n")
        self._eol()


def scrub_home(s: str, tr: Transforms | None = None, home: str | None = None) -> str:
    home = home or os.path.expanduser("~")
    if home and home != "/" and home in s:
        if tr:
            tr.home_replaced += s.count(home)
        s = s.replace(home, "~")
    return s


def demark(line: str, tr: Transforms | None = None) -> str:
    n = 0
    m = re.match(r"^(\s*)#{1,6}\s+", line)
    if m:
        line = m.group(1) + line[m.end():]
        n += 1
    n += line.count("**") + line.count("`")
    line = line.replace("**", "").replace("`", "")
    if tr:
        tr.markdown_markers_dropped += n
    return line


def wrap_block(text: str, width: int, tr: Transforms | None = None, markdown: bool = True) -> list[str]:
    out: list[str] = []
    for raw in text.split("\n"):
        line = scrub_home(raw, tr)
        if markdown:
            line = demark(line, tr)
        if not line.strip():
            out.append("")
            continue
        m = re.match(r"^(\s*(?:[-*]|\d+\.|>|\|)?\s*)", line)
        indent = " " * len(m.group(1)) if m else ""
        out.extend(textwrap.wrap(line, width=width, subsequent_indent=indent, break_long_words=False,
                                 break_on_hyphens=False, replace_whitespace=False, drop_whitespace=True) or [""])
    return out


def relative(path: str, cwd: str, tr: Transforms | None = None) -> str:
    prefix = cwd.rstrip("/") + "/"
    if path.startswith(prefix):
        if tr:
            tr.paths_relativized += 1
        return path[len(prefix):]
    return scrub_home(path, tr)


def header(step: Step, cwd: str, tr: Transforms | None = None) -> str:
    name, inp = step.name, step.input
    if name == "Read":
        return f"Read {relative(str(inp.get('file_path', '')), cwd, tr)}"
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"{name} {relative(str(inp.get('file_path') or inp.get('notebook_path') or ''), cwd, tr)}"
    if name == "Bash":
        return f"Bash  {scrub_home(str(inp.get('description') or inp.get('command') or ''), tr)}"
    if name in ("Grep", "Glob"):
        return f"{name} {scrub_home(str(inp.get('pattern', '')), tr)}"
    if name == "ToolSearch":
        return f"ToolSearch {inp.get('query', '')}"
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        args = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in inp.items())
        return f"{server} · {tool}({scrub_home(args, tr)})"
    return f"{name} {scrub_home(json.dumps(inp, ensure_ascii=False), tr)}"


def build(take: Take, prompt: str, cwd: str, *, cols: int, rows: int, label: str, pacing: Pacing,
          hook_path: str | None = None, hook_fallback: str | None = None) -> Cast:
    """Lay out one take as a terminal session. `hook_path`: the Read whose hook result is always shown, even
    when the hook added nothing. `hook_fallback`: text to show there when the stream has no hook events."""
    c = Cast(cols=cols, rows=rows, pacing=pacing)
    tr = c.transforms
    width = cols - 4
    version = take.init.get("claude_code_version")
    head = " · ".join(x for x in [label, scrub_home(cwd, tr), f"claude -p (Claude Code {version})" if version else "claude -p"] if x)
    c.line(head, pre=OVERLAY1)
    c.line()
    c.pause(0.6)
    for i, pl in enumerate(textwrap.wrap(prompt, width=cols - 4)):
        c.stream(pl, pacing.prompt_cps, indent="> " if i == 0 else "  ", pre=CYAN)
    c.line()
    c.pause(1.0)

    steps = take.steps
    shown_hooks = {s.tool_index for s in steps if s.kind == "hook" and s.text}
    first_tool = True
    texts = [i for i, s in enumerate(steps) if s.kind == "text"]
    last_text = texts[-1] if texts else None
    for i, s in enumerate(steps):
        if s.kind == "tool":
            if first_tool:
                c.t = max(c.t, pacing.first_tool_at)
                first_tool = False
            h = header(s, cwd, tr)
            c.mark(f"tool:{s.name}")
            parts = textwrap.wrap(h, width=cols - 4, break_long_words=True) or [""]
            rel = relative(str(s.input.get("file_path", "")), cwd) if s.name == "Read" else None
            if rel:
                c.mark(f"read:{rel}")
            c.line(f"● {parts[0]}", pre=GREEN + BOLD)
            for more in parts[1:]:
                c.line(f"  {more}", pre=BOLD)
            is_hook_read = bool(hook_path and rel == hook_path and f"hook-shown:{hook_path}" not in c.marks)
            has_hook = i in shown_hooks
            c.pause(pacing.hook_delay if (is_hook_read or has_hook) else pacing.after_tool)
            if has_hook:
                continue  # the hook step itself follows in `steps`
            if is_hook_read:
                c.mark(f"hook-shown:{hook_path}")
                if not take.hook_events_seen and hook_fallback:
                    _hook_block(c, hook_fallback, width, pacing, tr)
                else:
                    c.mark("hook")
                    c.line(f"  {HOOK_NOTHING}", pre=PEACH)
                    c.pause(pacing.after_nothing)
        elif s.kind == "hook" and s.text:
            tool = steps[s.tool_index] if s.tool_index is not None else None
            if tool is not None and tool.name == "Read":
                c.mark(f"hook-shown:{relative(str(tool.input.get('file_path', '')), cwd)}")
            _hook_block(c, s.text, width, pacing, tr)
        elif s.kind == "text":
            c.line()
            if i == last_text:
                c.mark("reply")
            for tl in wrap_block(s.text, width, tr):
                c.stream(tl, pacing.reply_cps, indent="  ")
                c.reply_chars += len(tl)
            c.line()
            c.pause(pacing.after_text)
    c.pause(pacing.end_hold)
    c.mark("end")
    c.out("")
    return c


def _hook_block(c: Cast, text: str, width: int, pacing: Pacing, tr: Transforms) -> None:
    c.mark("hook")
    c.line(f"  {HOOK_HEADER}", pre=PEACH)
    for hl in wrap_block(text, width - 4, tr, markdown=False):
        c.line(f"  │ {hl.ljust(width - 4)}", pre=SURFACE0_BG)
    c.pause(pacing.after_hook)


def fit(take: Take, prompt: str, cwd: str, target: float, *, cols: int, rows: int, label: str, pacing: Pacing,
        hook_path: str | None = None, hook_fallback: str | None = None) -> Cast:
    """Choose the reply streaming speed so the scene lasts `target` seconds. Every pause keeps its length."""
    kw = dict(cols=cols, rows=rows, label=label, hook_path=hook_path, hook_fallback=hook_fallback)
    probe = build(take, prompt, cwd, pacing=_with(pacing, reply_cps=1e9), **kw)
    room = target - probe.t
    if room < 1.0:
        raise ValueError(f"target {target:.1f}s is shorter than the scene's fixed part ({probe.t:.1f}s); raise visual_seconds")
    cps = max(probe.reply_chars / room, 1.0) if probe.reply_chars else pacing.reply_cps
    return build(take, prompt, cwd, pacing=_with(pacing, reply_cps=cps), **kw)


def _with(p: Pacing, **changes) -> Pacing:
    return Pacing(**{**p.__dict__, **changes})


def write(c: Cast, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"version": 2, "width": c.cols, "height": c.rows, "env": {"TERM": "xterm-256color"}}) + "\n")
        for ev in c.events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def read_events(path: Path) -> tuple[dict, list[list]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0]), [json.loads(l) for l in lines[1:] if l.strip()]


def resolve_trigger(c: Cast, at: str) -> float | None:
    """When a caption fires: a named mark (`@read:<path>`, `@hook`, `@reply`, `@tool:<Name>`, `@end`) or the
    first line whose text (after leading spaces) starts with `at`."""
    if at.startswith("@"):
        return c.marks.get(at[1:])
    for t, text in c.line_times:
        if text.lstrip().startswith(at):
            return t
    return None
