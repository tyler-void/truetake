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
| `truetake import` | Bring in an existing `claude -p` stream-json run (and its `--resume` follow-ups) as a take, with its source and checksums; `--transcript` adds hook rewrites and final usage |
| `truetake select` | List a scene's takes, or choose one with a reason the report quotes |
| `truetake render` | Take → asciicast → [agg](https://github.com/asciinema/agg) → video, with a code card and step captions timed from the cast; title cards and slides |
| `truetake narrate` | One TTS clip per narration string: [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) locally (default) or Gemini |
| `truetake assemble` | Segments in order, one continuous audio track encoded once, loudness normalised, click test at every join, time budget enforced |
| `truetake check` | Privacy gate over everything shown or spoken, a frame every N seconds plus contact sheets, card-cover and audio checks |
| `truetake report` | `REPORT.md`: takes, the chosen one and why, every change to the agent's text, cards, captions, narration, checks |
| `truetake film` | The film set (experimental): a real run (a Codex transcript, or a recorded take) drawn frame by frame on a themed stage, with smooth scrolling, a spring cursor, a camera and a real-time clock; exports a cue list for [truetake-score](https://github.com/tyler-void/truetake-score) |
| `truetake callouts` | For a live talk: a large caption and a highlight box (or a zoomed copy of a terminal line) per beat, laid over the assembled video into a copy of the same length; `--preview` writes one frame per callout first |

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

For comparisons and benchmark demos, a scene can also take `followups` (prompts of a resumed session),
`display_cwd`, `prompt_seconds`, `pacing` overrides, `annotate: {sizes, rewrites, usage, usage_bar}` (with
top-level `usage_prices`), and a short `title` for the terminal header; a `pair` segment (`left`, `right`,
`cols`, `rows`) shows two scenes side by side; title and slide segments take `hold` (seconds on screen when
there is no narration). See [docs/honesty.md](docs/honesty.md) for what each one changes and how the report
lists it.

For a talk narrated live, `callouts` lays captions and boxes over the assembled video:

```yaml
slides: {pdf: slides.pdf, text: slides.text.json}   # text: {page: words}, for PDFs that are pixels only
callouts:
  output: demo-cap.mp4                              # never the assembled `output`
  items:
    - {from: intro, to: fix, text: The task in one line, boxes: [{segment: intro, text: title}]}
    - from: fix                                     # seconds, <segment>, <segment>:end, <segment>+<s>, <scene>@<mark>
      to: fix:end
      text: It reads the config first
      boxes: [{scene: fix, line: '^● Read .*config', rows: 1}]   # followed as the terminal scrolls
      zoom: 1.6                                     # a magnified copy of that line for zoom_seconds (default 8)
```

A box is a slide rectangle (`segment` + `rect` in output pixels), a title-card line (`segment` + `text`),
terminal rows matching a regex (`scene` + `line`) or a scene's usage bar (`scene` + `status`). `truetake
callouts --preview` writes one frame per callout and a contact sheet through the same filter graph as the
full render; `truetake callouts` writes the copy and checks its length against the source to the frame.

### The film set (experimental)

`truetake film` draws a run as a film rather than a screen recording: a terminal pane on a stage, with the
scroll gliding, new characters fading in, a cursor on a spring, and a camera that pushes in and breathes.
Every frame is a pure function of film time, drawn with [Skia](https://skia.org)
(`pip install "truetake[film]"`), so `--preview` frames are the frames of the full render.

```yaml
film:
  output: film.mp4
  duration: auto                          # or seconds
  theme: marginalia                       # or mocha, or {base: marginalia, accent: "#2E567A"}
  fonts: {serif: fonts/Literata.ttf, sans: fonts/Lexend.ttf}   # TTF/OTF files
  panes:                                  # one pane per thread; `pane:` for a single one
    - {id: orchestrator, source: codex, path: transcript-01.ndjson, title: ORCHESTRATOR}
    - {id: currency, source: codex, path: transcript-02.ndjson, title: SUBAGENT, subtitle: BUG-02}
  clock: {utc_offset: 7}
  beats:                                  # read these real-time windows; time-lapse the rest
    - {from: "12:34:12", to: "12:34:24"}
    - {from: "12:44:00", to: "12:44:10"}
  camera: auto                            # or [{at: 5, zoom: 1.3, pane: currency}, {at: 16, zoom: 1.0}]
  score: score.wav                        # optional, from truetake-score
```

Several panes share one timeline: items from every thread are merged in real-time order, so the clock is
true for all of them. A pane opens when its thread starts, and the panes glide to the next layout: one
centred, two side by side, then the first on the left with the others stacked. `beats` are the moments the
film reads; between them it runs as a time-lapse, where lines land whole and the clock races, labelled
"×N real time · time-lapse". `camera: auto` pushes in on the pane that writes the most during each beat,
far enough that its text is near full size, and pulls back to the whole set for each time-lapse.

The rules are the scene rules. Text comes from the source as recorded. Messages type at a little above
reading speed and get a short beat, so a viewer can read them without the film stalling. A source with
timestamps gets a clock showing the real time on screen. The clock holds while an item that had already
happened reaches the screen, runs fast through the idle time between items, and says how fast ("×40 real
time", "paused to read"). An output that would show a private name is replaced by one row that says so;
private text anywhere else stops the film before a frame is drawn. `.truetake/film/film.json` records the
source, each change to its text, the pacing, the camera and the time map; `cues.json` lists every visible
event for the score.

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
