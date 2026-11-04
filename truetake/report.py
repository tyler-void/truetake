"""report: REPORT.md, the record a reviewer reads before trusting the video.

It lists every take (kept, not just the chosen one), why the chosen take was chosen, every change made to
the agent's text, what the agent was given to run with, the cards, captions and narration, and the checks.
Home paths are written as ~ throughout.
"""
from __future__ import annotations

from pathlib import Path

from . import state, takes
from .cast import scrub_home
from .config import Config
from .record import command_line


def _rel(cfg: Config, p: str | Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(cfg.root))
    except ValueError:
        return scrub_home(str(p))


def report(cfg: Config, log=print) -> Path:
    L: list[str] = [f"# {cfg.title}: demo video report", ""]
    asm = state.assemble_dir(cfg) / "assemble.json"
    chk = state.check_dir(cfg) / "check.json"
    a = state.read_json(asm) if asm.exists() else None
    c = state.read_json(chk) if chk.exists() else None
    if a:
        L += [f"**Output:** `{_rel(cfg, a['output'])}`, {a['seconds']:.1f} s (budget {a['budget_seconds']:.0f} s), "
              f"{cfg.width}×{cfg.height} H.264 {cfg.fps} fps + AAC. Narration: {cfg.voice.engine} "
              f"`{cfg.voice.name}`, loudness {a['loudness'].get('output_lufs')} LUFS (target {cfg.voice.loudness_lufs})."]
    L += [f"**Checks:** {'passed' if c and c['pass'] else 'NOT PASSED' if c else 'not run'}.", "",
          f"**Re-render:** `truetake build -c {_rel(cfg, cfg.path)}` (narrate, render, assemble, check, report).", ""]

    L += ["## Segments", "", "| # | Segment | Kind | Starts | Lasts | Visual | Speech |", "|---|---|---|---|---|---|---|"]
    for i, s in enumerate(a["segments"] if a else [], 1):
        L.append(f"| {i} | {s['id']} | {s['kind']} | {s['start']:.2f} | {s['seconds']:.2f} | {s['visual']:.2f} | {s['speech']:.2f} |")
    L.append("")

    for seg in cfg.scenes():
        L += [f"## Scene `{seg.id}`" + (f" (shown in pair `{seg.in_pair}`)" if seg.in_pair else ""), "",
              f"**Prompt** (sent exactly as written, nothing added to the system prompt):", "", f"> {seg.prompt}", ""]
        for k, fu in enumerate(seg.followups, 2):
            L += [f"**Follow-up prompt {k}** (`--resume`):", "", f"> {fu}", ""]
        nums = state.recorded_takes(cfg, seg.id)
        sel = state.selection(cfg, seg.id)
        metas = {n: state.read_json(state.take_path(cfg, seg.id, n, ".meta.json")) for n in nums}
        imported = {n: m["imported"] for n, m in metas.items() if m.get("imported")}
        if imported:
            L += ["**Imported takes** (existing runs, not recorded by truetake; streams kept verbatim):", ""]
            for n, im in imported.items():
                streams = ", ".join(f"`{x['file']}` sha256 `{x['sha256'][:12]}…`" for x in im["streams"])
                tr = im.get("transcript")
                L.append(f"- take {n}: source **{im['source']}**, model `{im.get('model')}`, {im['invocations']} invocation(s); "
                         f"streams {streams}" + (f"; from the transcript (`{tr['sha256'][:12]}…`): {tr['pre_tool_use_rewrites']} "
                                                 f"PreToolUse rewrite(s), final usage of {tr.get('final_usage_requests', 0)} request(s)"
                                                 if tr else "; no transcript: usage annotations use the stream's provisional numbers"))
            L.append("")
        if nums and any(metas[n]["argv"] for n in nums):
            argvs = {n: metas[n]["argv"] for n in nums if metas[n]["argv"]}
            nums_rec = list(argvs)
            if all(a == argvs[nums_rec[0]] for a in argvs.values()):
                L += [f"**Agent command** (identical for all {len(nums_rec)} recorded takes):", "",
                      "```", scrub_home(command_line(argvs[nums_rec[0]])), "```", ""]
            else:
                L += ["**Agent command** (the takes differ):", ""]
                for n, a in argvs.items():
                    L += [f"take {n}:", "```", scrub_home(command_line(a)), "```"]
                L.append("")
        L += ["| Take | Exit | Seconds | Tool calls | Hook context | Reply chars | Files changed | Denials | First line of the final reply |",
              "|---|---|---|---|---|---|---|---|---|"]
        for n in nums:
            meta = state.read_json(state.take_path(cfg, seg.id, n, ".meta.json"))
            s = takes.summary(takes.parse(state.take_path(cfg, seg.id, n, ".jsonl"), prompts=meta.get("prompts")))
            chosen = " **(chosen)**" if sel and int(sel["take"]) == n else ""
            L.append(f"| {n}{chosen} | {meta['exit_code']} | {meta['seconds']} | {len(s['tool_calls'])} | "
                     f"{s['hooks_with_context']} | {s['reply_chars']} | {', '.join(meta['files_changed']) or 'none'} | "
                     f"{s['permission_denials']} | {scrub_home(s['final_text_start']).replace('|', '/')} |")
        L.append("")
        if sel:
            L += [f"**Chosen take {sel['take']}:** {sel['reason']}", ""]
            s = takes.summary(takes.parse(state.take_path(cfg, seg.id, int(sel["take"]), ".jsonl")))
            loaded = [f"model `{s['model']}`", f"Claude Code {s['claude_code_version']}", f"permission mode `{s['permission_mode']}`",
                      f"MCP servers: {', '.join(s['mcp_servers']) or 'none'}", f"plugins: {', '.join(p for p in s['plugins'] if p) or 'none'}"]
            L += ["**What the agent ran with:** " + "; ".join(loaded) + ".", ""]
            meta = state.read_json(state.take_path(cfg, seg.id, int(sel["take"]), ".meta.json"))
            if meta.get("user_memory_file_present") and meta.get("isolation") != "bare":
                L += ["> A user-level CLAUDE.md existed when this take was recorded and may have been loaded. Record "
                      "with `agent.isolation: bare` to exclude it.", ""]
        else:
            L += ["**No take selected yet.**", ""]
        sch_p = state.render_dir(cfg) / seg.id / "schedule.json"
        if sch_p.exists():
            sch = state.read_json(sch_p)
            p, t = sch["pacing"], sch["transforms"]
            L += ["**Changes to the agent's text** (the only ones allowed):", "",
                  f"- pacing: prompt typed at {p['prompt_cps']:.0f} chars/s; replies streamed at {p['reply_cps']:.0f} chars/s; "
                  f"{p['after_tool']} s after each tool call; the first tool call held until {p['first_tool_at']:.2f} s; "
                  f"{p['after_hook']} s after a hook block; {p['end_hold']} s on the finished screen",
                  f"- Markdown markers dropped: {t['markdown_markers_dropped']}",
                  f"- tool-call paths made relative to the sandbox: {t['paths_relativized']}",
                  f"- home directory written as ~: {t['home_replaced']}",
                  *([f"- working directory shown in the header as `{t['cwd_display']}`, and written that way inside "
                     f"{t.get('cwd_in_commands', 0)} command(s)"] if t.get("cwd_display") else []),
                  *([f"- annotations added from the stream (dim `⎿`/`·` lines, not agent text): "
                     + ", ".join(k for k, v in seg.annotate.items() if v)] if any(seg.annotate.values()) else []),
                  f"- hook text source: {sch['hook_source']}"
                  + (" (the stream's hook_response)" if sch["hook_source"] == "stream" else
                     " (captured with hook.text_command, not from the stream)" if sch["hook_source"] == "command" else ""),
                  f"- widest line {sch['widest_line']} of {sch['cols']} columns (no terminal wrap)", ""]
            if card := sch.get("card"):
                spans = ", ".join(f"{x}–{y}" if x != y else str(x) for x, y in card["lines"])
                L += ["**Code card:**", "",
                      f"- `{card['file']}` lines {spans} at `{card['commit']}`, verbatim (checked against `git show`)",
                      f"- explanation line: “{card['explain']}”",
                      f"- on screen 0.00–{card['gone']:.2f} s of the scene (fade from {card['fade_start']:.2f} s); "
                      f"text reaches its area at {card['text_reaches']} s ({card['reached_by']})"
                      + (f"; its narration ends at {card['narration_ends']:.2f} s" if card.get("narration_ends") else ""),
                      f"- cover check: {card['cover_check']['frames_with_text_under_card']} of "
                      f"{card['cover_check']['frames_checked']} frames show text under the visible card", ""]
            if sch["captions"]:
                L += ["**Captions** (each timed to its line in the cast):", ""]
                L += [f"- {cap['in']:.2f} s: “{cap['text']}” (trigger `{cap['at']}`)" for cap in sch["captions"]]
                L.append("")
        if seg.card_narration or seg.narration:
            L += ["**Narration** (check it against the take above; it must not claim more than the take shows):", ""]
            if seg.card_narration:
                L.append(f"- while the card is up: “{seg.card_narration}”")
            if seg.narration:
                L.append(f"- then: “{seg.narration}”")
            L.append("")

    others = [s for s in cfg.segments if s.kind != "scene" and s.narration]
    if others:
        L += ["## Other narration", ""] + [f"- `{s.id}`: “{s.narration}”" for s in others] + [""]

    if c:
        L += ["## Checks", ""]
        L.append(f"- privacy scan: {c['privacy_hits']} hit(s) over {len(c['scanned_sources'])} text sources "
                 f"(deny pattern {'set' if c['scanner']['deny_pattern_set'] else 'NOT set'}, home, username, email)")
        if "frames" in c:
            L.append(f"- frames: {c['frames']['count']}, one every {c['frames']['every_seconds']:g} s, "
                     f"{len(c['frames']['sheets'])} contact sheet(s) in `{_rel(cfg, state.check_dir(cfg) / 'sheets')}`; "
                     "look at every one before sharing")
        for sid, cov in c.get("cover_checks", {}).items():
            L.append(f"- card cover ({sid}): {cov['frames_with_text_under_card']} bad of {cov['frames_checked']} frames")
        if "click_test" in c:
            L.append(f"- joins: worst {c['click_test']['worst_dbfs']} dBFS ({'pass' if c['click_test']['pass'] else 'FAIL'})")
        if "loudness" in c:
            L.append(f"- loudness: {c['loudness'].get('output_lufs')} LUFS (target {cfg.voice.loudness_lufs})")
        L += [f"- note: {n}" for n in c.get("notes", [])]
        L += [f"- FAIL: {f}" for f in c.get("failures", [])]
        L.append("")

    out = cfg.work_dir / "REPORT.md"
    out.write_text("\n".join(L), encoding="utf-8")
    log(f"  wrote {_rel(cfg, out)}")
    return out
