# The hello example

A toy repository, a tiny review hook, and one scene: we ask Claude Code to make a greeting friendlier, and the
repository's hook hands the agent a reviewed team note when it opens `greet.py`. What the agent does with
that note is up to it; the video shows whatever the take you choose shows.

```bash
examples/hello/setup.sh                 # builds /tmp/truetake-hello (the sandbox) and ./hello-demo/truetake.yaml
cd hello-demo
truetake record                          # two fresh takes with claude -p (real runs: they use your quota)
truetake select --scene greet --list
truetake select --scene greet --take N --reason "why this take"
```

Now read the chosen take (`.truetake/takes/greet/take-N.jsonl`) and make `narration.main` in `truetake.yaml`
describe what it actually shows. The generated narration only says what is true of any take. Then:

```bash
export TRUETAKE_DENY_PATTERN='…'         # names you never want on screen
truetake build                           # narrate, render, assemble, check, report
```

The video is `hello-demo/hello-demo.mp4`, the report `hello-demo/.truetake/REPORT.md`, and the frames to
look at are in `hello-demo/.truetake/check/sheets/`.

What the scene shows, in the one run used to test this example: in one take the agent read `greet.py` through
Bash, so the Read hook never fired and the agent found the note by reading the hook script; in the other it
used the Read tool, the hook added the note, and the agent changed nothing and asked how to go ahead. Your
takes may differ. That is the point.

Notes:
- `setup.sh` refuses to overwrite an existing sandbox; remove `/tmp/truetake-hello` or set `SANDBOX`.
- The sandbox commit has the author `hello-demo` and no email address, so nothing personal can reach the
  screen through `git log`.
- The font is `JetBrains Mono`; if yours is installed under another family name (a Nerd Font build, for
  example), change `terminal.font_family`. `truetake render` names the closest installed families.
