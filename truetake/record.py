"""Record fresh takes of a coding agent (Claude Code headless) and keep every one as evidence.

Each take runs the scene's prompt exactly as written, with nothing added to the system prompt. Edits are
allowed (`acceptEdits` by default), so if the agent declines to change something, that is its own choice,
not a missing permission. After each take the sandbox's git status and diff are saved, then the sandbox is
reset to its committed state so the next take starts from the same place.
"""
from __future__ import annotations

import datetime as dt
import os
import shlex
import subprocess
import time
from pathlib import Path

from . import state
from .config import Config, ConfigError, Segment
from .media import need


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in the sandbox failed: {proc.stderr.strip()}")
    return proc.stdout


def check_sandbox(cfg: Config, scene: Segment) -> None:
    cwd = scene.cwd
    if not cwd or not cwd.is_dir():
        raise ConfigError(f"scene {scene.id}: cwd {cwd} does not exist")
    if _git(cwd, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise ConfigError(f"scene {scene.id}: {cwd} is not a git work tree; commit the sandbox first")
    top = Path(_git(cwd, "rev-parse", "--show-toplevel").strip()).resolve()
    work = cfg.work_dir.resolve()
    if work == top or top in work.parents:
        raise ConfigError(f"scene {scene.id}: the work directory is inside the sandbox; the reset between takes "
                          "would delete the evidence. Put truetake.yaml and its work_dir outside the sandbox.")
    if _git(cwd, "status", "--porcelain").strip():
        raise ConfigError(f"scene {scene.id}: the sandbox has uncommitted changes. Commit them so every take "
                          "starts from a clean `git status` (see docs/pitfalls.md).")


def argv(cfg: Config, scene: Segment) -> list[str]:
    a = cfg.agent
    cmd = [a.command, "-p", scene.prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events",
           "--permission-mode", a.permission_mode, "--strict-mcp-config"]
    if scene.mcp_config:
        cmd += ["--mcp-config", str(scene.mcp_config)]
    if scene.allowed_tools:
        cmd += ["--allowedTools", ",".join(scene.allowed_tools)]
    if a.isolation == "bare":
        cmd += ["--bare"]
        settings = scene.cwd / ".claude" / "settings.json"
        if settings.exists():  # bare mode skips auto-discovery; hand it the sandbox's own settings explicitly
            cmd += ["--settings", str(settings)]
    else:
        cmd += ["--setting-sources", "project,local"]
    return cmd + a.extra_args


def record(cfg: Config, scene: Segment, takes: int | None = None, log=print) -> list[int]:
    need(cfg.agent.command, "Install Claude Code, or set agent.command in truetake.yaml.")
    check_sandbox(cfg, scene)
    n_takes = takes or scene.takes
    existing = state.recorded_takes(cfg, scene.id)
    start = (existing[-1] + 1) if existing else 1
    cmd = argv(cfg, scene)
    if cfg.agent.isolation == "bare" and not os.environ.get("ANTHROPIC_API_KEY"):
        log("  note: isolation is bare, which does not use a subscription login; set ANTHROPIC_API_KEY")
    made = []
    for n in range(start, start + n_takes):
        out = state.take_path(cfg, scene.id, n, ".jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        log(f"  take {n}: {scene.id} …")
        t0 = time.time()
        with out.open("wb") as fo, state.take_path(cfg, scene.id, n, ".stderr").open("wb") as fe, \
                open(os.devnull, "rb") as fi:  # stdin closed: claude -p would otherwise wait on it
            try:
                proc = subprocess.run(cmd, cwd=scene.cwd, stdin=fi, stdout=fo, stderr=fe, timeout=scene.timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        elapsed = time.time() - t0
        status = _git(scene.cwd, "status", "--short")
        stat = _git(scene.cwd, "diff", "--stat")
        diff = _git(scene.cwd, "diff")
        state.take_path(cfg, scene.id, n, ".git.txt").write_text(
            f"== git status --short\n{status}\n== git diff --stat\n{stat}\n== git diff\n{diff}", encoding="utf-8")
        changed = [l[3:] for l in status.splitlines() if l.strip()]
        state.write_json(state.take_path(cfg, scene.id, n, ".meta.json"), {
            "scene": scene.id, "take": n, "prompt": scene.prompt, "argv": cmd, "cwd": str(scene.cwd),
            "started": dt.datetime.fromtimestamp(t0, dt.timezone.utc).isoformat(timespec="seconds"),
            "seconds": round(elapsed, 1), "exit_code": code, "files_changed": changed,
            "isolation": cfg.agent.isolation,
            "user_memory_file_present": (Path.home() / ".claude" / "CLAUDE.md").exists(),
        })
        # Reset to the committed state so the next take starts where this one did.
        _git(scene.cwd, "checkout", "--", ".")
        _git(scene.cwd, "clean", *scene.clean_args)
        log(f"  take {n}: exit {code}, {elapsed:.0f}s, files changed: {', '.join(changed) or 'none'}")
        made.append(n)
    return made


def command_line(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)
