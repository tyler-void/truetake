"""Parse a recorded take: the newline-delimited JSON that `claude -p --output-format stream-json --verbose`
writes. Only what the video and the report need is kept; the raw file stays untouched as evidence."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Step:
    kind: str  # "tool" | "text" | "hook" | "prompt" (a follow-up prompt, --resume) | "usage" (one API request)
    name: str = ""  # tool name, or hook name such as "PostToolUse:Read"
    input: dict = field(default_factory=dict)
    text: str = ""  # assistant text, the hook's additionalContext, or the follow-up prompt
    tool_index: int | None = None  # for a hook: index (in steps) of the tool call it followed
    tool_id: str = ""  # tool_use id, links a tool call to its result and to PreToolUse rewrites
    result_chars: int | None = None  # characters of the tool result the model received
    result_error: bool = False
    rewrite: str = ""  # the command a PreToolUse hook substituted (updatedInput.command), if any
    usage: dict = field(default_factory=dict)  # kind "usage": the request's final usage block, plus "request": n


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

    @property
    def requests(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "usage"]


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


def _result_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(x.get("text", "")) for x in content if isinstance(x, dict))
    return 0


def sidecar(path: Path) -> dict:
    """What `truetake import --transcript` copied from the session transcript into `<take>.sidecar.json`:
    PreToolUse rewrites (the stream's PreToolUse `hook_response` has no stdout) keyed by tool_use id, and each
    request's final usage (the stream's per-block usage is provisional) keyed by message id."""
    side = path.with_suffix(".sidecar.json")
    return json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}


def parse(path: Path, prompts: list[str] | None = None) -> Take:
    """`prompts`: the prompts of a resumed session, in order. A take may hold several `claude -p` invocations
    (prompt 1, then `--resume` follow-ups) back to back; each new `system/init` starts the next prompt."""
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    final_usage: dict[str, dict] = {}
    for e in events:  # a message's content blocks arrive as separate events; keep its last (complete) usage
        m = e.get("message") or {}
        if e.get("type") == "assistant" and m.get("id") and m.get("usage"):
            final_usage[m["id"]] = m["usage"]
    side = sidecar(path)
    rewrites = side.get("pre_tool_use", {})
    final_usage.update(side.get("usage", {}))  # final numbers from the transcript win over the stream's provisional ones
    steps: list[Step] = []
    init: dict = {}
    result: dict = {}
    hook_events = False
    inits = 0
    seen_msgs: set[str] = set()
    by_id: dict[str, Step] = {}
    for e in events:
        t, st = e.get("type"), e.get("subtype")
        if t == "system" and st == "init":
            inits += 1
            if inits == 1:
                init = e
            else:
                text = prompts[inits - 1] if prompts and len(prompts) >= inits else f"(prompt {inits} was not recorded)"
                steps.append(Step(kind="prompt", text=text, name=str(inits)))
        elif t == "system" and st in ("hook_started", "hook_response"):
            hook_events = True
            if st == "hook_response" and str(e.get("hook_event", "")).startswith("PostToolUse"):
                tool_name = str(e.get("hook_name", "")).partition(":")[2]
                idx = next((i for i in range(len(steps) - 1, -1, -1)
                            if steps[i].kind == "tool" and (not tool_name or steps[i].name == tool_name)), None)
                steps.append(Step(kind="hook", name=str(e.get("hook_name", "")),
                                  text=_additional_context(e.get("stdout") or e.get("output") or ""), tool_index=idx))
        elif t == "assistant":
            m = e.get("message") or {}
            mid = m.get("id")
            if mid and mid not in seen_msgs and mid in final_usage:
                seen_msgs.add(mid)
                steps.append(Step(kind="usage", name=str(m.get("model") or ""),
                                  usage={**final_usage[mid], "request": len(seen_msgs),
                                         "final": mid in side.get("usage", {})}))
            for part in m.get("content") or []:
                if part.get("type") == "tool_use":
                    tid = str(part.get("id") or "")
                    step = Step(kind="tool", name=part.get("name", ""), input=part.get("input") or {}, tool_id=tid)
                    rw = rewrites.get(tid) or {}
                    step.rewrite = str(rw.get("command") or "")
                    steps.append(step)
                    if tid:
                        by_id[tid] = step
                elif part.get("type") == "text" and part.get("text", "").strip():
                    steps.append(Step(kind="text", text=part["text"]))
        elif t == "user":
            for part in (e.get("message") or {}).get("content") or []:
                if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in by_id:
                    step = by_id[part["tool_use_id"]]
                    step.result_chars = _result_chars(part.get("content"))
                    step.result_error = bool(part.get("is_error"))
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
        "prompts": 1 + sum(1 for s in take.steps if s.kind == "prompt"),
        "api_requests": len(take.requests),
        "pre_tool_use_rewrites": sum(1 for s in take.tools if s.rewrite),
    }
