"""truetake.yaml: load and validate the one file that drives every subcommand.

Relative paths resolve from the directory that holds truetake.yaml. See docs/ and
`truetake init` for an annotated example.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
KINDS = ("title", "slide", "scene")
ENGINES = ("kokoro", "gemini", "none")
ISOLATION = ("project", "bare")


class ConfigError(Exception):
    """truetake.yaml is missing something or says something truetake cannot honour."""


@dataclass
class Card:
    repo: Path
    commit: str
    file: str
    lines: list[tuple[int, int]]
    explain: str


@dataclass
class Caption:
    text: str
    at: str  # a prefix of a rendered cast line, or @read:<path>, @hook, @reply, @tool:<Name>


@dataclass
class Hook:
    after_read: str  # repository-relative path whose Read fires the hook shown in the video
    text_command: str | None = None  # fallback when the stream carries no hook_response


@dataclass
class Segment:
    id: str
    kind: str
    narration: str = ""  # title/slide: the whole narration; scene: spoken after the card phase
    card_narration: str = ""  # scene only: spoken while the code card is on screen
    title: str = ""
    subtitle: str = ""
    page: int | None = None
    cwd: Path | None = None
    prompt: str = ""
    takes: int = 3
    allowed_tools: list[str] = field(default_factory=list)
    mcp_config: Path | None = None
    visual_seconds: float = 30.0
    card_hold: float = 6.0
    timeout: int = 900
    clean_args: list[str] = field(default_factory=lambda: ["-fd"])
    hook: Hook | None = None
    card: Card | None = None
    captions: list[Caption] = field(default_factory=list)


@dataclass
class Terminal:
    cols: int = 140
    rows: int = 34
    font_family: str = "JetBrains Mono"
    font_size: int = 18
    line_height: float = 1.4


@dataclass
class Agent:
    command: str = "claude"
    isolation: str = "project"
    permission_mode: str = "acceptEdits"
    extra_args: list[str] = field(default_factory=list)


@dataclass
class Voice:
    engine: str = "kokoro"
    name: str = "af_heart"
    gemini_model: str | None = None
    loudness_lufs: float = -16.0


@dataclass
class Privacy:
    allow_emails: list[str] = field(default_factory=list)  # fnmatch patterns, e.g. "*@users.noreply.github.com"
    frame_every: float = 3.0


@dataclass
class Config:
    path: Path
    root: Path
    title: str
    output: Path
    work_dir: Path
    budget_seconds: float
    label: str
    width: int
    height: int
    fps: int
    terminal: Terminal
    agent: Agent
    voice: Voice
    privacy: Privacy
    slides_pdf: Path | None
    segments: list[Segment]
    fonts: dict[str, str]

    def scenes(self) -> list[Segment]:
        return [s for s in self.segments if s.kind == "scene"]

    def segment(self, seg_id: str) -> Segment:
        for s in self.segments:
            if s.id == seg_id:
                return s
        raise ConfigError(f"no segment with id {seg_id!r}")


def _path(root: Path, value: Any) -> Path | None:
    if value in (None, ""):
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (root / p).resolve()


def _need(d: dict, key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{where}: `{key}` is required")
    return d[key]


def _lines(value: Any, where: str) -> list[tuple[int, int]]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: `lines` must be a list of [first, last] ranges")
    out: list[tuple[int, int]] = []
    for r in value:
        if isinstance(r, int):
            r = [r, r]
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) for x in r)):
            raise ConfigError(f"{where}: each range in `lines` is [first, last], got {r!r}")
        a, b = r
        if a < 1 or b < a:
            raise ConfigError(f"{where}: bad line range {r!r}")
        if out and a <= out[-1][1]:
            raise ConfigError(f"{where}: line ranges must be in order and not overlap")
        out.append((a, b))
    return out


def _scene(root: Path, raw: dict, where: str, seg: Segment) -> None:
    seg.cwd = _path(root, _need(raw, "cwd", where))
    seg.prompt = str(_need(raw, "prompt", where))
    seg.takes = int(raw.get("takes", 3))
    if seg.takes < 1:
        raise ConfigError(f"{where}: `takes` must be at least 1")
    seg.allowed_tools = [str(t) for t in raw.get("allowed_tools") or []]
    seg.mcp_config = _path(root, raw.get("mcp_config"))
    seg.visual_seconds = float(raw.get("visual_seconds", 30))
    seg.card_hold = float(raw.get("card_hold", 6))
    seg.timeout = int(raw.get("timeout", 900))
    clean = raw.get("clean", "-fd")
    seg.clean_args = clean.split() if isinstance(clean, str) else [str(c) for c in clean]
    if hook := raw.get("hook"):
        seg.hook = Hook(after_read=str(_need(hook, "after_read", f"{where}.hook")), text_command=hook.get("text_command"))
    if card := raw.get("card"):
        w = f"{where}.card"
        commit = str(_need(card, "commit", w))
        if not SHA_RE.match(commit):
            raise ConfigError(f"{w}: `commit` must be a full 40-character SHA (pin it: git rev-parse HEAD), got {commit!r}")
        seg.card = Card(
            repo=_path(root, card.get("repo") or raw["cwd"]),
            commit=commit,
            file=str(_need(card, "file", w)),
            lines=_lines(card.get("lines"), w),
            explain=str(_need(card, "explain", w)),
        )
    for i, cap in enumerate(raw.get("captions") or []):
        w = f"{where}.captions[{i}]"
        seg.captions.append(Caption(text=str(_need(cap, "text", w)), at=str(_need(cap, "at", w))))
    narr = raw.get("narration") or ""
    if isinstance(narr, dict):
        seg.card_narration = str(narr.get("card") or "")
        seg.narration = str(narr.get("main") or "")
    else:
        seg.narration = str(narr)
    if seg.card_narration and not seg.card:
        raise ConfigError(f"{where}: `narration.card` needs a `card`")


def load(path: str | Path) -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"{path.name} not found (run `truetake init` to create one)")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("version", 1) != 1:
        raise ConfigError("only `version: 1` is supported")
    root = path.parent
    video = raw.get("video") or {}
    term = raw.get("terminal") or {}
    agent = raw.get("agent") or {}
    voice = raw.get("voice") or {}
    privacy = raw.get("privacy") or {}

    cfg = Config(
        path=path,
        root=root,
        title=str(raw.get("title") or root.name),
        output=_path(root, raw.get("output") or "demo.mp4"),
        work_dir=_path(root, raw.get("work_dir") or ".truetake"),
        budget_seconds=float(raw.get("budget_seconds") or 170),
        label=str(raw.get("label", "")),
        width=int(video.get("width", 1920)),
        height=int(video.get("height", 1080)),
        fps=int(video.get("fps", 24)),
        terminal=Terminal(
            cols=int(term.get("cols", 140)),
            rows=int(term.get("rows", 34)),
            font_family=str(term.get("font_family", "JetBrains Mono")),
            font_size=int(term.get("font_size", 18)),
            line_height=float(term.get("line_height", 1.4)),
        ),
        agent=Agent(
            command=str(agent.get("command", "claude")),
            isolation=str(agent.get("isolation", "project")),
            permission_mode=str(agent.get("permission_mode", "acceptEdits")),
            extra_args=[str(a) for a in agent.get("extra_args") or []],
        ),
        voice=Voice(
            engine=str(voice.get("engine", "kokoro")),
            name=str(voice.get("name", "af_heart")),
            gemini_model=voice.get("gemini_model"),
            loudness_lufs=float(voice.get("loudness_lufs", -16)),
        ),
        privacy=Privacy(
            allow_emails=[str(e) for e in privacy.get("allow_emails") or []],
            frame_every=float(privacy.get("frame_every", 3)),
        ),
        slides_pdf=_path(root, (raw.get("slides") or {}).get("pdf")),
        segments=[],
        fonts={k: str(v) for k, v in (raw.get("fonts") or {}).items()},
    )
    if cfg.agent.isolation not in ISOLATION:
        raise ConfigError(f"agent.isolation must be one of {ISOLATION}")
    if cfg.voice.engine not in ENGINES:
        raise ConfigError(f"voice.engine must be one of {ENGINES}")

    seen: set[str] = set()
    for i, s in enumerate(raw.get("segments") or []):
        where = f"segments[{i}]"
        seg_id = str(_need(s, "id", where))
        if not ID_RE.match(seg_id) or seg_id in seen:
            raise ConfigError(f"{where}: id {seg_id!r} must be unique, lowercase, [a-z0-9_-]")
        seen.add(seg_id)
        kind = str(_need(s, "kind", where))
        if kind not in KINDS:
            raise ConfigError(f"{where}: kind must be one of {KINDS}")
        seg = Segment(id=seg_id, kind=kind)
        if kind == "title":
            seg.title = str(_need(s, "title", where))
            seg.subtitle = str(s.get("subtitle") or "")
            seg.narration = str(s.get("narration") or "")
        elif kind == "slide":
            if not cfg.slides_pdf:
                raise ConfigError(f"{where}: a slide segment needs `slides.pdf`")
            seg.page = int(_need(s, "page", where))
            seg.narration = str(s.get("narration") or "")
        else:
            _scene(root, s, where, seg)
        cfg.segments.append(seg)
    if not cfg.segments:
        raise ConfigError("`segments` is empty")
    return cfg
