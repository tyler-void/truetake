---
name: truetake
description: Make an honest demo video of a coding agent from real, freshly recorded takes. Records several takes of Claude Code (headless) per scene in a sandbox, keeps every take as evidence, and builds a narrated video with verbatim agent text, code cards from the repository, step captions and a privacy gate. Use when the user wants a demo video, screencast or recording of an agent working on a repository.
when_to_use: The user asks for a demo video, a product walkthrough recorded from real agent runs, or to turn agent takes into a video; or mentions truetake or truetake.yaml.
argument-hint: "[path to truetake.yaml]"
license: MIT
---

# truetake: real agent takes, turned into honest demo videos

You drive the `truetake` CLI through nine phases. The video claims "this is what the agent did when we asked
it", so every phase protects that claim. Do not skip a phase, and do not work around a failing check: a
failure means the video would say something untrue or leak something private.

If `truetake` is not on PATH, install it: `uv tool install "truetake[kokoro] @ git+https://github.com/tyler-void/truetake"`
(or `pipx install`). It also needs `agg`, `ffmpeg`, fontconfig and a monospace font; `truetake render` and
`truetake check` say what is missing. See the repository README for requirements.

## Phases

1. **Plan.** Agree with the user on the story: the segments (title cards, slides, scenes), what each scene
   should show, the time budget, and the voice. For each scene decide the natural prompt a user would type
   (no hints about what you hope the agent does), the code card (a file and 8–15 lines at a pinned commit,
   plus one plain-English explanation line) and 2–4 captions. Write `truetake.yaml` (`truetake init` gives
   an annotated one).

2. **Sandbox.** Build the repository the agent will work in, with a script, under a neutral path such as
   `/tmp/<name>`, and commit it. Keep `truetake.yaml` and its work directory **outside** the sandbox (takes
   reset the sandbox with `git clean`). Pin card commits with `git -C <repo> rev-parse HEAD`.

3. **Record.** `truetake record`. It runs `takes:` fresh takes per scene with `claude -p`, nothing added to
   the system prompt, edits allowed, and keeps every stream, stderr, git status and diff. Tell the user
   before you start: each take is a real agent run and uses their quota.

4. **Select.** `truetake select --scene <id> --list`, then read the chosen candidate's full stream. Choose
   with `truetake select --scene <id> --take N --reason "<why, in a sentence the report can quote>"`. If no
   take shows what the plan hoped for, say so to the user; use the best honest take, or change the plan.
   Never re-record until a take "behaves".

5. **Render.** `truetake render`. Casts, code cards (checked line by line against `git show`), captions and
   still segments. It fails on a privacy hit, a card that does not match the source, a caption whose trigger
   is not in the take, or text under a visible card; fix the cause, not the check.

6. **Narrate.** Now revise the narration in `truetake.yaml` so it describes what the chosen take actually
   shows, and never claims more. Then `truetake narrate`. Run `truetake render` again afterwards: it fits the
   hold before the first tool call to the card's narration.

7. **Assemble.** `truetake assemble`. One audio track, encoded once, loudness normalised, a click test at
   every join, and the time budget enforced.

8. **Check.** `truetake check`, with the user's `TRUETAKE_DENY_PATTERN` exported. Then look at every contact
   sheet under `.truetake/check/sheets/` yourself: names, usernames, home paths, cut-off or wrapped lines,
   captions and explanation lines that are hard to read.

9. **Report.** `truetake report` writes `.truetake/REPORT.md`. Read it, then report to the user with the
   template below. `truetake build` reruns phases 6–9 in one go.

## Honesty rules

- The prompt is sent exactly as written. Add nothing to the system prompt; give no hint about the behaviour
  you want to show.
- Keep edit permission on (`acceptEdits`), so a refusal is the agent's own choice. For MCP tools, allow the
  read-only ones with `allowed_tools`; never allow write tools to make a take "work".
- Keep every take. Justify the chosen one in `--reason`.
- Agent text on screen is verbatim. The only changes are pacing, dropped Markdown markers, tool-call paths
  made relative, and the home directory as `~`; the report lists each. Never edit, reorder or paraphrase
  agent text or tool output, and never retype it into a slide.
- Code cards come from `git show <commit>:<file>` at a pinned commit; the only allowed addition is a `…` row.
- Narration and captions describe what the chosen take shows. If the take differs from the plan, reword
  them to match the take.

## Privacy rules

- No legal names, usernames, home paths or private email addresses in any frame or in the narration.
  Names go in `TRUETAKE_DENY_PATTERN` (an environment variable, never a file). Allowed emails, such as a
  noreply address, go in `privacy.allow_emails`.
- API keys come from the environment (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) and are never written down.
- The work directory holds raw evidence with absolute paths. Share the video and `REPORT.md`, not
  `.truetake/`.

## Pitfalls

Details and fixes are in the repository's `docs/pitfalls.md`.

- Under `claude -p`, `acceptEdits` does not allow MCP tools: pass read-only MCP tools in `allowed_tools`
  (truetake turns that into `--allowedTools`). Check the report's denial count.
- A hook's `additionalContext` is not in a plain stream-json stream. truetake passes `--include-hook-events`
  and shows the `hook_response` text. Without it, capture the same text with a command (`hook.text_command`)
  and verify its ids against the hook's own state before trusting it.
- Always `--strict-mcp-config` and stdin from `/dev/null`; truetake does both.
- Without `--bare`, `claude -p` runs a project's hooks and MCP servers with no trust prompt. Only record in
  sandboxes you built. `agent.isolation: bare` excludes user-level context but needs `ANTHROPIC_API_KEY`.
- Commit the sandbox so every take starts from a clean `git status`; truetake refuses otherwise.
- A Read hook does not fire when the agent reads a file through Bash; pick the take that shows the claim.
- VHS and headless Chromium can fail silently on some systems; truetake renders with agg instead.
- Some TTS WAVs carry extra RIFF chunks (a C2PA manifest) that play as noise; truetake keeps only `data`.
- Pacing versus budget: real replies are long. `visual_seconds` sets the reply speed; above about 250
  characters per second nobody reads along, so give the scene more time or cut something.

## Report template

Report to the user in this shape, filled from `.truetake/REPORT.md` and what you saw:

```markdown
**Video:** <path>, <seconds> s (budget <budget> s), <voice>, <LUFS> LUFS. Checks: <passed/failed>.

**Takes**
- <scene>: <N> takes. Take <n> chosen because <reason>. Other takes: <one line each on what they did>.

**Choices I made** (anything not in the plan): <pacing chosen, narration rewording and why, captions
reworded to match the take, anything left out>.

**Checks**
- Privacy scan: <hits> over <sources> sources (deny pattern <set/not set>).
- Frames: <count> every <n> s; I looked at every contact sheet: <what I saw>.
- Code cards: verbatim at <commit>; cover check <bad>/<frames>.
- Audio: joins <worst dBFS>, loudness <LUFS>.

**Re-render:** `truetake build -c <path to truetake.yaml>`
```
