"""Parse a recorded take: the newline-delimited JSON that `claude -p --output-format stream-json --verbose`
writes. Only what the video and the report need is kept; the raw file stays untouched as evidence."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Step:
    kind: str  # "tool" | "text" | "hook"
    name: str = ""  # tool name, or hook name such as "PostToolUse:Read"
    input: dict = field(default_factory=dict)
    text: str = ""  # assistant text, or the hook's additionalContext ("" when the hook added nothing)
    tool_index: int | None = None  # for a hook: index (in steps) of the tool call it followed


@dataclass
class Take:
    path: Path
    steps: list[Step]
    init: dict
    result: dict
    hook_events_seen: bool

    @property
    def tools(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "tool"]

    @property
    def texts(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "text"]

    @property
    def final_text(self) -> str:
        return self.texts[-1].text if self.texts else ""


def _additional_context(stdout: str) -> str:
    stdout = (stdout or "").strip()
    if not stdout:
        return ""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout  # plain stdout from a hook is also added as context
    if isinstance(data, dict):
        spec = data.get("hookSpecificOutput") or {}
        return str(spec.get("additionalContext") or "")
    return ""


def parse(path: Path) -> Take:
    steps: list[Step] = []
    init: dict = {}
    result: dict = {}
    hook_events = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        t, st = e.get("type"), e.get("subtype")
        if t == "system" and st == "init":
            init = e
        elif t == "system" and st in ("hook_started", "hook_response"):
            hook_events = True
            if st == "hook_response" and str(e.get("hook_event", "")).startswith("PostToolUse"):
                tool_name = str(e.get("hook_name", "")).partition(":")[2]
                idx = next((i for i in range(len(steps) - 1, -1, -1)
                            if steps[i].kind == "tool" and (not tool_name or steps[i].name == tool_name)), None)
                steps.append(Step(kind="hook", name=str(e.get("hook_name", "")),
                                  text=_additional_context(e.get("stdout") or e.get("output") or ""), tool_index=idx))
        elif t == "assistant":
            for part in (e.get("message") or {}).get("content") or []:
                if part.get("type") == "tool_use":
                    steps.append(Step(kind="tool", name=part.get("name", ""), input=part.get("input") or {}))
                elif part.get("type") == "text" and part.get("text", "").strip():
                    steps.append(Step(kind="text", text=part["text"]))
        elif t == "result":
            result = e
    return Take(path=path, steps=steps, init=init, result=result, hook_events_seen=hook_events)


def summary(take: Take) -> dict:
    """What the report and `truetake select --list` show about one take."""
    r = take.result
    return {
        "tool_calls": [s.name for s in take.tools],
        "hooks_with_context": sum(1 for s in take.steps if s.kind == "hook" and s.text),
        "reply_chars": sum(len(s.text) for s in take.texts),
        "final_text_start": take.final_text.strip().splitlines()[0][:160] if take.final_text.strip() else "",
        "result": r.get("subtype", "missing"),
        "is_error": bool(r.get("is_error")),
        "num_turns": r.get("num_turns"),
        "duration_ms": r.get("duration_ms"),
        "permission_denials": len(r.get("permission_denials") or []),
        "model": take.init.get("model"),
        "claude_code_version": take.init.get("claude_code_version"),
        "permission_mode": take.init.get("permissionMode"),
        "mcp_servers": [m.get("name") for m in take.init.get("mcp_servers") or []],
        "plugins": [p.get("source") or p.get("name") for p in take.init.get("plugins") or []],
    }
