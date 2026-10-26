# Pitfalls

Everything here was hit for real while building demo videos this way. Where Claude Code behaviour is
involved, the version was 2.1.280; check the current docs if yours is newer.

## Recording

### MCP tools are denied under `claude -p` even with `acceptEdits`

`acceptEdits` approves file edits, not MCP tool calls. In a headless take the agent's MCP calls are
denied and it carries on without them, which silently changes the take. List the tools the scene may use:

```yaml
allowed_tools: ["mcp__myserver__find_thing", "mcp__myserver__get_thing"]
```

Allow read-only tools only, so the agent's edits stay its own choice. The report counts permission denials
per take; a non-zero count is worth a look.

### Hook output is not in the stream unless you ask for it

A `PostToolUse` hook's `additionalContext` reaches the agent, but a plain `--output-format stream-json`
stream does not show it. `truetake record` passes `--include-hook-events`, which adds `hook_started` and
`hook_response` events; the `hook_response` carries the hook's stdout, so the video shows exactly the text
the agent received. Without those events, the fallback is to capture the same text with a command
(`hook.text_command`, for example the tool your hook calls) and to verify the record ids it prints against
the hook's own state or log before trusting it. The report says which source was used.

### A Read hook does not see files read through Bash

A hook with matcher `Read` fires on the Read tool only. If the agent opens a file with `cat` or `sed` in
Bash, the hook never runs for it. In the hello example one take read `greet.py` through Bash and got no
note; the other used Read and got it. Both are real takes; pick the one that shows what the video claims,
and say why in `select --reason`. A caption with trigger `@read:<path>` makes `render` fail for a take
that never read the file, instead of labelling something that did not happen.

### Isolate the take from your own setup

- `--strict-mcp-config` is always passed, so only the scene's `mcp_config` servers load, never your user
  or project ones.
- stdin is closed (`< /dev/null`); otherwise `claude -p` waits on it or warns.
- <a id="bare-mode"></a>Without `--bare`, `claude -p` still loads context an interactive session would,
  and the docs note that it runs a project's hooks and connects its `.mcp.json` servers **with no trust
  prompt** ([Headless mode](https://code.claude.com/docs/en/headless#start-faster-with-bare-mode),
  [What runs before you trust a folder](https://code.claude.com/docs/en/permissions#what-runs-before-you-trust-a-folder)).
  Only record in sandboxes you built and control. `agent.isolation: project` (default) passes
  `--setting-sources project,local`, so your user-level settings stay out; `agent.isolation: bare` passes
  `--bare` and hands the sandbox's settings over explicitly with `--settings`. Bare mode needs
  `ANTHROPIC_API_KEY`, because it does not use a subscription login.

### Commit the sandbox

Every take must start from the same state. `record` refuses a sandbox with uncommitted changes, and after
each take it saves the diff and resets with `git checkout -- .` and `git clean` (`-fd` by default,
`clean:` to change). Because of that reset, it also refuses a work directory inside the sandbox: `git clean`
would delete the evidence.

### `/tmp` is emptied at reboot

A sandbox under `/tmp` keeps paths on screen neutral, and is cheap to rebuild with a setup script. The work
directory is evidence: keep it somewhere that survives.

## Rendering

### VHS and headless Chromium can fail silently

Tools that drive a browser-based terminal (VHS uses ttyd and Chromium) can produce a blank or truncated
video on some systems without an error. truetake renders from an asciicast with
[agg](https://github.com/asciinema/agg), which needs no browser. If agg is not an option, the fallback is an
HTML replay of the cast recorded with Playwright's `recordVideo`; truetake does not ship it, and the cover
check and frame review still apply to whatever you record.

### agg compresses long pauses

agg's `--idle-time-limit` defaults to 5 seconds, which would shorten the hold before the first tool call and
break the timing truetake computed from the cast. truetake passes a very large limit.

### Text can appear a frame early

agg groups events into frames and the GIF→MP4 step resamples to the output rate, so a character can be on
screen up to about a frame before its cast timestamp. The card therefore leaves two frames on top of its
0.05 s gap, and the cover check reads the actual frames to prove it.

### Fonts are found by family name

agg matches `terminal.font_family` against installed families; a Nerd Font build of JetBrains Mono is
called "JetBrainsMono Nerd Font Mono", not "JetBrains Mono". `render` checks with `fc-list` and names the
closest installed families. Code-font ligatures are switched off on cards: `===` drawn as one glyph is not
what the code says.

`render` checks the family with `fc-list`, but agg uses its own font matching: a fontconfig alias such as
"JetBrainsMono NF" passes the check and then fails in agg with "no faces matching font family options". Use
the full family name.

### No line may wrap

The terminal wraps nothing: agent text is word-wrapped at `terminal.cols − 4`, and `render` fails if any
line is wider than the terminal. A token longer than the line (a long path in a narrow pane) is split
across lines; that is layout, not a change to the text.

### Long prompts take long to type

Prompts are typed at 30 characters per second, so a 900-character benchmark prompt takes 30 s of the scene.
Set the scene's `prompt_seconds` to cap the typing time.

## Imported runs and annotations

### The stream's usage is provisional

In `--output-format stream-json`, each content block of an assistant message is its own event and carries
the message's usage as it stood when the block was emitted: `output_tokens` counts only what was written so
far (3 where the transcript says 175). A cost computed from the stream alone comes out low (in one session,
$0.126 instead of $0.163). The session transcript (`<config>/projects/<cwd>/<session>.jsonl`) has the final
numbers. Import with `--transcript`, which keeps the final usage per message in `take-NN.sidecar.json`;
without it, the report says the usage annotations use the stream's provisional numbers.

### A PreToolUse hook's rewrite is not in the stream

A PreToolUse hook that rewrites a command (`updatedInput`, as rtk's hook does) leaves a `hook_response`
event with an empty stdout, and the `tool_use` in the stream is the original command. The substituted
command is only in the transcript's `hook_success` attachment, keyed by `toolUseID`. `import --transcript`
copies it into the sidecar so `annotate.rewrites` can show it.

### One run, several streams

A session continued with `claude -p … --resume <id>` writes one stream per invocation. Import them in order
and list the follow-up prompts in the scene's `followups`; import refuses when the number of invocations and
prompts differ. `total_cost_usd` and `modelUsage` in each result are cumulative over the session.

### Labels on a half-width pane

In a `pair`, each scene renders on half the canvas. The label strip uses a smaller font there, and may have
two lines (`\n` in `label`); keep each line short. The scene's `title` is the short header inside the
terminal; `label` is the strip under it.

## Audio

### A WAV from a TTS service can carry more than audio

Some services return a complete WAV file with extra chunks after the samples, such as a `C2PA` provenance
manifest. Written out as raw PCM, the 44-byte header plays as a click at the start and the manifest as a
full-scale burst of noise at the end. truetake parses the RIFF container and keeps only the `data` chunk;
other chunks are saved beside the clip, never played.

### Clicks at joins

Encoding each segment's audio separately puts an encoder boundary at every join. truetake builds the whole
narration in one filter graph, encodes it once, gives every clip 5 ms fades, and makes every segment a whole
number of frames long. `assemble` then decodes the result and fails if any join is louder than −60 dBFS.

### Quiet narration

Local TTS can come out far below streaming loudness (one Kokoro cut measured −25.5 LUFS). The track is
normalised with ffmpeg's two-pass `loudnorm` to `voice.loudness_lufs` (−16 by default); `check` fails if the
result is more than 2 LU off.

### Kokoro needs a spaCy model it cannot always install

Kokoro's English front end loads spaCy's `en_core_web_sm` and tries to install it with pip on first use. In
a virtual environment without pip (uv creates those), that fails. Install the wheel yourself, matching your
spaCy version:

```bash
v=$(python -c "import spacy; print('.'.join(spacy.__version__.split('.')[:2]))")
uv pip install "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-$v.0/en_core_web_sm-$v.0-py3-none-any.whl"
```

## Pacing versus the time budget

Real takes are long. Streaming an agent's reply at a readable 40 characters per second, three scenes ran
60–90 seconds each. truetake fits each scene to `visual_seconds` by choosing the reply speed, keeping every
pause, and reports the speed it chose. Above roughly 250 characters per second nobody reads along: give the
scene more time, cut a scene, or accept that the viewer reads the finished screen during the end hold.
`assemble` fails when the whole video is over `budget_seconds`.
