# Privacy

A demo video is easy to share and impossible to unshare. truetake treats privacy as a gate, not a
suggestion: `render` refuses to draw anything that fails the scan, and `check` fails the build.

## What is scanned

Everything that reaches the screen or the voice:

- the terminal text of every rendered scene, including the hook's added context and the cast header;
- captions, code-card excerpts, card titles and explanation lines;
- narration: the strings in `truetake.yaml` and the text each clip actually speaks (the `.txt` beside it);
- title cards and the label strip;
- slide text, when `pdftotext` (poppler) is installed; otherwise `check` says it was not scanned.

`render` scans the configured texts first, then each scene's text before its video exists. `check` scans
everything again against the finished build.

## What counts as a hit

| Kind | Matches |
|---|---|
| `deny` | Your own regular expression in `$TRUETAKE_DENY_PATTERN`, matched case-insensitively |
| `home` | Your home directory, or any Linux, macOS or Windows user-folder path |
| `username` | The login name of the account running truetake, as a whole word |
| `email` | Any email address not allowed by `privacy.allow_emails` (glob patterns, e.g. `*@users.noreply.github.com`) |

Hits are reported with the match replaced by `[kind]`, so a report never repeats what it caught.

### The deny pattern

Put the names you never want published (legal names, client names, internal code names) in an
environment variable, not in a file:

```bash
export TRUETAKE_DENY_PATTERN='\b(?:first\s+last|codename-x|client\s+corp)\b'
```

It is a Python regular expression. Nothing truetake writes contains it; the report says only whether it
was set. If it is not set, `check` still passes the other scans but prints a note that names were not
scanned.

## Frames for a human

No scan reads pixels. `check` exports a frame every `privacy.frame_every` seconds (3 by default) and tiles
them into contact sheets under `.truetake/check/sheets/`. Look at every one before you share: slides,
wrapped lines, anything a scan cannot see.

## Keys

- Gemini TTS reads `$GEMINI_API_KEY` from the environment. truetake never writes it to disk or into a
  report.
- `agent.isolation: bare` needs `$ANTHROPIC_API_KEY` in the environment (bare mode does not use a
  subscription login).

## Evidence stays local

The work directory (`.truetake/`) holds raw takes. They contain absolute paths, session ids and whatever the
agent read. Keep the work directory out of any repository you publish (the default `.gitignore` of this
repository already ignores it) and share the video and `REPORT.md`, which have home paths written as `~`.

Record in a sandbox under a neutral path such as `/tmp/<name>`: tool calls and the cast header show the
sandbox path, and a neutral one needs no rewriting. Keep the work directory itself out of `/tmp` if you
want the evidence to survive a reboot.
