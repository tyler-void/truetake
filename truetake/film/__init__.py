"""The film set: a real run on a stage, with Neovide-style motion and a camera, drawn frame by frame.

    film:
      output: film.mp4                 # never the assembled `output`
      duration: 20                     # seconds of film
      fps: 60
      theme: marginalia                # or mocha, or {base: marginalia, accent: "#2E567A", ...}
      fonts: {serif: fonts/Literata.ttf, sans: fonts/Lexend.ttf}   # TTF/OTF; mono defaults to terminal.font_family
      pane:
        source: codex                  # a Codex transcript (has timestamps: the film shows a clock)
        path: runs/transcript-01.ndjson
        # source: scene, scene: hello  # the chosen take of a recorded scene (no timestamps: no clock)
        title: ORCHESTRATOR
        subtitle: Codex · practice match · Oct 29
        cols: 86
        rows: 21
      clock: {utc_offset: 7}           # hours, for the clock's wall time
      camera: [{at: 5, zoom: 1.3}, {at: 16, zoom: 1.0}]   # or `auto`: close on the working pane while reading
      pacing: {message_cps: 24}        # any field of truetake.film.script.Pacing
      score: score.wav                 # optional: truetake-score's render of .truetake/film/cues.json
      loudness: -21                    # LUFS of the score in the film
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import fields
from pathlib import Path

from ..config import Config, ConfigError
from ..scan import Scanner
from . import script as scriptmod
from . import theme as thememod
from .render import Camera, FilmSpec, PaneSpec, render
from .script import FilmError, Pacing

KEYS = {"output", "duration", "fps", "theme", "fonts", "pane", "panes", "cols", "rows", "clock", "camera", "beats",
        "pacing", "score", "loudness"}
PANE_KEYS = {"id", "source", "path", "scene", "title", "subtitle", "cols", "rows"}
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _path(cfg: Config, p) -> Path:
    q = Path(str(p)).expanduser()
    return q if q.is_absolute() else (cfg.root / q).resolve()


def _panes(raw: dict) -> list[dict]:
    if raw.get("pane") and raw.get("panes"):
        raise ConfigError("film: give `pane` (one) or `panes` (several), not both")
    panes = raw.get("panes") or ([raw["pane"]] if raw.get("pane") else [])
    if not panes:
        raise ConfigError("film: `panes` (or `pane`) is required")
    ids = []
    for i, p in enumerate(panes):
        if not isinstance(p, dict) or set(p) - PANE_KEYS:
            raise ConfigError(f"film.panes[{i}]: unknown key(s) {sorted(set(p) - PANE_KEYS) if isinstance(p, dict) else p}")
        ids.append(str(p.get("id") or f"pane{i + 1}"))
    if len(set(ids)) != len(ids):
        raise ConfigError("film.panes: ids must be unique")
    return [dict(p, id=pid) for p, pid in zip(panes, ids)]


def spec(cfg: Config) -> tuple[FilmSpec, list[dict], Pacing]:
    raw = cfg.film
    if not raw:
        raise ConfigError("no `film:` section in truetake.yaml")
    if set(raw) - KEYS:
        raise ConfigError(f"film: unknown key(s) {sorted(set(raw) - KEYS)}")
    if "output" not in raw:
        raise ConfigError("film.output is required")
    panes = _panes(raw)
    out = _path(cfg, raw["output"])
    if out == cfg.output:
        raise ConfigError("film.output must differ from `output`: the film never overwrites the assembled video")
    ids = {p["id"] for p in panes}
    cams = []
    auto = raw.get("camera") == "auto"
    for i, c in enumerate([] if auto else (raw.get("camera") or [])):
        try:
            cams.append(Camera(at=float(c["at"]), zoom=float(c["zoom"]), pane=str(c.get("pane") or "")))
        except (KeyError, TypeError, ValueError):
            raise ConfigError(f"film.camera[{i}]: {{at: seconds, zoom: factor, pane: id}}")
        if not 1.0 <= cams[-1].zoom <= 2.0:
            raise ConfigError(f"film.camera[{i}]: zoom is between 1.0 and 2.0")
        if cams[-1].pane and cams[-1].pane not in ids:
            raise ConfigError(f"film.camera[{i}]: no pane {cams[-1].pane!r}")
    if [c.at for c in cams] != sorted(c.at for c in cams):
        raise ConfigError("film.camera: moves must be in time order")
    pace = raw.get("pacing") or {}
    known = {f.name for f in fields(Pacing)}
    if set(pace) - known:
        raise ConfigError(f"film.pacing: unknown key(s) {sorted(set(pace) - known)}")
    pacing = Pacing(**{k: (int(v) if k == "output_rows" else float(v)) for k, v in pace.items()})
    clock = raw.get("clock") or {}
    duration = raw.get("duration", 20)
    if duration != "auto":
        duration = float(duration)
        if duration <= 0:
            raise ConfigError("film.duration must be positive, or `auto`")
    first = panes[0]
    fs = FilmSpec(output=out, width=cfg.width, height=cfg.height, fps=int(raw.get("fps", 60)),
                  duration=duration if duration != "auto" else 0.0,
                  cols=int(raw.get("cols", first.get("cols", 86))), rows=int(raw.get("rows", first.get("rows", 21))),
                  panes=[PaneSpec(p["id"], str(p.get("title", "")), str(p.get("subtitle", ""))) for p in panes],
                  camera=cams, utc_offset=float(clock["utc_offset"]) if "utc_offset" in clock else None,
                  score=_path(cfg, raw["score"]) if raw.get("score") else None,
                  loudness=float(raw.get("loudness", -21)), camera_auto=auto)
    return fs, panes, pacing


def source(cfg: Config, pane: dict) -> scriptmod.Source:
    kind = pane.get("source")
    if kind == "codex":
        p = _path(cfg, pane.get("path") or "")
        if not p.is_file():
            raise ConfigError(f"film pane {pane['id']}: {p} not found")
        return scriptmod.codex_transcript(p)
    if kind == "scene":
        from ..render import selected_take

        seg = cfg.segment(str(pane.get("scene") or ""))
        if seg.kind != "scene":
            raise ConfigError(f"film pane {pane['id']}: {seg.id!r} is not a scene")
        _, take, _ = selected_take(cfg, seg)
        return scriptmod.claude_take(take, seg.prompt, str(seg.cwd))
    raise ConfigError(f"film pane {pane['id']}: source is `codex` (with `path`) or `scene` (with `scene`)")


def beats(raw, t_zero: dt.datetime | None, utc_offset: float | None) -> list[tuple[float, float]]:
    """`film.beats`: real-time windows played at reading pace. Each end is seconds since the first event, or
    a wall-clock time `HH:MM[:SS]` (needs `clock.utc_offset`)."""
    def when(v, where: str) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        m = CLOCK_RE.match(str(v))
        if not m:
            raise ConfigError(f"{where}: seconds, or a wall-clock time HH:MM[:SS]")
        if t_zero is None or utc_offset is None:
            raise ConfigError(f"{where}: a wall-clock time needs timestamped sources and `clock.utc_offset`")
        local = t_zero + dt.timedelta(hours=utc_offset)
        at = local.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3] or 0), microsecond=0)
        return (at - local).total_seconds()

    out = []
    for i, b in enumerate(raw or []):
        if not isinstance(b, dict) or "from" not in b or "to" not in b:
            raise ConfigError(f"film.beats[{i}]: {{from: …, to: …}}")
        a, z = when(b["from"], f"film.beats[{i}].from"), when(b["to"], f"film.beats[{i}].to")
        if z <= a:
            raise ConfigError(f"film.beats[{i}]: `to` must come after `from`")
        out.append((a, z))
    return out


def make(cfg: Config, preview: bool = False, log=print) -> dict:
    fs, panes, pacing = spec(cfg)
    fonts_cfg = {k: str(_path(cfg, v)) for k, v in (cfg.film.get("fonts") or {}).items()}
    theme = thememod.load(cfg.film.get("theme"), fonts_cfg, cfg.terminal.font_family)
    scanner = Scanner.from_env(cfg.privacy.allow_emails)
    sources = [(p["id"], source(cfg, p)) for p in panes]
    t_zero = min((s.started for _, s in sources if s.started), default=None)
    fs.beats = beats(cfg.film.get("beats"), t_zero, fs.utc_offset)
    auto = fs.duration == 0.0
    sc = scriptmod.build_set(sources, fs.cols, pacing, scanner, until=None if auto else fs.duration, beats=fs.beats)
    if auto:
        fs.duration = round(sc.end + 2.0, 2)
    times = [round(fs.duration * q, 2) for q in (0.1, 0.35, 0.6, 0.9)] if preview else None
    return render(fs, sc, theme, pacing, cfg.work_dir, preview=times, log=log)


__all__ = ["FilmError", "make", "spec"]
