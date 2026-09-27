"""render: turn the selected takes into terminal videos with a code card and step captions, and prepare the
still segments (title cards, slides).

Pipeline per scene: take → asciicast (cast.py) → GIF (agg) → MP4 on a WxH canvas → card and captions
composited with ffmpeg. The card's exit is computed by replaying the cast (screen.py); afterwards every frame
of the terminal-only video is read inside the card's rectangle to prove no text was under the visible card.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import cards, cast as castmod, fonts, screen, state, takes, tts
from .config import Config, ConfigError, Segment
from .media import duration, ffmpeg, need, run
from .scan import Scanner

# agg palette: background, foreground, then the 16 ANSI colours (Catppuccin Mocha)
MOCHA = ("1e1e2e,cdd6f4,45475a,f38ba8,a6e3a1,f9e2af,89b4fa,f5c2e7,94e2d5,bac2de,"
         "585b70,f38ba8,a6e3a1,f9e2af,89b4fa,f5c2e7,94e2d5,a6adc8")
BG = np.array([0x1E, 0x1E, 0x2E])
FADE, GAP = 0.3, 0.05  # the card fades over FADE seconds and is gone GAP seconds before text reaches it
# agg groups events into frames and the GIF→MP4 step resamples to the output rate, so a character can appear
# up to about a frame before its cast timestamp. The card leaves two frames on top of GAP; the cover check,
# which reads the rendered frames, is what proves the margin was enough.
FRAME_MARGIN = 2
CAPTION_FADE_IN, CAPTION_FADE_OUT = 1.0, 0.5
AGG_LAST_FRAME = 0.5


class RenderError(RuntimeError):
    pass


@dataclass
class Geometry:
    cell_w: float
    cell_h: float
    origin_x: float
    origin_y: float
    gif_w: int
    gif_h: int


@dataclass
class Layout:
    width: int
    height: int
    top: int
    term_x: int
    term_y: int
    term_w: int
    term_h: int
    sx: float
    sy: float
    geo: Geometry

    def cell_rect(self, row: int, col: int) -> tuple[float, float, float, float]:
        x = self.term_x + (self.geo.origin_x + self.geo.cell_w * col) * self.sx
        y = self.term_y + (self.geo.origin_y + self.geo.cell_h * row) * self.sy
        return x, y, x + self.geo.cell_w * self.sx, y + self.geo.cell_h * self.sy

    @property
    def scale(self) -> float:
        return self.height / 1080


def agg(cfg: Config, cast_path: Path, gif: Path) -> None:
    need("agg", "Install agg: cargo install --git https://github.com/asciinema/agg")
    t = cfg.terminal
    run(["agg", "--font-family", t.font_family, "--font-size", str(t.font_size), "--line-height", str(t.line_height),
         "--theme", MOCHA, "--fps-cap", str(cfg.fps), "--idle-time-limit", "86400",
         "--last-frame-duration", str(AGG_LAST_FRAME), "--quiet", str(cast_path), str(gif)])


def check_font(family: str) -> None:
    """agg finds fonts by family name among the system fonts; say so plainly when the family is missing."""
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return  # no fontconfig to ask; agg will report it
    families = {f.strip() for line in out.splitlines() for f in line.split(",")}
    if family not in families:
        close = sorted(f for f in families if family.split()[0].lower().replace(" ", "") in f.lower().replace(" ", ""))
        hint = f" Installed families that look close: {', '.join(close[:5])}." if close else ""
        raise RenderError(f"font family {family!r} is not installed (fc-list). Install it or set terminal.font_family.{hint}")


def calibrate(cfg: Config) -> Geometry:
    """Measure agg's cell size and origin by rendering a marker in each corner of an empty screen."""
    t = cfg.terminal
    check_font(t.font_family)
    key = hashlib.sha256(json.dumps([t.font_family, t.font_size, t.line_height, t.cols, t.rows]).encode()).hexdigest()[:12]
    cache = state.render_dir(cfg) / f"calibration-{key}.json"
    if cache.exists():
        return Geometry(**state.read_json(cache))
    d = state.render_dir(cfg) / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    blank = " " * (t.cols - 2)
    rows = ["█" + blank + "█"] + [""] * (t.rows - 2) + ["█" + blank + "█"]
    c = d / "grid.cast"
    c.write_text(json.dumps({"version": 2, "width": t.cols, "height": t.rows}) + "\n"
                 + json.dumps([0.0, "o", "\r\n".join(rows)], ensure_ascii=False) + "\n"
                 + json.dumps([0.5, "o", ""]) + "\n", encoding="utf-8")
    gif = d / "grid.gif"
    agg(cfg, c, gif)
    im = np.asarray(Image.open(gif).convert("RGB")).astype(int)
    h, w, _ = im.shape
    ink = np.abs(im - BG).sum(axis=2) > 60
    ys, xs = np.nonzero(ink)
    if xs.size == 0:
        raise RenderError("calibration frame is empty: is the font installed? (fc-list | grep -i mono)")
    left, right, top, bottom = xs < w / 2, xs > w / 2, ys < h / 2, ys > h / 2
    tl_x, tl_y = xs[left & top].min(), ys[left & top].min()
    tr_x = xs[right & top].min()
    bl_y = ys[left & bottom].min()
    geo = Geometry(cell_w=(tr_x - tl_x) / (t.cols - 1), cell_h=(bl_y - tl_y) / (t.rows - 1),
                   origin_x=float(tl_x), origin_y=float(tl_y), gif_w=w, gif_h=h)
    state.write_json(cache, asdict(geo))
    return geo


def layout(cfg: Config, geo: Geometry) -> Layout:
    s = cfg.height / 1080
    top, bottom, margin = round(116 * s), round(58 * s), round(24 * s)
    avail_w, avail_h = cfg.width - 2 * margin, cfg.height - top - bottom
    k = min(avail_w / geo.gif_w, avail_h / geo.gif_h)
    tw, th = int(geo.gif_w * k) // 2 * 2, int(geo.gif_h * k) // 2 * 2
    return Layout(width=cfg.width, height=cfg.height, top=top, term_x=(cfg.width - tw) // 2, term_y=top,
                  term_w=tw, term_h=th, sx=tw / geo.gif_w, sy=th / geo.gif_h, geo=geo)


def _gate(scanner: Scanner, texts: dict[str, str]) -> None:
    hits = [h for src, text in texts.items() for h in scanner.scan(src, text)]
    if hits:
        lines = "\n".join(f"  {h.source}:{h.line} {h.kind}: {h.snippet}" for h in hits[:20])
        raise RenderError(f"privacy gate: {len(hits)} hit(s) before render; nothing was rendered\n{lines}")


def selected_take(cfg: Config, scene: Segment) -> tuple[int, takes.Take, dict]:
    sel = state.selection(cfg, scene.id)
    if not sel:
        raise RenderError(f"scene {scene.id}: no take selected (truetake select --scene {scene.id} --take N --reason ...)")
    n = int(sel["take"])
    meta = state.read_json(state.take_path(cfg, scene.id, n, ".meta.json"))
    if meta["prompt"] != scene.prompt:
        raise RenderError(f"scene {scene.id}: the prompt in truetake.yaml changed after take {n} was recorded; "
                          "record new takes, or restore the prompt the take was recorded with")
    return n, takes.parse(state.take_path(cfg, scene.id, n, ".jsonl")), meta


def card_clip(cfg: Config, scene: Segment) -> Path | None:
    p = state.tts_dir(cfg) / f"{scene.id}.card.wav"
    return p if scene.card_narration and tts.up_to_date(p, scene.card_narration) else None


def render_scene(cfg: Config, scene: Segment, lay: Layout, scanner: Scanner, log=print) -> dict:
    out = state.render_dir(cfg) / scene.id
    out.mkdir(parents=True, exist_ok=True)
    n, take, meta = selected_take(cfg, scene)
    t = cfg.terminal

    hook_fallback, hook_source = None, "stream" if take.hook_events_seen else "none"
    if scene.hook and scene.hook.text_command and not take.hook_events_seen:
        proc = subprocess.run(scene.hook.text_command, shell=True, cwd=scene.cwd, capture_output=True, text=True)
        hook_fallback, hook_source = proc.stdout.rstrip("\n"), "command"
        (out / "hook-fallback.txt").write_text(hook_fallback, encoding="utf-8")

    card = None
    if scene.card:
        cd = scene.card
        card = cards.render_card(repo=cd.repo, commit=cd.commit, file=cd.file, ranges=cd.lines, explain=cd.explain,
                                 out=out / "card.png", scale=lay.scale, fonts_cfg=cfg.fonts, mono_family=t.font_family)

    clip = card_clip(cfg, scene)
    card_speech_end = (0.2 + duration(clip)) if clip else 0.0
    first_tool_at = (card_speech_end + 0.3) if clip else (scene.card_hold if card else 0.0)
    pacing = castmod.Pacing(first_tool_at=first_tool_at, hook_delay=2.5 if scene.captions else 0.9)
    c = castmod.fit(take, scene.prompt, str(scene.cwd), scene.visual_seconds, cols=t.cols, rows=t.rows,
                    label=cfg.label, pacing=pacing,
                    hook_path=scene.hook.after_read if scene.hook else None, hook_fallback=hook_fallback)
    if c.widest > t.cols:
        raise RenderError(f"scene {scene.id}: a line is {c.widest} columns wide, the terminal has {t.cols}; raise terminal.cols")
    cast_path = out / "scene.cast"
    castmod.write(c, cast_path)
    _, events = castmod.read_events(cast_path)

    # Privacy gate, before any video exists: everything this scene will show.
    shown = {f"{scene.id}/terminal": "\n".join(screen.screen_text(events, t.rows, t.cols)),
             f"{scene.id}/captions": "\n".join(cap.text for cap in scene.captions)}
    if card:
        shown[f"{scene.id}/card"] = card.title + "\n" + scene.card.explain + "\n" + "\n".join(txt for _, txt in card.rows)
    _gate(scanner, shown)

    gif = out / "scene.gif"
    agg(cfg, cast_path, gif)
    label_file = out / "label.txt"
    label_file.write_text(cfg.label, encoding="utf-8")
    mono = fonts.resolve(cfg.fonts, "mono", t.font_family)
    term_mp4 = out / "terminal.mp4"
    vf = (f"fps={cfg.fps},scale={lay.term_w}:{lay.term_h}:flags=lanczos,"
          f"pad={cfg.width}:{cfg.height}:{lay.term_x}:{lay.term_y}:color=0x1e1e2e")
    if cfg.label:
        vf += (f",drawtext=fontfile='{mono}':textfile='{label_file}':fontsize={round(26 * lay.scale)}:"
               f"fontcolor=0xa6adc8:x=(w-tw)/2:y={cfg.height - round(44 * lay.scale)}")
    ffmpeg("-i", str(gif), "-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-crf", "18", "-r", str(cfg.fps), str(term_mp4))
    visual = duration(term_mp4)

    sched: dict = {"scene": scene.id, "take": n, "visual_seconds": round(visual, 3), "hook_source": hook_source,
                   "pacing": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(c.pacing).items()},
                   "transforms": asdict(c.transforms), "widest_line": c.widest, "cols": t.cols,
                   "marks": c.marks, "card": None, "captions": []}

    filters, inputs = [f"[0:v]fps={cfg.fps},format=yuv420p[v0]"], ["-i", str(term_mp4)]
    last = "v0"
    if card:
        b0, b1, b2, b3 = card.box
        cx = (cfg.width - (b2 - b0)) // 2 - b0
        cy = lay.term_y + lay.term_h - round(16 * lay.scale) - b3
        rect = (cx, cy, cx + card.size[0], cy + card.size[1])
        if cy < lay.term_y:
            raise RenderError(f"scene {scene.id}: the card is taller than the terminal; show fewer lines")
        hit = screen.first_hit(events, t.rows, t.cols, rect, lay.cell_rect)
        margin = GAP + FRAME_MARGIN / cfg.fps
        t_hit = hit.t if hit else visual + margin + FADE
        gone = min(t_hit, visual) - margin
        fade_start = gone - FADE
        if clip and fade_start < card_speech_end:
            raise RenderError(f"scene {scene.id}: the card would fade at {fade_start:.2f}s, before its narration ends "
                              f"at {card_speech_end:.2f}s; shorten narration.card or raise visual_seconds")
        inputs += ["-loop", "1", "-framerate", str(cfg.fps), "-t", f"{visual}", "-i", str(card.path)]
        filters += [f"[1:v]format=rgba,fade=t=out:st={fade_start:.3f}:d={FADE}:alpha=1[card]",
                    f"[v0][card]overlay=x={cx}:y={cy}:enable='lt(t,{gone:.3f})'[v1]"]
        last = "v1"
        cover = cover_check(term_mp4, rect, lay, gone, cfg.fps)
        sched["card"] = {"png": str(card.path.relative_to(cfg.work_dir)), "file": scene.card.file,
                         "lines": scene.card.lines, "commit": scene.card.commit, "explain": scene.card.explain,
                         "x": cx, "y": cy, "w": card.size[0], "h": card.size[1], "in": 0.0,
                         "fade_start": round(fade_start, 3), "gone": round(gone, 3),
                         "text_reaches": round(hit.t, 3) if hit else None, "reached_by": hit.cause if hit else None,
                         "narration_ends": round(card_speech_end, 3) if clip else None, "cover_check": cover}
        if cover["frames_with_text_under_card"]:
            raise RenderError(f"scene {scene.id}: {cover['frames_with_text_under_card']} frame(s) show text under the visible card")

    times = []
    for i, cap in enumerate(scene.captions):
        at = castmod.resolve_trigger(c, cap.at)
        if at is None:
            raise RenderError(f"scene {scene.id}: caption {cap.text!r} never fires: {cap.at!r} is not in this take. "
                              "Reword the caption or its trigger so it describes what this take shows.")
        times.append(at)
    if times != sorted(times):
        raise RenderError(f"scene {scene.id}: caption triggers are out of order in this take: {times}")
    for i, (cap, at) in enumerate(zip(scene.captions, times)):
        nxt = times[i + 1] if i + 1 < len(times) else None
        if nxt is not None and nxt - at < CAPTION_FADE_IN + CAPTION_FADE_OUT:
            raise RenderError(f"scene {scene.id}: captions {i + 1} and {i + 2} are {nxt - at:.2f}s apart; "
                              "they need at least 1.5s. Raise visual_seconds or drop one.")
        png = out / f"caption-{i + 1}.png"
        w, _ = cards.render_caption(cap.text, png, lay.scale, cfg.fonts, t.font_family)
        x, y = cfg.width - round(40 * lay.scale) - w, round(22 * lay.scale)
        idx = 1 + (1 if card else 0) + i  # ffmpeg input index: 0 terminal, 1 card (if any), then captions
        inputs += ["-loop", "1", "-framerate", str(cfg.fps), "-t", f"{visual}", "-i", str(png)]
        chain = f"[{idx}:v]format=rgba,fade=t=in:st={at:.3f}:d={CAPTION_FADE_IN}:alpha=1"
        enable = f"gte(t,{at:.3f})"
        if nxt is not None:
            chain += f",fade=t=out:st={nxt - CAPTION_FADE_OUT:.3f}:d={CAPTION_FADE_OUT}:alpha=1"
            enable = f"between(t,{at:.3f},{nxt:.3f})"
        filters += [f"{chain}[c{i}]", f"[{last}][c{i}]overlay=x={x}:y={y}:enable='{enable}'[o{i}]"]
        last = f"o{i}"
        sched["captions"].append({"text": cap.text, "at": cap.at, "in": round(at, 3),
                                  "out": round(nxt, 3) if nxt is not None else None})

    scene_mp4 = out / "scene.mp4"
    ffmpeg(*inputs, "-filter_complex", ";".join(filters), "-map", f"[{last}]", "-t", f"{visual}", "-an",
           "-c:v", "libx264", "-crf", "18", "-r", str(cfg.fps), "-pix_fmt", "yuv420p", str(scene_mp4))
    state.write_json(out / "schedule.json", sched)
    state.write_json(out / "shown-text.json", shown)
    log(f"  {scene.id}: take {n}, {visual:.1f}s"
        + (f", card 0–{sched['card']['gone']:.2f}s (text reaches it at {sched['card']['text_reaches']}s)" if card else "")
        + (f", {len(times)} caption(s)" if times else ""))
    return sched


def cover_check(video: Path, rect: tuple, lay: Layout, gone: float, fps: int) -> dict:
    """Read every frame of the terminal-only video inside the card rectangle (clipped to the terminal) until the
    card is gone; count frames that show text there while the card is visible."""
    x0 = max(int(rect[0]), lay.term_x)
    y0 = max(int(rect[1]), lay.term_y)
    x1 = min(int(rect[2]), lay.term_x + lay.term_w)
    y1 = min(int(rect[3]), lay.term_y + lay.term_h)
    w, h = x1 - x0, y1 - y0
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-t", f"{gone + 1.0:.3f}",
                             "-vf", f"format=rgb24,crop={w}:{h}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)
    size, i, checked, bad, first_ink = w * h * 3, 0, 0, [], None
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3).astype(np.int16)
        ink = int((np.abs(frame - BG).sum(axis=2) > 60).sum())
        t = i / fps
        if ink > 20 and first_ink is None:
            first_ink = round(t, 3)
        if t < gone:
            checked += 1
            if ink > 20:
                bad.append(round(t, 3))
        i += 1
    proc.wait()
    return {"frames_checked": checked, "frames_with_text_under_card": len(bad), "first_text_in_area": first_ink}


def render_still(cfg: Config, seg: Segment) -> Path:
    out = state.render_dir(cfg) / seg.id
    out.mkdir(parents=True, exist_ok=True)
    png = out / "still.png"
    if seg.kind == "title":
        cards.render_title(seg.title, seg.subtitle, png, cfg.width, cfg.height, cfg.fonts, cfg.terminal.font_family)
    else:
        need("pdftoppm", "Install poppler (poppler-utils) for slides.")
        page = out / "page"
        run(["pdftoppm", "-png", "-f", str(seg.page), "-l", str(seg.page), "-singlefile",
             "-scale-to-x", str(cfg.width), "-scale-to-y", "-1", str(cfg.slides_pdf), str(page)])
        ffmpeg("-i", str(page) + ".png", "-vf",
               f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=decrease,"
               f"pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2:color=0x1e1e2e", str(png))
    return png


def render(cfg: Config, only: list[str] | None = None, log=print) -> dict:
    scanner = Scanner.from_env(cfg.privacy.allow_emails)
    # Gate the configured texts first: narration, titles, captions, card explanations.
    texts = {}
    for s in cfg.segments:
        texts[f"{s.id}/narration"] = "\n".join(x for x in (s.card_narration, s.narration) if x)
        texts[f"{s.id}/title"] = "\n".join(x for x in (s.title, s.subtitle) if x)
    texts["label"] = cfg.label
    _gate(scanner, texts)
    geo = lay = None
    result = {}
    for seg in cfg.segments:
        if only and seg.id not in only:
            continue
        if seg.kind == "scene":
            if lay is None:
                geo = calibrate(cfg)
                lay = layout(cfg, geo)
            result[seg.id] = render_scene(cfg, seg, lay, scanner, log)
        else:
            render_still(cfg, seg)
            log(f"  {seg.id}: {seg.kind} still")
    if lay is not None:
        state.write_json(state.render_dir(cfg) / "layout.json", {**asdict(lay), "geo": asdict(geo)})
    return result
