# truetake

**Real agent takes, turned into honest demo videos.**

truetake records fresh takes of a coding agent (Claude Code headless first), keeps every take as evidence,
and builds a narrated demo video with verbatim agent text, code cards from the repository, step captions,
and privacy checks before anything is rendered.

A demo video of an agent says "this is what it did when we asked". truetake is built so that sentence stays
true: the prompt goes in exactly as written, several takes are recorded and all of them kept, the words on
screen are the agent's own, the code on a card is checked against `git show`, and a report lists every
take, every choice and every check.

## What it does

One `truetake.yaml` drives eight subcommands:

| Command | What it does |
|---|---|
| `truetake init` | Write an annotated `truetake.yaml` |
| `truetake record` | Run N fresh takes per scene with `claude -p` in a sandbox; keep every stream, stderr, git status and diff |
| `truetake select` | List a scene's takes, or choose one with a reason the report quotes |
| `truetake render` | Take → asciicast → [agg](https://github.com/asciinema/agg) → video, with a code card and step captions timed from the cast; title cards and slides |
| `truetake narrate` | One TTS clip per narration string: [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) locally (default) or Gemini |
| `truetake assemble` | Segments in order, one continuous audio track encoded once, loudness normalised, click test at every join, time budget enforced |
| `truetake check` | Privacy gate over everything shown or spoken, a frame every N seconds plus contact sheets, card-cover and audio checks |
| `truetake report` | `REPORT.md`: takes, the chosen one and why, every change to the agent's text, cards, captions, narration, checks |

`truetake build` runs narrate, render, assemble, check and report in one go.

## Quickstart: the hello example

```bash
git clone https://github.com/tyler-void/truetake && cd truetake
uv venv && uv pip install -e ".[kokoro]"      # or: pip install -e ".[kokoro]"
source .venv/bin/activate

examples/hello/setup.sh                        # a toy repo in /tmp/truetake-hello, ./hello-demo/truetake.yaml
cd hello-demo
truetake record                                # 2 fresh takes (real Claude Code runs)
truetake select --scene greet --list
truetake select --scene greet --take 2 --reason "why this take"
# edit narration.main in truetake.yaml so it describes what that take shows
export TRUETAKE_DENY_PATTERN='your|names|here'
truetake build
```

You get `hello-demo.mp4` (about 50 seconds), `.truetake/REPORT.md`, and contact sheets in
`.truetake/check/sheets/`. See [examples/hello](examples/hello/README.md).

## Requirements

- Python 3.10 or later.
- [Claude Code](https://code.claude.com), signed in. With `agent.isolation: bare`, `ANTHROPIC_API_KEY`
  instead.
- [agg](https://github.com/asciinema/agg): `cargo install --git https://github.com/asciinema/agg`.
- [FFmpeg](https://ffmpeg.org) (`ffmpeg`, `ffprobe`) and fontconfig (`fc-match`, `fc-list`).
- A monospace font agg can find by family name. The default is
  [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono), and a sans-serif font with the characters
  your captions use.
- Optional: [Poppler](https://poppler.freedesktop.org) for slides (`pdftoppm`, `pdftotext`).
- Optional: the `kokoro` extra for local narration. The model downloads on first use; it needs spaCy's
  `en_core_web_sm`, see [pitfalls](docs/pitfalls.md#kokoro-needs-a-spacy-model-it-cannot-always-install).
  For CPU-only PyTorch, install `torch` from `https://download.pytorch.org/whl/cpu` first.
- Optional: `GEMINI_API_KEY` for `voice.engine: gemini`.

Nothing is bundled: fonts, model weights and programs are installed separately. Licenses are in
[THIRD_PARTY.md](THIRD_PARTY.md).

## Use it from Claude Code

The repository is a Claude Code plugin and a one-plugin marketplace. Its skill walks an agent through the
whole workflow: plan, sandbox, record, select, render, narrate, assemble, check, report.

```bash
claude plugin marketplace add tyler-void/truetake
claude plugin install truetake@truetake
```

Then ask for a demo video, or run `/truetake:truetake`. To use the skill without the plugin, copy
[`skills/truetake`](skills/truetake/SKILL.md) into `~/.claude/skills/` (it becomes `/truetake`). The skill
calls the `truetake` CLI, so install that too: `uv tool install "truetake[kokoro] @ git+https://github.com/tyler-void/truetake"`.

## How honesty is enforced

- **The prompt is sent as written, with nothing added to the system prompt.** Edits are allowed, so a
  refusal is the agent's choice. The report prints the exact command line of every take and what the agent
  ran with (model, version, permission mode, MCP servers, plugins).
- **Every take is kept.** Choosing one needs a reason, and the report shows all of them side by side.
- **The agent's words are verbatim.** Only pacing, Markdown markers, relative paths in tool headers and `~`
  for the home directory change, and each change is counted in the report. The hook's added context comes
  from the stream's own `hook_response` event.
- **Code cards are real code.** Excerpts come from `git show <commit>:<file>` at a pinned commit, and every
  drawn row is checked against it; the only addition allowed is a `…` row.
- **Timing is computed, then verified.** A card fades out before text reaches it, found by replaying the cast
  on a virtual screen. Then every frame is read to prove no text was under the visible card.
- **Captions and narration must match the chosen take.** A caption whose trigger is not in the take fails
  the render; narration is printed next to the take in the report for review.

Details: [docs/honesty.md](docs/honesty.md).

## How privacy is enforced

`render` refuses to draw, and `check` fails the build, on any match of your `TRUETAKE_DENY_PATTERN`, a home
path, your username, or an email address not allowed in `truetake.yaml`. The scan covers terminal text, hook
text, captions, card excerpts, narration, titles and slide text. `check` also exports a frame every few
seconds and contact sheets, because no scan reads pixels. Keys come from the environment only. Details:
[docs/privacy.md](docs/privacy.md).

## truetake.yaml

```yaml
version: 1
output: demo.mp4
budget_seconds: 90
voice: {engine: kokoro, name: af_heart, loudness_lufs: -16}
segments:
  - {id: intro, kind: title, title: My project, narration: What the viewer is about to see.}
  - id: fix
    kind: scene
    cwd: /tmp/my-sandbox              # a committed git repo you control
    prompt: A natural request, as a user would type it.
    takes: 3
    visual_seconds: 35
    allowed_tools: []                 # read-only MCP tools, if the scene uses MCP
    card: {repo: /tmp/my-sandbox, commit: <40-char sha>, file: src/app.py, lines: [[10, 22]], explain: One plain line.}
    captions:
      - {text: "① The agent reads app.py", at: "@read:src/app.py"}
      - {text: "② It answers", at: "@reply"}
    narration: {card: While the card is up., main: What the chosen take shows.}
```

`truetake init` writes the full annotated version, with slides, the terminal size and font, MCP configs,
hooks, isolation, email allowlists and frame spacing.

## Pitfalls

What goes wrong in practice, and how truetake handles it: MCP tools under `claude -p`, hook output in the
stream, trust and `--bare`, sandbox resets, silent VHS failures, WAV trailers, clicks at joins, loudness,
fonts, and pacing against the time budget. See [docs/pitfalls.md](docs/pitfalls.md).

## Related tools

These tools turn an existing agent session into something you can share. Descriptions are from their own
READMEs:

- [ccreplay](https://github.com/zhebrak/ccreplay): "Terminal replay videos of Claude Code sessions": reads
  Claude Code JSONL session logs and renders MP4 videos.
- [sessionreel](https://github.com/mandu5/sessionreel): reads a Claude Code session log and renders a short
  recap video, with optional narration.
- [claude-replay](https://github.com/es617/claude-replay): turns Claude Code, Cursor, Codex CLI, Gemini
  CLI and other agents' session logs into interactive, self-contained HTML replays.
- [tracecast](https://github.com/yarinlevin18-ai/tracecast): "Replay your agent runs like a screen
  recording": an animated in-browser player for a Claude Code session file, with a shareable link.
- [cc-prism](https://github.com/gulp/cc-prism): turns Claude Code sessions into asciinema recordings.

The difference is where they start. Those tools replay a session log that already exists. truetake records
fresh takes on purpose, several per scene, keeps all of them as evidence, and builds the video from the one
you choose, with a report that says why. If you already have the session you want to show, one of them may
suit you better.

## License

MIT, © 2026 Tyler (@tyler-void). See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
