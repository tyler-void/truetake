# The bare-C example

A repository with only a Makefile that builds a static binary with no libc and no startup files, and one
scene: we ask Claude Code for hello world under those rules. The agent has to write its own `_start` and
reach the kernel through raw syscalls.

```bash
examples/bare-c/setup.sh                # builds /tmp/truetake-bare-c (the sandbox) and ./bare-c-demo/truetake.yaml
cd bare-c-demo
truetake record                          # two fresh takes with claude -p (real runs: they use your quota)
truetake select --scene hello --list
truetake select --scene hello --take N --reason "why this take"
# make narration.main in truetake.yaml describe what the chosen take shows
truetake build
```

Notes:
- `acceptEdits` allows file edits but not shell commands, so the scene allows `make` and running `./hello`
  with `allowed_tools`, and nothing else. Without it the agent writes `hello.c` and every build is denied.
- `hello` is gitignored, so the scene resets with `clean: -fdx`; with the default `-fd` one take's binary
  is still in the tree when the next take starts.
- The scene shows each tool call's description, not its output, so the owner column of an `ls -la` never
  reaches the screen. The privacy scan still covers everything that does.
- The font is `JetBrains Mono`; a Nerd Font build is `JetBrainsMono Nerd Font Mono`. Change
  `terminal.font_family` to match yours.
