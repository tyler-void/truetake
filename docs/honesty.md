# Honesty

A truetake video claims one thing: *this is what the agent did when we asked it*. Every rule below protects
that claim. Some are enforced by the tool; the rest are the job of whoever writes the narration and picks
the take, and the report makes them checkable.

## The agent's behaviour is its own

- **The prompt is a natural request, sent exactly as written.** `truetake record` passes the scene's
  `prompt` to `claude -p` and adds nothing to the system prompt. The report prints the full command line
  each take ran with, and says whether every take used the same one.
- **Edit permission stays on.** Takes run with `--permission-mode acceptEdits` by default, so if the agent
  declines to change something, that is its choice, not a missing permission. Denied tool calls are counted
  per take in the report.
- **What the agent was given is recorded.** From each take's own `init` event: model, Claude Code version,
  permission mode, MCP servers and plugins. MCP servers come only from the scene's `mcp_config`
  (`--strict-mcp-config` is always passed). With `agent.isolation: project` (the default) only project and
  local settings load; if a user-level `CLAUDE.md` exists, the report warns that it may have been loaded.
  `agent.isolation: bare` excludes it (see [pitfalls](pitfalls.md#bare-mode)).

## Every take is kept

- `truetake record` runs N takes per scene (`takes:`) and keeps every raw stream, its stderr, the sandbox's
  `git status`, `diff --stat` and full diff, and a metadata file. Nothing is overwritten: a second
  `record` adds takes after the existing ones.
- `truetake select` needs a reason, and the report shows every take next to the chosen one: exit code,
  tool calls, whether the hook added context, reply length, files changed, denials, and the first line of
  the final reply.

## The words on screen are the agent's words

`truetake render` lays out the chosen take's stream as a terminal session. Every word the agent wrote
appears, in order. Only four kinds of change are made, each counted and listed in the report:

| Change | Why |
|---|---|
| Pacing: typing speed, streaming speed, pauses, the hold before the first tool call | A real take is too slow or too fast to read; pace is presentation, not content |
| Markdown markers dropped: `**`, backticks, heading `#` | A terminal does not render Markdown; the markers are noise |
| Paths in tool-call headers made relative to the sandbox | Shorter, and no machine path on screen |
| The home directory written as `~` | Privacy |

Word-wrapping to the terminal width is layout. Tool calls are shown as one-line headers (`● Read greet.py`);
their outputs are not shown. The hook's added context is shown verbatim as a highlighted block right after
the tool call it followed, taken from the stream's `hook_response` event. If a stream has no hook events (an
older Claude Code), `hook.text_command` can supply the text, and the report says so.

The prompt shown is the prompt the take was recorded with: if `truetake.yaml` changes it later, `render`
refuses.

## Code cards show real code

- The excerpt comes from `git show <commit>:<file>` at a pinned 40-character commit. A branch name or
  `HEAD` is refused.
- Every drawn row is checked against that source before anything is drawn. The only row that may be added
  is `…`, and only between two line ranges that are not adjacent.
- The title bar shows the file, the line ranges and the commit. Ligatures are switched off so `===` reads as
  three characters; tabs are drawn as spaces.
- The explanation line is yours. Keep it to what the code does.

## Timing is computed, then verified

A card covers the empty lower part of the terminal while the agent works, and must be gone before any text
reaches it. `render` replays the cast on a virtual screen, character by character and with scrolling, to
find the first moment text is inside the card's rectangle. The card fades out before that moment, with a
margin of two frames. Then every frame of the terminal-only video is read inside the rectangle; if any frame
shows text under the visible card, the render fails. Captions fire at the first character of the cast line
they are tied to, or at a named event (`@read:<path>`, `@hook`, `@reply`, `@tool:<Name>`).

## Narration describes the chosen take

This is the one rule the tool cannot check for you. Write or revise the narration **after** `select`, from
the chosen take, and never claim more than it shows. The report puts the narration next to the take so a
reviewer can compare. Captions help: a caption whose trigger is not in the chosen take (for example
`@read:greet.py` when the agent read the file through Bash instead) makes `render` fail rather than label
something that did not happen.
