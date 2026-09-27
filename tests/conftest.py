import json

import pytest


def stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


@pytest.fixture
def take_file(tmp_path):
    """A small synthetic stream-json take: a Read, a hook that adds context, an Edit, and a reply."""
    cwd = "/tmp/sandbox"
    hook_out = json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "Reviewed rule: keep the format."}})
    text = stream(
        {"type": "system", "subtype": "init", "claude_code_version": "9.9.9", "model": "m", "permissionMode": "acceptEdits",
         "mcp_servers": [], "plugins": []},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": f"{cwd}/app.py"}}]}},
        {"type": "system", "subtype": "hook_started", "hook_name": "PostToolUse:Read", "hook_event": "PostToolUse"},
        {"type": "system", "subtype": "hook_response", "hook_name": "PostToolUse:Read", "hook_event": "PostToolUse",
         "stdout": hook_out, "output": hook_out},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "I read it. The **rule** says keep `greet()` as is."}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": f"{cwd}/notes.md"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "## Done\nI changed nothing in app.py."}]}},
        {"type": "result", "subtype": "success", "num_turns": 3, "duration_ms": 1000},
    )
    p = tmp_path / "take-1.jsonl"
    p.write_text(text, encoding="utf-8")
    return p, cwd
