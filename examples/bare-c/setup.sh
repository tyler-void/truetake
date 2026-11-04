#!/usr/bin/env bash
# Build the bare-C example: an almost empty C repository (the sandbox the agent works in) and a truetake.yaml.
#
#   examples/bare-c/setup.sh [PROJECT_DIR]     default: ./bare-c-demo
#   SANDBOX=/tmp/somewhere examples/bare-c/setup.sh
#
# The sandbox goes under /tmp so that no personal path is ever on screen. Keep the project directory outside
# the sandbox (takes reset the sandbox).
set -euo pipefail

PROJECT=${1:-./bare-c-demo}
SANDBOX=${SANDBOX:-/tmp/truetake-bare-c}

if [ -e "$SANDBOX" ]; then
  echo "$SANDBOX exists; remove it or set SANDBOX to another path" >&2
  exit 1
fi
mkdir -p "$SANDBOX" "$PROJECT"

# The house rules: no libc, no startup files, a static binary. The agent writes hello.c itself.
cat > "$SANDBOX/Makefile" <<'MK'
# No libc, no crt0, no dynamic loader: the program brings its own _start.
CFLAGS  = -Os -static -nostdlib -fno-builtin -fno-stack-protector -fno-asynchronous-unwind-tables
LDFLAGS = -Wl,--build-id=none -s

hello: hello.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

clean:
	rm -f hello

.PHONY: clean
MK

cat > "$SANDBOX/README.md" <<'MD'
# hello

Hello, world for x86_64 Linux, the hard way. `make` builds a static binary with no libc and no startup
files, so `hello.c` has to talk to the kernel itself.
MD

printf 'hello\n' > "$SANDBOX/.gitignore"

git -C "$SANDBOX" init -q
git -C "$SANDBOX" add -A
# A neutral identity with no email address: nothing personal can reach the screen through `git log`.
git -C "$SANDBOX" -c user.name=bare-c-demo -c user.email= -c commit.gpgsign=false commit -qm "Makefile with no libc"
SHA=$(git -C "$SANDBOX" rev-parse HEAD)

cat > "$PROJECT/truetake.yaml" <<YAML
# The bare-C example, written by examples/bare-c/setup.sh. Sandbox: $SANDBOX (commit $SHA).
version: 1
title: hello, the hard way
output: bare-c-demo.mp4
work_dir: .truetake
budget_seconds: 90
label: toy repository made by examples/bare-c/setup.sh

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
    title: Hello, world. The hard way.
    subtitle: No libc, no includes, just the kernel
    narration: >-
      Hello world in C usually means one include and a call to printf. Here there is no libc at all, so
      the program has to ask the kernel itself. We gave Claude Code a repository with only a Makefile.

  - id: hello
    kind: scene
    cwd: $SANDBOX
    prompt: Write hello.c so that make builds it and ./hello prints "Hello, world!". No libc, so no includes and no printf. Then build it and run it.
    takes: 2
    clean: -fdx          # hello is gitignored; without -x one take's binary is in the next take's tree
    # acceptEdits allows edits, not commands: let it build and run, nothing else
    allowed_tools: ["Bash(make:*)", "Bash(./hello)", "Bash($SANDBOX/hello)"]
    visual_seconds: 40
    card:
      repo: $SANDBOX
      commit: $SHA
      file: Makefile
      lines: [[1, 6]]
      explain: Static, no libc, no startup files; hello.c must bring its own _start
    captions:
      - {text: "① It writes hello.c, then builds and runs it", at: "@tool:Write"}
      - {text: "② The agent's answer, verbatim", at: "@reply"}
    narration:
      card: >-
        This is the whole repository. The Makefile builds a static binary with no C library and no
        startup files.
      main: >-
        We ask for hello world under those rules. What the agent writes, and whether it builds, is in the
        take.

  - id: outro
    kind: title
    title: I use Arch, btw.
    subtitle: truetake record · select · build
    narration: Every take is kept as evidence, and every word on screen came from the recorded take.
YAML

echo "sandbox:  $SANDBOX (commit ${SHA:0:12})"
echo "project:  $PROJECT/truetake.yaml"
