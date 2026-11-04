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
      camera: [{at: 5, zoom: 1.3}, {at: 16, zoom: 1.0}]
      pacing: {message_cps: 24}        # any field of truetake.film.script.Pacing
      score: score.wav                 # optional: truetake-score's render of .truetake/film/cues.json
      loudness: -21                    # LUFS of the score in the film
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from ..config import Config, ConfigError
from ..scan import Scanner
from . import script as scriptmod
from . import theme as thememod
from .render import Camera, FilmSpec, render
from .script import FilmError, Pacing

KEYS = {"output", "duration", "fps", "theme", "fonts", "pane", "clock", "camera", "pacing", "score", "loudness"}
PANE_KEYS = {"source", "path", "scene", "title", "subtitle", "cols", "rows"}


def _path(cfg: Config, p) -> Path:
    q = Path(str(p)).expanduser()
    return q if q.is_absolute() else (cfg.root / q).resolve()


def spec(cfg: Config) -> tuple[FilmSpec, dict, Pacing]:
    raw = cfg.film
    if not raw:
        raise ConfigError("no `film:` section in truetake.yaml")
    if set(raw) - KEYS:
        raise ConfigError(f"film: unknown key(s) {sorted(set(raw) - KEYS)}")
    pane = raw.get("pane") or {}
    if set(pane) - PANE_KEYS:
        raise ConfigError(f"film.pane: unknown key(s) {sorted(set(pane) - PANE_KEYS)}")
    if "output" not in raw:
        raise ConfigError("film.output is required")
    out = _path(cfg, raw["output"])
    if out == cfg.output:
        raise ConfigError("film.output must differ from `output`: the film never overwrites the assembled video")
    cams = []
    for i, c in enumerate(raw.get("camera") or []):
        try:
            cams.append(Camera(at=float(c["at"]), zoom=float(c["zoom"])))
        except (KeyError, TypeError, ValueError):
            raise ConfigError(f"film.camera[{i}]: {{at: seconds, zoom: factor}}")
        if not 1.0 <= cams[-1].zoom <= 2.0:
            raise ConfigError(f"film.camera[{i}]: zoom is between 1.0 and 2.0")
    if [c.at for c in cams] != sorted(c.at for c in cams):
        raise ConfigError("film.camera: moves must be in time order")
    pace = raw.get("pacing") or {}
    known = {f.name for f in fields(Pacing)}
    if set(pace) - known:
        raise ConfigError(f"film.pacing: unknown key(s) {sorted(set(pace) - known)}")
    pacing = Pacing(**{k: (int(v) if k == "output_rows" else float(v)) for k, v in pace.items()})
    clock = raw.get("clock") or {}
    fs = FilmSpec(output=out, width=cfg.width, height=cfg.height, fps=int(raw.get("fps", 60)),
                  duration=float(raw.get("duration", 20)), cols=int(pane.get("cols", 86)), rows=int(pane.get("rows", 21)),
                  title=str(pane.get("title", "")), subtitle=str(pane.get("subtitle", "")), camera=cams,
                  utc_offset=float(clock["utc_offset"]) if "utc_offset" in clock else None,
                  score=_path(cfg, raw["score"]) if raw.get("score") else None,
                  loudness=float(raw.get("loudness", -21)))
    if fs.duration <= 0:
        raise ConfigError("film.duration must be positive")
    return fs, pane, pacing


def source(cfg: Config, pane: dict) -> scriptmod.Source:
    kind = pane.get("source")
    if kind == "codex":
        p = _path(cfg, pane.get("path") or "")
        if not p.is_file():
            raise ConfigError(f"film.pane.path: {p} not found")
        return scriptmod.codex_transcript(p)
    if kind == "scene":
        from ..render import selected_take

        seg = cfg.segment(str(pane.get("scene") or ""))
        if seg.kind != "scene":
            raise ConfigError(f"film.pane.scene: no scene {pane.get('scene')!r}")
        _, take, _ = selected_take(cfg, seg)
        return scriptmod.claude_take(take, seg.prompt, str(seg.cwd))
    raise ConfigError("film.pane.source: `codex` (with `path`) or `scene` (with `scene`)")


def make(cfg: Config, preview: bool = False, log=print) -> dict:
    fs, pane, pacing = spec(cfg)
    fonts_cfg = {k: str(_path(cfg, v)) for k, v in (cfg.film.get("fonts") or {}).items()}
    theme = thememod.load(cfg.film.get("theme"), fonts_cfg, cfg.terminal.font_family)
    scanner = Scanner.from_env(cfg.privacy.allow_emails)
    sc = scriptmod.build(source(cfg, pane), fs.cols, pacing, scanner, until=fs.duration)
    times = [round(fs.duration * q, 2) for q in (0.1, 0.35, 0.6, 0.9)] if preview else None
    return render(fs, sc, theme, pacing, cfg.work_dir, preview=times, log=log)


__all__ = ["FilmError", "make", "spec"]
