"""assemble: segments in order, one continuous narration track, one encode.

Audio rules (each one fixed a click or a level problem):
  * each clip gets 5 ms fades at both edges;
  * narration starts 0.2 s into its segment; a scene's card clip and main clip are 0.3 s apart;
  * every segment lasts max(narration, visual) + 0.5 s, rounded up to a whole frame, so audio and video
    stay in step to the sample;
  * the whole track is built in one filter graph and encoded to AAC once, so no encoder boundary falls on a
    join;
  * the track is normalised to the target loudness (default −16 LUFS) with ffmpeg's two-pass loudnorm;
  * afterwards a click test reads the decoded output around every join.
"""
from __future__ import annotations

import array
import json
import math
import re
import subprocess
from pathlib import Path

from . import state
from .config import Config
from .media import duration, ffmpeg, frames, need
from .narrate import clips

LEAD, GAP, TAIL = 0.2, 0.3, 0.5


class AssembleError(RuntimeError):
    pass


def plan(cfg: Config) -> list[dict]:
    out, t0 = [], 0.0
    for seg in cfg.segments:
        parts = []
        for role, path, text in clips(cfg, seg) if cfg.voice.engine != "none" else []:
            if not path.exists() or path.with_suffix(".txt").read_text(encoding="utf-8") != text:
                raise AssembleError(f"{seg.id}: narration clip for {role} is missing or stale; run `truetake narrate`")
            parts.append({"role": role, "path": str(path), "seconds": duration(path)})
        speech = sum(p["seconds"] for p in parts) + GAP * max(0, len(parts) - 1)
        if seg.kind == "scene":
            video = state.render_dir(cfg) / seg.id / "scene.mp4"
            if not video.exists():
                raise AssembleError(f"{seg.id}: not rendered; run `truetake render`")
            visual = duration(video)
            sched = state.read_json(state.render_dir(cfg) / seg.id / "schedule.json")
            card = sched.get("card")
            card_part = next((p for p in parts if p["role"] == "card"), None)
            if card and card_part and card["fade_start"] < LEAD + card_part["seconds"]:
                raise AssembleError(f"{seg.id}: the card fades at {card['fade_start']}s but its narration runs to "
                                    f"{LEAD + card_part['seconds']:.2f}s; run `truetake render` again (it fits the pacing "
                                    "to the narration) or shorten narration.card")
        else:
            video = state.render_dir(cfg) / seg.id / "still.png"
            if not video.exists():
                raise AssembleError(f"{seg.id}: not rendered; run `truetake render`")
            visual = 0.0
        d = frames(max(LEAD + speech, visual) + TAIL, cfg.fps)
        out.append({"id": seg.id, "kind": seg.kind, "start": round(t0, 3), "seconds": d, "visual": round(visual, 3),
                    "speech": round(speech, 3), "clips": parts, "source": str(video)})
        t0 += d
    return out


def _segment_videos(cfg: Config, segs: list[dict], work: Path) -> Path:
    lst = work / "segments.txt"
    lines = []
    for s in segs:
        out = work / f"seg-{s['id']}.mp4"
        common = ["-an", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-r", str(cfg.fps), "-pix_fmt", "yuv420p"]
        if s["kind"] == "scene":
            ffmpeg("-i", s["source"], "-vf", f"tpad=stop_mode=clone:stop_duration={s['seconds']},fps={cfg.fps},format=yuv420p",
                   "-t", f"{s['seconds']}", *common, str(out))
        else:
            ffmpeg("-loop", "1", "-framerate", str(cfg.fps), "-t", f"{s['seconds']}", "-i", s["source"],
                   "-vf", f"fps={cfg.fps},format=yuv420p", "-t", f"{s['seconds']}", *common, str(out))
        lines.append(f"file '{out.name}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    video = work / "video.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video))
    return video


def _narration_track(segs: list[dict], work: Path) -> Path:
    inputs, graph, k, outs = [], [], 0, []
    for i, s in enumerate(segs):
        names = []
        for c in s["clips"]:
            inputs += ["-i", c["path"]]
            fade_out = max(0.0, c["seconds"] - 0.005)
            graph.append(f"[{k}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                         f"afade=t=in:d=0.005,afade=t=out:st={fade_out:.3f}:d=0.005[p{k}]")
            names.append(f"p{k}")
            k += 1
        tail = f"adelay={int(LEAD * 1000)}:all=1,apad,atrim=end={s['seconds']},asetpts=PTS-STARTPTS[s{i}]"
        if not names:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=end={s['seconds']},aformat=sample_fmts=fltp[s{i}]")
        elif len(names) == 1:
            graph.append(f"[{names[0]}]{tail}")
        else:
            padded = [f"[{n}]apad=pad_dur={GAP}[g{n}]" for n in names[:-1]]
            graph += padded
            chain = "".join(f"[g{n}]" for n in names[:-1]) + f"[{names[-1]}]"
            graph.append(f"{chain}concat=n={len(names)}:v=0:a=1,{tail}")
        outs.append(f"[s{i}]")
    graph.append("".join(outs) + f"concat=n={len(outs)}:v=0:a=1[a]")
    raw = work / "narration-raw.wav"
    ffmpeg(*inputs, "-filter_complex", ";".join(graph), "-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000", str(raw))
    return raw


def _loudnorm(raw: Path, out: Path, target: float) -> dict:
    spec = f"loudnorm=I={target}:TP=-1.5:LRA=11"
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(raw), "-af", spec + ":print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:
        raise AssembleError("loudnorm measurement failed")
    meas = json.loads(m.group(0))
    if meas["input_i"] in ("-inf", "inf"):  # silent track: nothing to normalise
        ffmpeg("-i", str(raw), "-c:a", "pcm_s16le", str(out))
        return {"measured_input_lufs": None, "target_lufs": target}
    ffmpeg("-i", str(raw), "-af",
           f"{spec}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:measured_LRA={meas['input_lra']}:"
           f"measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true,aresample=48000",
           "-c:a", "pcm_s16le", "-ar", "48000", str(out))
    return {"measured_input_lufs": float(meas["input_i"]), "target_lufs": target}


def integrated_loudness(path: Path) -> float | None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "loudnorm=print_format=json",
                           "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:
        return None
    v = json.loads(m.group(0))["input_i"]
    return None if v in ("-inf", "inf") else float(v)


def click_test(video: Path, segs: list[dict]) -> dict:
    """Decode the final audio and read around every join. A join must be digital silence; so must the 0.2 s
    before each segment's first clip. Returns peaks in dBFS."""
    rate = 48000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "s16le", "-"], capture_output=True).stdout
    a = array.array("h", raw)

    def peak(t0: float, t1: float) -> float:
        i, j = max(0, int(t0 * rate)), min(len(a), int(t1 * rate))
        m = max((abs(x) for x in a[i:j]), default=0)
        return round(20 * math.log10(max(m, 1) / 32768), 1)

    joins = []
    for s in segs:
        b = s["start"]
        joins.append({"at": b, "segment": s["id"], "peak_around_join": peak(b - 0.1, b + 0.1),
                      "peak_before_speech": peak(b + 0.005, b + LEAD - 0.005)})
    worst = max([max(j["peak_around_join"], j["peak_before_speech"]) for j in joins] or [-90.3])
    return {"joins": joins, "worst_dbfs": worst, "pass": worst <= -60.0}


def assemble(cfg: Config, log=print) -> dict:
    need("ffmpeg", "Install ffmpeg.")
    work = state.assemble_dir(cfg)
    work.mkdir(parents=True, exist_ok=True)
    segs = plan(cfg)
    total = sum(s["seconds"] for s in segs)
    if total > cfg.budget_seconds:
        raise AssembleError(f"the video would be {total:.1f}s, over the {cfg.budget_seconds:.0f}s budget. "
                            "Lower a scene's visual_seconds (the agent text streams faster) or shorten narration.")
    video = _segment_videos(cfg, segs, work)
    raw = _narration_track(segs, work)
    norm = work / "narration.wav"
    loud = _loudnorm(raw, norm, cfg.voice.loudness_lufs) if cfg.voice.engine != "none" else {"measured_input_lufs": None}
    if cfg.voice.engine == "none":
        norm = raw
    cfg.output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(video), "-i", str(norm), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-ar", "48000", "-shortest", "-movflags", "+faststart", str(cfg.output))
    clicks = click_test(cfg.output, segs)
    result = {"output": str(cfg.output), "seconds": round(duration(cfg.output), 3), "budget_seconds": cfg.budget_seconds,
              "segments": segs, "loudness": {**loud, "output_lufs": integrated_loudness(cfg.output)}, "click_test": clicks}
    state.write_json(work / "assemble.json", result)
    log(f"  wrote {cfg.output.name}: {result['seconds']:.1f}s (budget {cfg.budget_seconds:.0f}s), "
        f"loudness {result['loudness']['output_lufs']} LUFS, joins worst {clicks['worst_dbfs']} dBFS")
    if not clicks["pass"]:
        raise AssembleError(f"click test failed: a join peaks at {clicks['worst_dbfs']} dBFS (must be ≤ −60)")
    return result
