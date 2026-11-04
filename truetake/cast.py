"""Turn a recorded take into an asciicast v2 file.

Honesty: every word the agent wrote appears verbatim, in order. The only changes are:
  * pacing: typing and streaming speeds and the pauses between beats (all in `Pacing`);
  * Markdown emphasis and heading markers dropped from agent text (`**`, backticks, leading `#`);
  * paths in tool-call headers made relative to the sandbox;
  * the home directory written as `~`, anywhere.
Each change is counted in `Transforms` and listed in the report. Word-wrapping to the terminal width is
layout, not a change. Tool calls become one-line headers; the hook's added context is shown verbatim as a
highlighted block right after the tool call it followed.

Opt-in annotations (`Annotate`) add measured facts from the same stream, each on its own dim line marked `⎿`/`·`
so it cannot be mistaken for agent text: the command a PreToolUse hook substituted, how many characters of tool
output the model received, and the context size and running list-price cost of each API request. Follow-up
prompts of a resumed session (`--resume`) are shown like the first prompt.
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
REWRITE_HEADER = "⎿ PreToolUse hook ran instead:"
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
    prompt_max_seconds: float | None = None  # type a long prompt faster so it takes at most this long


@dataclass
class Annotate:
    rewrites: bool = False  # show the command a PreToolUse hook substituted
    sizes: bool = False  # show how many characters of tool output the model received
    usage: bool = False  # one line per API request: context size and running cost
    usage_bar: bool = False  # a fixed status bar above the terminal (render draws it from Cast.status)
    prices: dict = field(default_factory=dict)  # $/MTok: input, output, cache_write_5m, cache_write_1h, cache_read
    price_label: str = "list price"


@dataclass
class Transforms:
    markdown_markers_dropped: int = 0
    paths_relativized: int = 0
    home_replaced: int = 0
    cwd_display: str = ""  # the working directory shown in the header, when the scene sets display_cwd
    cwd_in_commands: int = 0  # the real working directory inside a shell command, written as display_cwd


@dataclass
class Cast:
    cols: int
    rows: int
    t: float = 0.0
    events: list[list] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)
    line_times: list[tuple[float, str]] = field(default_factory=list)  # (first visible character, plain text)
    reply_chars: int = 0
    status: list = field(default_factory=list)  # (time, text): the usage bar's value from that moment on
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
        # A token longer than the line (a long path in a narrow pane) is split across lines: layout, not a change.
        out.extend(textwrap.wrap(line, width=width, subsequent_indent=indent, break_long_words=True,
                                 break_on_hyphens=False, replace_whitespace=False, drop_whitespace=True) or [""])
    return out


def relative(path: str, cwd: str, tr: Transforms | None = None) -> str:
    prefix = cwd.rstrip("/") + "/"
    if path.startswith(prefix):
        if tr:
            tr.paths_relativized += 1
        return path[len(prefix):]
    return scrub_home(path, tr)


def show_cwd(text: str, cwd: str, display: str, tr: Transforms | None = None) -> str:
    """The real working directory inside a command, written as the scene's display_cwd (a counted transform)."""
    real = cwd.rstrip("/")
    if display and real and real in text:
        if tr:
            tr.cwd_in_commands += text.count(real)
        text = text.replace(real, display.rstrip("/") or ".")
    return text


def header(step: Step, cwd: str, tr: Transforms | None = None, display: str = "") -> str:
    name, inp = step.name, step.input
    if name == "Read":
        return f"Read {relative(str(inp.get('file_path', '')), cwd, tr)}"
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"{name} {relative(str(inp.get('file_path') or inp.get('notebook_path') or ''), cwd, tr)}"
    if name == "Bash":
        return f"Bash  {scrub_home(show_cwd(str(inp.get('description') or inp.get('command') or ''), cwd, display, tr), tr)}"
    if name in ("Grep", "Glob"):
        return f"{name} {scrub_home(str(inp.get('pattern', '')), tr)}"
    if name == "ToolSearch":
        return f"ToolSearch {inp.get('query', '')}"
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        args = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in inp.items())
        return f"{server} · {tool}({scrub_home(args, tr)})"
    return f"{name} {scrub_home(json.dumps(inp, ensure_ascii=False), tr)}"


def request_cost(u: dict, prices: dict) -> float | None:
    """List-price cost of one API request from its usage block; None without prices."""
    if not prices:
        return None
    cc = u.get("cache_creation") or {}
    w1 = cc.get("ephemeral_1h_input_tokens", 0)
    w5 = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - w1)
    return (u.get("input_tokens", 0) * prices.get("input", 0) + u.get("output_tokens", 0) * prices.get("output", 0)
            + w5 * prices.get("cache_write_5m", 0) + w1 * prices.get("cache_write_1h", 0)
            + u.get("cache_read_input_tokens", 0) * prices.get("cache_read", 0)) / 1e6


def build(take: Take, prompt: str, cwd: str, *, cols: int, rows: int, label: str, pacing: Pacing,
          hook_path: str | None = None, hook_fallback: str | None = None, annotate: Annotate | None = None,
          display_cwd: str = "") -> Cast:
    """Lay out one take as a terminal session. `hook_path`: the Read whose hook result is always shown, even
    when the hook added nothing. `hook_fallback`: text to show there when the stream has no hook events.
    `display_cwd`: shown in the header instead of the real working directory (counted as a transform)."""
    c = Cast(cols=cols, rows=rows, pacing=pacing)
    tr = c.transforms
    an = annotate or Annotate()
    width = cols - 4
    version = take.init.get("claude_code_version")
    shown_cwd = scrub_home(cwd, tr)
    if display_cwd:
        tr.cwd_display = display_cwd
        shown_cwd = display_cwd
    head = " · ".join(x for x in [label, shown_cwd, f"claude -p (Claude Code {version})" if version else "claude -p"] if x)
    c.line(head, pre=OVERLAY1)
    c.line()
    c.pause(0.6)

    def prompt_cps(text: str) -> float:
        if pacing.prompt_max_seconds:
            return max(pacing.prompt_cps, len(text) / pacing.prompt_max_seconds)
        return pacing.prompt_cps

    for i, pl in enumerate(textwrap.wrap(prompt, width=cols - 4)):
        c.stream(pl, prompt_cps(prompt), indent="> " if i == 0 else "  ", pre=CYAN)
    c.line()
    c.pause(1.0)

    steps = take.steps
    shown_hooks = {s.tool_index for s in steps if s.kind == "hook" and s.text}
    first_tool = True
    spent = 0.0
    texts = [i for i, s in enumerate(steps) if s.kind == "text"]
    last_text = texts[-1] if texts else None
    for i, s in enumerate(steps):
        if s.kind == "tool":
            if first_tool:
                c.t = max(c.t, pacing.first_tool_at)
                first_tool = False
            h = header(s, cwd, tr, display_cwd)
            c.mark(f"tool:{s.name}")
            parts = textwrap.wrap(h, width=cols - 4, break_long_words=True) or [""]
            rel = relative(str(s.input.get("file_path", "")), cwd) if s.name == "Read" else None
            if rel:
                c.mark(f"read:{rel}")
            c.line(f"● {parts[0]}", pre=GREEN + BOLD)
            for more in parts[1:]:
                c.line(f"  {more}", pre=BOLD)
            if an.rewrites and s.rewrite:
                c.mark("rewrite")
                rw = textwrap.wrap(f"{REWRITE_HEADER} {scrub_home(show_cwd(s.rewrite, cwd, display_cwd, tr), tr)}",
                                   width=cols - 4, break_long_words=True) or [""]
                c.line(f"  {rw[0]}", pre=PEACH)
                for more in rw[1:]:
                    c.line(f"    {more}", pre=PEACH)
            if an.sizes and s.result_chars is not None:
                c.mark(f"size:{len(c.marks)}")
                c.line(f"  ⎿ the model received {s.result_chars:,} characters" + (" (error)" if s.result_error else ""),
                       pre=OVERLAY1)
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
        elif s.kind == "prompt":
            c.line()
            c.mark(f"prompt:{s.name}")
            for j, pl in enumerate(textwrap.wrap(s.text, width=cols - 4)):
                c.stream(pl, prompt_cps(s.text), indent="> " if j == 0 else "  ", pre=CYAN)
            c.line()
            c.pause(1.0)
        elif s.kind == "usage" and (an.usage or an.usage_bar):
            u = s.usage
            ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            cost = request_cost(u, an.prices)
            if cost is not None:
                spent += cost
            c.mark(f"request:{u.get('request')}")
            if an.usage:
                line = f"· request {u.get('request')} · context {ctx:,} tokens"
                if cost is not None:
                    line += f" · ${spent:.3f} so far ({an.price_label})"
                c.line(f"  {line}", pre=OVERLAY1)
            if an.usage_bar:
                bar = f"{u.get('request')} request{'s' if u.get('request') != 1 else ''} · context {ctx:,} tokens"
                if cost is not None:
                    bar += f" · ${spent:.3f}"
                c.status.append((round(c.t, 3), bar))
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
        hook_path: str | None = None, hook_fallback: str | None = None, annotate: Annotate | None = None,
        display_cwd: str = "") -> Cast:
    """Choose the reply streaming speed so the scene lasts `target` seconds. Every pause keeps its length."""
    kw = dict(cols=cols, rows=rows, label=label, hook_path=hook_path, hook_fallback=hook_fallback, annotate=annotate,
              display_cwd=display_cwd)
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
