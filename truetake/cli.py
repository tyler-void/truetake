"""truetake command line: init, record, select, render, narrate, assemble, check, report (and build)."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import __version__, state, takes
from .config import ConfigError, load

TEMPLATE = """\
# truetake.yaml: one file drives every step. Relative paths resolve from this file's directory.
version: 1
title: My demo
output: demo.mp4
work_dir: .truetake          # takes (evidence), clips, renders, checks. Keep it OUTSIDE the sandbox.
budget_seconds: 90
label: synthetic demo repository   # strip under the terminal; "" hides it

video: {width: 1920, height: 1080, fps: 24}
terminal: {cols: 140, rows: 34, font_family: JetBrains Mono, font_size: 18}

agent:
  command: claude
  isolation: project         # project: -p with project settings only; bare: --bare (needs ANTHROPIC_API_KEY)
  permission_mode: acceptEdits

voice:
  engine: kokoro             # kokoro (local) | gemini (needs $GEMINI_API_KEY) | none
  name: af_heart
  loudness_lufs: -16

privacy:
  allow_emails: []           # e.g. ["*@users.noreply.github.com"]
  frame_every: 3

# slides: {pdf: slides.pdf}

segments:
  - id: intro
    kind: title
    title: My project
    subtitle: What this demo shows
    narration: One or two sentences that say what the viewer is about to see.

  - id: main
    kind: scene
    cwd: /tmp/my-sandbox     # a committed git repository you control; takes reset it between runs
    prompt: A natural request, exactly as a user would type it.
    takes: 3
    visual_seconds: 35
    # allowed_tools: ["mcp__server__read_only_tool"]   # MCP tools need this under claude -p
    # mcp_config: mcp.json
    # hook: {after_read: src/app.py}
    card:
      repo: /tmp/my-sandbox
      commit: 0000000000000000000000000000000000000000   # git -C <repo> rev-parse HEAD
      file: src/app.py
      lines: [[1, 12]]
      explain: One plain-English line about this code.
    captions:
      - {text: "1  The agent reads app.py", at: "@read:src/app.py"}
      - {text: "2  It answers", at: "@reply"}
    narration:
      card: Spoken while the code card is on screen.
      main: Spoken while the agent works. Describe only what the chosen take shows.
"""


def _cfg(args):
    return load(args.config)


def cmd_init(args) -> int:
    target = Path(args.dir) / "truetake.yaml"
    if target.exists() and not args.force:
        print(f"{target} exists (use --force to overwrite)", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def cmd_record(args) -> int:
    from .record import record

    cfg = _cfg(args)
    for seg in cfg.scenes():
        if args.scene and seg.id not in args.scene:
            continue
        print(f"record {seg.id}")
        record(cfg, seg, args.takes)
    return 0


def cmd_select(args) -> int:
    cfg = _cfg(args)
    seg = cfg.segment(args.scene)
    nums = state.recorded_takes(cfg, seg.id)
    if not nums:
        print(f"no takes recorded for {seg.id}", file=sys.stderr)
        return 1
    if args.list or args.take is None:
        sel = state.selection(cfg, seg.id)
        for n in nums:
            s = takes.summary(takes.parse(state.take_path(cfg, seg.id, n, ".jsonl")))
            meta = state.read_json(state.take_path(cfg, seg.id, n, ".meta.json"))
            mark = " (chosen)" if sel and int(sel["take"]) == n else ""
            print(f"take {n}{mark}: exit {meta['exit_code']}, {len(s['tool_calls'])} tool calls "
                  f"({', '.join(s['tool_calls'])}), hook context {s['hooks_with_context']}, reply {s['reply_chars']} chars, "
                  f"files changed: {', '.join(meta['files_changed']) or 'none'}")
            print(f"    first line: {s['final_text_start']}")
        return 0
    if args.take not in nums:
        print(f"take {args.take} does not exist for {seg.id} (have {nums})", file=sys.stderr)
        return 1
    if not args.reason or len(args.reason.strip()) < 10:
        print("--reason is required: say why this take, in a sentence the report can quote", file=sys.stderr)
        return 1
    state.write_json(state.takes_dir(cfg, seg.id) / "selected.json", {
        "take": args.take, "reason": args.reason.strip(), "of": nums,
        "selected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    print(f"{seg.id}: take {args.take} selected")
    return 0


def cmd_render(args) -> int:
    from .render import render

    render(_cfg(args), args.segment)
    return 0


def cmd_narrate(args) -> int:
    from .narrate import narrate

    narrate(_cfg(args), args.segment)
    return 0


def cmd_assemble(args) -> int:
    from .assemble import assemble

    assemble(_cfg(args))
    return 0


def cmd_check(args) -> int:
    from .check import check

    return 0 if check(_cfg(args))["pass"] else 1


def cmd_report(args) -> int:
    from .report import report

    report(_cfg(args))
    return 0


def cmd_build(args) -> int:
    from .assemble import assemble
    from .check import check
    from .narrate import narrate
    from .render import render
    from .report import report

    cfg = _cfg(args)
    print("narrate"); narrate(cfg)
    print("render"); render(cfg)
    print("assemble"); assemble(cfg)
    print("check"); ok = check(cfg)["pass"]
    print("report"); report(cfg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="truetake", description="Real agent takes, turned into honest demo videos.")
    p.add_argument("--version", action="version", version=f"truetake {__version__}")
    p.add_argument("-c", "--config", default="truetake.yaml", help="path to truetake.yaml (default: ./truetake.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write an annotated truetake.yaml")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("record", help="record fresh takes of each scene (every take is kept)")
    s.add_argument("--scene", action="append", help="only this scene (repeatable)")
    s.add_argument("--takes", type=int, help="override the number of takes")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("select", help="list a scene's takes, or choose one with a reason")
    s.add_argument("--scene", required=True)
    s.add_argument("--take", type=int)
    s.add_argument("--reason")
    s.add_argument("--list", action="store_true")
    s.set_defaults(fn=cmd_select)

    for name, fn, h in (("render", cmd_render, "casts, code cards, captions and still segments"),
                        ("narrate", cmd_narrate, "one TTS clip per narration string")):
        s = sub.add_parser(name, help=h)
        s.add_argument("--segment", action="append", help="only this segment (repeatable)")
        s.set_defaults(fn=fn)
    for name, fn, h in (("assemble", cmd_assemble, "segments, one audio track, one encode"),
                        ("check", cmd_check, "privacy gate, frames and contact sheets, audio and card checks"),
                        ("report", cmd_report, "write REPORT.md"),
                        ("build", cmd_build, "narrate, render, assemble, check, report")):
        sub.add_parser(name, help=h).set_defaults(fn=fn)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"truetake.yaml: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # every failure is a message, not a traceback, unless TRUETAKE_DEBUG is set
        import os

        if os.environ.get("TRUETAKE_DEBUG"):
            raise
        print(f"truetake {args.cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
