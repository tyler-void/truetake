"""Where everything lives inside the work directory (default `.truetake/` next to truetake.yaml).

  takes/<scene>/take-<n>.jsonl        raw `claude -p --output-format stream-json` stream (evidence)
  takes/<scene>/take-<n>.stderr       its stderr
  takes/<scene>/take-<n>.git.txt      git status --short, diff --stat and diff after the take
  takes/<scene>/take-<n>.meta.json    argv, timings, exit code, prompt
  takes/<scene>/selected.json         the chosen take and why
  tts/<voice>/<segment>[.card].wav    narration clips, each with a .txt naming the text it speaks
  render/...                          casts, cards, captions, per-segment videos, schedules
  assemble/...                        the audio track, joins, loudness
  check/...                           frames, contact sheets, check.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Config


def takes_dir(cfg: Config, scene: str) -> Path:
    return cfg.work_dir / "takes" / scene


def take_path(cfg: Config, scene: str, n: int, suffix: str) -> Path:
    return takes_dir(cfg, scene) / f"take-{n}{suffix}"


def recorded_takes(cfg: Config, scene: str) -> list[int]:
    d = takes_dir(cfg, scene)
    if not d.exists():
        return []
    out = []
    for p in d.glob("take-*.jsonl"):
        try:
            out.append(int(p.stem.split("-", 1)[1]))
        except ValueError:
            pass
    return sorted(out)


def selection(cfg: Config, scene: str) -> dict | None:
    p = takes_dir(cfg, scene) / "selected.json"
    return read_json(p) if p.exists() else None


def tts_dir(cfg: Config) -> Path:
    return cfg.work_dir / "tts" / f"{cfg.voice.engine}-{cfg.voice.name}"


def render_dir(cfg: Config) -> Path:
    return cfg.work_dir / "render"


def assemble_dir(cfg: Config) -> Path:
    return cfg.work_dir / "assemble"


def check_dir(cfg: Config) -> Path:
    return cfg.work_dir / "check"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
