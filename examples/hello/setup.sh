#!/usr/bin/env bash
# Build the hello example: a toy repository (the sandbox the agent works in) and a truetake.yaml.
#
#   examples/hello/setup.sh [PROJECT_DIR]      default: ./hello-demo
#   SANDBOX=/tmp/somewhere examples/hello/setup.sh
#
# The sandbox goes under /tmp so that no personal path is ever on screen. The project directory holds
# truetake.yaml and the work directory with every take; keep it outside the sandbox (takes reset the sandbox).
set -euo pipefail

PROJECT=${1:-./hello-demo}
SANDBOX=${SANDBOX:-/tmp/truetake-hello}

if [ -e "$SANDBOX" ]; then
  echo "$SANDBOX exists; remove it or set SANDBOX to another path" >&2
  exit 1
fi
mkdir -p "$SANDBOX/.claude/hooks" "$PROJECT"

cat > "$SANDBOX/greet.py" <<'PY'
"""Greetings used in the shop's order emails."""


def greet(name: str) -> str:
    return f"Hello, {name}!"


def farewell(name: str) -> str:
    return f"Goodbye, {name}."
PY

cat > "$SANDBOX/test_greet.py" <<'PY'
from greet import farewell, greet


def test_greet():
    assert greet("Ada") == "Hello, Ada!"


def test_farewell():
    assert farewell("Ada") == "Goodbye, Ada."
PY

# A tiny review hook: when the agent reads greet.py, add a reviewed team note to its context.
cat > "$SANDBOX/.claude/hooks/team_note.py" <<'PY'
"""PostToolUse hook for Read: hand the agent the reviewed team note for the file it just opened."""
import json
import sys

NOTES = {
    "greet.py": "Team note (reviewed): the billing export matches the greeting line exactly, "
                "so greet() must keep returning 'Hello, <name>!'.",
}

payload = json.load(sys.stdin)
path = payload.get("tool_input", {}).get("file_path", "")
note = next((text for name, text in NOTES.items() if path.endswith("/" + name) or path == name), None)
if note:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}))
PY

cat > "$SANDBOX/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "Read", "hooks": [{"type": "command", "command": "python3 .claude/hooks/team_note.py", "timeout": 10}]}
    ]
  }
}
JSON

git -C "$SANDBOX" init -q
git -C "$SANDBOX" add -A
# A neutral identity with no email address: nothing personal can reach the screen through `git log`.
git -C "$SANDBOX" -c user.name=hello-demo -c user.email= -c commit.gpgsign=false commit -qm "Toy shop greetings"
SHA=$(git -C "$SANDBOX" rev-parse HEAD)

cat > "$PROJECT/truetake.yaml" <<YAML
# The hello example, written by examples/hello/setup.sh. Sandbox: $SANDBOX (commit $SHA).
version: 1
title: truetake hello
output: hello-demo.mp4
work_dir: .truetake
budget_seconds: 90
label: toy repository made by examples/hello/setup.sh

video: {width: 1920, height: 1080, fps: 24}
terminal: {cols: 120, rows: 30, font_family: JetBrains Mono, font_size: 20}

agent:
  command: claude
  isolation: project
  permission_mode: acceptEdits

voice: {engine: kokoro, name: af_heart, loudness_lufs: -16}

privacy: {frame_every: 3}

segments:
  - id: intro
    kind: title
    title: truetake
    subtitle: Real agent takes, turned into honest demo videos
    narration: >-
      This video was made by truetake. It records real takes of a coding agent, keeps every one, and
      turns the chosen take into a narrated demo. The repository is a toy that a setup script built.

  - id: greet
    kind: scene
    cwd: $SANDBOX
    prompt: Make the greeting in greet.py friendlier, something like "Hi Ada, great to see you!". Update the test too.
    takes: 2
    visual_seconds: 34
    hook: {after_read: greet.py}
    card:
      repo: $SANDBOX
      commit: $SHA
      file: .claude/hooks/team_note.py
      lines: [[5, 14]]
      explain: Runs after every Read and adds the reviewed note for the file the agent opened
    captions:
      - {text: "① The agent reads greet.py", at: "@read:greet.py"}
      - {text: "② The hook adds the team note", at: "@hook"}
      - {text: "③ The agent's answer, verbatim", at: "@reply"}
    narration:
      card: >-
        Here is the project's hook. After every file read, it adds the reviewed team note for that file to
        the agent's context.
      main: >-
        We ask Claude Code to make the greeting friendlier, with no hint about the note. It reads greet.py,
        and the hook adds the note. What the agent does next is its own decision, and every take is in the
        report.

  - id: outro
    kind: title
    title: Every take kept. Every word verbatim.
    subtitle: truetake record · select · build
    narration: Every take is kept as evidence, and every word on screen came from the recorded take.
YAML

echo "sandbox:  $SANDBOX (commit ${SHA:0:12})"
echo "project:  $PROJECT/truetake.yaml"
echo
echo "next:"
echo "  cd $PROJECT"
echo "  truetake record                       # two fresh takes with claude -p"
echo "  truetake select --scene greet --list"
echo "  truetake select --scene greet --take N --reason \"why this take\""
echo "  # read the chosen take, then make the narration in truetake.yaml describe what it shows"
echo "  truetake build                        # narrate, render, assemble, check, report"
