"""check: the gate before a video is shared. Fails (exit code 1) on any of:

  * a privacy hit in anything shown or spoken: terminal text, hook text, captions, card excerpts and their
    explanation lines, narration, titles, the label, and slide text (when pdftotext is available);
  * a video over its time budget;
  * a frame with text under a visible code card (from render's cover check);
  * a click at a join (from assemble's click test), or loudness more than 2 LU from the target.

It also exports a frame every `privacy.frame_every` seconds plus contact sheets, for the human look that no
scan replaces.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import state
from .config import Config
from .media import duration, ffmpeg
from .scan import Scanner


def _texts(cfg: Config) -> tuple[dict[str, str], list[str]]:
    texts: dict[str, str] = {"label": cfg.label}
    notes: list[str] = []
    for seg in cfg.segments:
        texts[f"{seg.id}/narration"] = "\n".join(x for x in (seg.card_narration, seg.narration) if x)
        if seg.kind == "title":
            texts[f"{seg.id}/title"] = f"{seg.title}\n{seg.subtitle}"
        if seg.kind == "scene":
            shown = state.render_dir(cfg) / seg.id / "shown-text.json"
            if shown.exists():
                texts.update(state.read_json(shown))
            else:
                notes.append(f"{seg.id}: not rendered, terminal text not scanned")
            fb = state.render_dir(cfg) / seg.id / "hook-fallback.txt"
            if fb.exists():
                texts[f"{seg.id}/hook-fallback"] = fb.read_text(encoding="utf-8")
        if seg.kind == "slide":
            if shutil.which("pdftotext"):
                out = subprocess.run(["pdftotext", "-f", str(seg.page), "-l", str(seg.page), "-layout",
                                      str(cfg.slides_pdf), "-"], capture_output=True, text=True).stdout
                texts[f"{seg.id}/slide-text"] = out
            else:
                notes.append(f"{seg.id}: pdftotext not found, slide text not scanned (look at the frames)")
    for p in sorted(state.tts_dir(cfg).glob("*.txt")) if state.tts_dir(cfg).exists() else []:
        texts[f"tts/{p.name}"] = p.read_text(encoding="utf-8")  # what each clip actually speaks
    return texts, notes


def frames(cfg: Config, video: Path, every: float) -> tuple[list[Path], list[Path]]:
    d = state.check_dir(cfg)
    fdir, sdir = d / "frames", d / "sheets"
    for x in (fdir, sdir):
        if x.exists():
            shutil.rmtree(x)
        x.mkdir(parents=True)
    ffmpeg("-i", str(video), "-vf", f"fps=1/{every}", str(fdir / "frame-%04d.png"))
    shots = sorted(fdir.glob("frame-*.png"))
    sheets = []
    for i in range(0, len(shots), 16):
        out = sdir / f"sheet-{i // 16 + 1:02d}.png"
        ffmpeg("-start_number", str(i + 1), "-i", str(fdir / "frame-%04d.png"), "-frames:v", "1",
               "-vf", "scale=480:-2,tile=4x4:padding=4:color=gray", str(out))
        sheets.append(out)
    return shots, sheets


def check(cfg: Config, log=print) -> dict:
    scanner = Scanner.from_env(cfg.privacy.allow_emails)
    texts, notes = _texts(cfg)
    hits = [h for src, text in texts.items() for h in scanner.scan(src, text)]
    failures: list[str] = [f"privacy: {h.source}:{h.line} {h.kind}: {h.snippet}" for h in hits]
    if not scanner.deny:
        notes.append("TRUETAKE_DENY_PATTERN is not set: names were not scanned")

    result: dict = {"scanned_sources": sorted(texts), "scanner": scanner.describe(), "privacy_hits": len(hits), "notes": notes}
    video = cfg.output
    if not video.exists():
        failures.append(f"{video.name} does not exist; run `truetake assemble`")
    else:
        secs = duration(video)
        result["seconds"] = round(secs, 3)
        if secs > cfg.budget_seconds:
            failures.append(f"duration {secs:.1f}s is over the {cfg.budget_seconds:.0f}s budget")
        shots, sheets = frames(cfg, video, cfg.privacy.frame_every)
        result["frames"] = {"every_seconds": cfg.privacy.frame_every, "count": len(shots),
                            "dir": str(shots[0].parent) if shots else None, "sheets": [str(s) for s in sheets]}

    covers = {}
    for seg in cfg.scenes():
        p = state.render_dir(cfg) / seg.id / "schedule.json"
        if p.exists() and (card := state.read_json(p).get("card")):
            covers[seg.id] = card["cover_check"]
            if card["cover_check"]["frames_with_text_under_card"]:
                failures.append(f"{seg.id}: text under the visible card in {card['cover_check']['frames_with_text_under_card']} frame(s)")
    result["cover_checks"] = covers

    asm = state.assemble_dir(cfg) / "assemble.json"
    if asm.exists():
        a = state.read_json(asm)
        result["click_test"] = {"worst_dbfs": a["click_test"]["worst_dbfs"], "pass": a["click_test"]["pass"]}
        result["loudness"] = a["loudness"]
        if not a["click_test"]["pass"]:
            failures.append(f"click test: a join peaks at {a['click_test']['worst_dbfs']} dBFS")
        out_lufs = a["loudness"].get("output_lufs")
        if cfg.voice.engine != "none" and out_lufs is not None and abs(out_lufs - cfg.voice.loudness_lufs) > 2.0:
            failures.append(f"loudness {out_lufs} LUFS is more than 2 LU from the {cfg.voice.loudness_lufs} target")
    else:
        failures.append("assemble.json missing; run `truetake assemble`")

    result["failures"] = failures
    result["pass"] = not failures
    state.write_json(state.check_dir(cfg) / "check.json", result)
    for n in notes:
        log(f"  note: {n}")
    for f in failures:
        log(f"  FAIL {f}")
    if "frames" in result:
        log(f"  {result['frames']['count']} frames every {cfg.privacy.frame_every:g}s for review: "
            f"{len(result['frames']['sheets'])} contact sheet(s) in {state.check_dir(cfg) / 'sheets'}")
    log(f"  check {'passed' if result['pass'] else 'FAILED'}: {len(texts)} text sources scanned, {len(hits)} privacy hit(s)")
    return result
