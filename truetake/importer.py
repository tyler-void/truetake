"""Import an existing `claude -p --output-format stream-json --verbose` run as a take.

Use it when the video must show a run that already happened, e.g. a pre-registered run of a benchmark, instead of
a fresh recording. The take keeps its evidence the same way a recorded one does: the stream(s) verbatim, with each
file's SHA-256, where it came from, and the prompts it was given. The report labels it "imported".

A resumed session (`claude -p … --resume <id>`) is several streams; pass them in order and list the follow-up
prompts in the scene's `followups`. The stream does not carry a PreToolUse hook's substituted command (its
`hook_response` has an empty stdout), and its per-block usage is provisional; `--transcript` reads the session
transcript and keeps the rewrites (keyed by tool_use id) and each request's final usage (keyed by message id) in
`take-NN.sidecar.json`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import state
from .config import Config, ConfigError, Segment


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def transcript_usage(path: Path) -> dict:
    """Final usage per API request (message id), from the session transcript. stream-json carries a provisional
    usage on each content block (output_tokens counted before the message ends), so costs computed from the stream
    alone come out low; the transcript has the final numbers."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = e.get("message") or {}
        if e.get("type") == "assistant" and m.get("id") and m.get("usage") and not e.get("isSidechain"):
            out[m["id"]] = m["usage"]
    return out


def transcript_rewrites(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        a = e.get("attachment") or {}
        if a.get("hookEvent") != "PreToolUse" or not a.get("toolUseID"):
            continue
        try:
            spec = (json.loads(a.get("stdout") or "{}") or {}).get("hookSpecificOutput") or {}
        except json.JSONDecodeError:
            continue
        cmd = (spec.get("updatedInput") or {}).get("command")
        if cmd:
            out[a["toolUseID"]] = {"command": cmd, "reason": spec.get("permissionDecisionReason") or "",
                                   "hook": a.get("hookName") or "", "hook_command": a.get("command") or ""}
    return out


def import_take(cfg: Config, scene: Segment, streams: list[Path], source: str, transcript: Path | None = None,
                git_txt: Path | None = None) -> int:
    if not streams:
        raise ConfigError("import: give at least one stream file")
    for p in streams:
        if not p.is_file():
            raise ConfigError(f"import: {p} does not exist")
    lines, inits, cwd, model, first_ts = [], 0, None, None, None
    for p in streams:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("type") == "system" and e.get("subtype") == "init":
                inits += 1
                cwd = cwd or e.get("cwd")
                model = model or e.get("model")
            first_ts = first_ts or e.get("timestamp")
            lines.append(line)
    if inits != len(scene.prompts):
        raise ConfigError(f"import: the streams hold {inits} invocation(s) but scene {scene.id} lists {len(scene.prompts)} "
                          "prompt(s) (prompt + followups); they must match one to one")
    if cwd and scene.cwd and str(scene.cwd) != cwd:
        raise ConfigError(f"import: the run's cwd was {cwd}; set the scene's cwd to exactly that path (it is used to make "
                          "tool paths relative) and display_cwd to what the video should show")
    existing = state.recorded_takes(cfg, scene.id)
    n = (existing[-1] + 1) if existing else 1
    out = state.take_path(cfg, scene.id, n, ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rewrites = transcript_rewrites(transcript) if transcript else {}
    usage = transcript_usage(transcript) if transcript else {}
    if transcript:
        state.write_json(out.with_suffix(".sidecar.json"), {"pre_tool_use": rewrites, "usage": usage, "from": "session transcript",
                                                            "transcript_sha256": _sha(transcript)})
    state.take_path(cfg, scene.id, n, ".stderr").write_text("", encoding="utf-8")
    state.take_path(cfg, scene.id, n, ".git.txt").write_text(
        git_txt.read_text(encoding="utf-8") if git_txt else "(imported take: no git state recorded by truetake)\n",
        encoding="utf-8")
    result = next((json.loads(l) for l in reversed(lines) if '"type":"result"' in l or '"type": "result"' in l), {})
    state.write_json(state.take_path(cfg, scene.id, n, ".meta.json"), {
        "scene": scene.id, "take": n, "prompt": scene.prompt, "prompts": scene.prompts, "argv": None, "cwd": cwd,
        "started": first_ts, "seconds": round((result.get("duration_ms") or 0) / 1000, 1) or None,
        "exit_code": 0 if result and not result.get("is_error") else "unknown", "files_changed": [],
        "isolation": None, "user_memory_file_present": None,
        "imported": {"source": source, "model": model, "invocations": inits,
                     "streams": [{"file": p.name, "sha256": _sha(p)} for p in streams],
                     "transcript": {"file": transcript.name, "sha256": _sha(transcript), "pre_tool_use_rewrites": len(rewrites),
                                    "final_usage_requests": len(usage)}
                     if transcript else None,
                     "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
    })
    return n
