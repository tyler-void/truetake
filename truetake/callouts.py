"""callouts: captions and highlight boxes laid over the assembled video, for a talk the presenter narrates live.

The assembled video is never re-rendered or overwritten. Callouts are overlays written to a copy
(`callouts.output`), and the copy keeps the original's length to the frame and its audio as is. Every box comes
from what truetake itself rendered:

  * a rectangle on a slide, from the config (in output pixels);
  * a line of a title card, from the title card's own layout;
  * terminal rows matching a regex, found by replaying the scene's cast on its scrolling grid; the box follows
    the line as the terminal scrolls and is hidden while the line is off screen or under the caption;
  * a scene's usage bar, from the scene's layout and the bar's values.

A caption may only use numbers that are on screen while it shows (digits, or number words such as "one").
`--preview` writes one frame per callout through the same filter graph as the full render.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import cards, cast as castmod, fonts, render as rendermod, screen, state
from .config import Box, Callout, Config, ConfigError, Segment
from .media import duration, ffmpeg, need, run
from .scan import Scanner

FADE = 0.3  # caption fade in and out
BOX_PAD, BOX_LINE = 6, 4  # px at 1080p
PEACH, BASE = "0xfab387", "0x1e1e2e"
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20", "hundred": "100",
                "thousand": "1000", "million": "1000000", "once": "1", "twice": "2", "double": "2", "triple": "3",
                "half": "50%", "dozen": "12"}


class CalloutError(RuntimeError):
    pass


Rect = tuple[int, int, int, int]  # x0, y0, x1, y1


@dataclass
class Span:
    """A box drawn from t0 to t1 (seconds of the assembled video) at rect."""
    t0: float
    t1: float
    rect: Rect


@dataclass
class Loupe:
    spans: list[Span]  # where the magnified line is, over time (crop source)
    w: int  # crop size
    h: int
    zoom: float
    x: int  # where the loupe sits
    y: int


@dataclass
class Plan:
    index: int
    callout: Callout
    t0: float
    t1: float
    caption: Path
    cap_rect: Rect
    spans: list[Span] = field(default_factory=list)
    loupe: Loupe | None = None
    hidden: float = 0.0  # seconds a line box spent under the caption (not drawn)
    preview_at: float = 0.0
    numbers: list[str] = field(default_factory=list)


def _intersects(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _merge(spans: list[Span]) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if out and out[-1].rect == s.rect and abs(out[-1].t1 - s.t0) < 1e-6:
            out[-1].t1 = s.t1
        else:
            out.append(Span(s.t0, s.t1, s.rect))
    return out


class Timeline:
    """Segment starts and lengths of the assembled video, and the render artifacts behind each scene."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        p = state.assemble_dir(cfg) / "assemble.json"
        if not p.exists() or not cfg.output.exists():
            raise CalloutError("no assembled video yet: run `truetake assemble` (callouts are laid over its output)")
        a = state.read_json(p)
        if Path(a["output"]).resolve() != cfg.output.resolve():
            raise CalloutError(f"assemble.json describes {a['output']}, not {cfg.output}; run `truetake assemble` again")
        self.segs = {s["id"]: s for s in a["segments"]}
        self.seconds = duration(cfg.output)
        if abs(self.seconds - a["seconds"]) > 1.0 / cfg.fps + 1e-3:
            raise CalloutError(f"{cfg.output.name} is {self.seconds:.3f}s but assemble.json says {a['seconds']}s; "
                               "it changed after assembly: run `truetake assemble` again")
        self._sched: dict[str, dict] = {}

    def container(self, seg: Segment) -> dict:
        return self.segs[seg.in_pair or seg.id]

    def window(self, seg_id: str) -> tuple[float, float]:
        seg = self.cfg.segment(seg_id)
        s = self.container(seg)
        # assemble.json rounds `start` to the millisecond but not `seconds`: snap both ends to the frame grid so
        # that `<a>:end` and the next segment's start are the same instant
        f = self.cfg.fps
        return round(s["start"] * f) / f, round((s["start"] + s["seconds"]) * f) / f

    def schedule(self, scene: str) -> dict:
        if scene not in self._sched:
            self._sched[scene] = state.read_json(state.render_dir(self.cfg) / scene / "schedule.json")
        return self._sched[scene]

    def time(self, spec: str) -> float:
        m = re.match(r"^(\d+(?:\.\d+)?)$", spec)
        if m:
            return float(m[1])
        if "@" in spec:
            scene, mark = spec.split("@", 1)
            marks = self.schedule(scene)["marks"]
            if mark not in marks:
                raise CalloutError(f"time {spec!r}: scene {scene} has no mark {mark!r} (it has {', '.join(marks)})")
            return self.window(scene)[0] + marks[mark]
        m = re.match(r"^([a-z0-9][a-z0-9_-]*)(?::(end)|([+-]\d+(?:\.\d+)?))?$", spec)
        start, end = self.window(m[1])
        return end if m[2] else start + float(m[3] or 0)


def scene_geometry(cfg: Config, scene: Segment):
    """The layout a scene was rendered with, its terminal size, its x offset in the frame and its config."""
    if scene.in_pair:
        pair = cfg.segment(scene.in_pair)
        half = rendermod.half_config(cfg, pair)
        lay = rendermod.layout(half, rendermod.calibrate(half))
        return lay, half.terminal.rows, half.terminal.cols, (half.width if scene.id == pair.right else 0), half
    return rendermod.layout(cfg, rendermod.calibrate(cfg)), cfg.terminal.rows, cfg.terminal.cols, 0, cfg


def _pad(r, k: float) -> Rect:
    p = round(BOX_PAD * k)
    return (int(r[0]) - p, int(r[1]) - p, int(round(r[2])) + p, int(round(r[3])) + p)


def status_spans(cfg: Config, tl: Timeline, box: Box, t0: float, t1: float) -> list[Span]:
    scene = cfg.segment(box.scene)
    lay, _, _, xo, rcfg = scene_geometry(cfg, scene)
    base, end = tl.window(scene.id)
    bar = tl.schedule(scene.id)["usage_bar"]
    fs = round(30 * min(lay.scale, rcfg.width / 1920 * 1.6))  # as render_scene draws it
    font = ImageFont.truetype(fonts.resolve(cfg.fonts, "mono", cfg.terminal.font_family), fs)
    asc, desc = font.getmetrics()
    x, y = xo + lay.term_x + round(16 * lay.scale), round(52 * lay.scale)
    spans = []
    for k, (bt, txt) in enumerate(bar):
        a, b = base + bt, (base + bar[k + 1][0] if k + 1 < len(bar) else end)
        a, b = max(a, t0), min(b, t1)
        if b > a:
            spans.append(Span(a, b, _pad((x, y, x + font.getlength(txt), y + asc + desc), lay.scale)))
    return spans


def line_spans(cfg: Config, tl: Timeline, box: Box, t0: float, t1: float) -> list[Span]:
    scene = cfg.segment(box.scene)
    lay, rows, cols, xo, _ = scene_geometry(cfg, scene)
    base, end = tl.window(scene.id)
    _, events = castmod.read_events(state.render_dir(cfg) / scene.id / "scene.cast")
    snaps = screen.snapshots(events, rows, cols)
    rx = re.compile(box.line)
    spans = []
    for i, (st, text) in enumerate(snaps):
        a, b = base + st, (base + snaps[i + 1][0] if i + 1 < len(snaps) else end)
        a, b = max(a, t0), min(b, t1)
        if b <= a:
            continue
        hit = next((r for r, line in enumerate(text) if rx.search(line)), None)
        if hit is None:
            continue
        last = min(hit + box.rows, rows) - 1
        body = [l for l in text[hit:last + 1] if l.strip()]
        c0 = min(len(l) - len(l.lstrip()) for l in body)
        c1 = max(len(l) for l in body)
        x0, y0, _, _ = lay.cell_rect(hit, c0)
        _, _, x1, y1 = lay.cell_rect(last, c1 - 1)
        spans.append(Span(a, b, _pad((xo + x0, y0, xo + x1, y1), lay.scale)))
    return _merge(spans)


def still_spans(cfg: Config, tl: Timeline, box: Box, t0: float, t1: float) -> list[Span]:
    seg = cfg.segment(box.segment)
    a, b = tl.window(seg.id)
    a, b = max(a, t0), min(b, t1)
    if b <= a:
        return []
    if box.rect:
        rect = box.rect
    else:
        rect = cards.title_boxes(seg.title, seg.subtitle, cfg.width, cfg.height, cfg.fonts, cfg.terminal.font_family)[box.text]
    return [Span(a, b, _pad(rect, cfg.height / 1080))]


def _scene_text(cfg: Config, tl: Timeline, sc: Segment, t0: float, t1: float) -> list[str]:
    """The terminal rows, usage-bar values and step captions of a scene that are on screen between t0 and t1."""
    _, rows, cols, _, _ = scene_geometry(cfg, sc)
    base, end = tl.window(sc.id)
    _, events = castmod.read_events(state.render_dir(cfg) / sc.id / "scene.cast")
    snaps = screen.snapshots(events, rows, cols)
    out = []
    for i, (st, text) in enumerate(snaps):
        if base + st < t1 and (base + snaps[i + 1][0] if i + 1 < len(snaps) else end) > t0:
            out += text
    sched = tl.schedule(sc.id)
    bar = sched.get("usage_bar") or []
    for k, (bt, txt) in enumerate(bar):
        if base + bt < t1 and (base + bar[k + 1][0] if k + 1 < len(bar) else end) > t0:
            out.append(txt)
    for cap in sched.get("captions") or []:
        if base + cap["in"] < t1 and (base + cap["out"] if cap["out"] is not None else end) > t0:
            out.append(cap["text"])
    out.append(sc.label if sc.label is not None else cfg.label)
    return out


def slide_text(cfg: Config, page: int) -> str:
    """A slide page's words: its PDF text layer, else `slides.text` (JSON {page: text}). A page with neither
    cannot back a number, so a caption with numbers over it fails the check rather than passing unread."""
    need("pdftotext", "Install poppler (poppler-utils).")
    text = run(["pdftotext", "-f", str(page), "-l", str(page), str(cfg.slides_pdf), "-"]).stdout
    if not text.strip() and cfg.slides_text and cfg.slides_text.exists():
        text = str(state.read_json(cfg.slides_text).get(str(page), ""))
    return text


def shown_text(cfg: Config, tl: Timeline, t0: float, t1: float) -> str:
    """Everything on screen between t0 and t1, for the numbers check."""
    parts = []
    for seg in cfg.segments:
        s = tl.segs.get(seg.id)
        if not s or s["start"] >= t1 or s["start"] + s["seconds"] <= t0:
            continue
        if seg.kind == "title":
            parts += [seg.title, seg.subtitle]
        elif seg.kind == "slide":
            parts.append(slide_text(cfg, seg.page))
        elif seg.kind == "pair":
            parts += _scene_text(cfg, tl, cfg.segment(seg.left), t0, t1) + _scene_text(cfg, tl, cfg.segment(seg.right), t0, t1)
        else:
            parts += _scene_text(cfg, tl, seg, t0, t1)
    return "\n".join(p for p in parts if p)


def check_numbers(text: str, shown: str) -> tuple[list[str], list[str]]:
    """(numbers in the caption, those not on screen). Digits must appear verbatim; a number word must appear as
    the word or as its digits."""
    found, missing = [], []
    low = shown.lower()
    for n in NUM_RE.findall(text):
        found.append(n)
        if not re.search(rf"(?<![\d.,]){re.escape(n)}(?![\d]|[.,]\d)", shown):
            missing.append(n)
    for w in re.findall(r"[A-Za-z]+", text):
        d = NUMBER_WORDS.get(w.lower())
        if d is None:
            continue
        found.append(w)
        if not (re.search(rf"\b{w.lower()}\b", low) or re.search(rf"(?<![\d.,]){re.escape(d)}(?![\d]|[.,]\d)", shown)):
            missing.append(w)
    return found, missing


def plan(cfg: Config, tl: Timeline, out_dir: Path) -> list[Plan]:
    co = cfg.callouts
    scale = cfg.height / 1080
    plans = []
    prev_end = 0.0
    for i, c in enumerate(co.items, 1):
        where = f"callout {i} ({c.text!r})"
        t0, t1 = tl.time(c.start), min(tl.time(c.end), tl.seconds)
        if t1 - t0 < 1.0:
            raise CalloutError(f"{where}: runs {t0:.2f}–{t1:.2f}s; a callout needs at least 1s")
        if t0 < prev_end - 1e-6:
            raise CalloutError(f"{where}: starts at {t0:.2f}s, before the previous callout ends ({prev_end:.2f}s)")
        prev_end = t1
        png = out_dir / f"caption-{i:02d}.png"
        w, h = cards.render_callout(c.text, png, scale, co.font_size, cfg.fonts, cfg.terminal.font_family)
        if w > cfg.width - round(40 * scale):
            raise CalloutError(f"{where}: the caption is {w}px wide, wider than the frame; shorten it")
        x, y = (cfg.width - w) // 2, cfg.height - round(100 * scale) - h
        p = Plan(index=i, callout=c, t0=t0, t1=t1, caption=png, cap_rect=(x, y, x + w, y + h))
        loupe_src: list[Span] = []
        for j, b in enumerate(c.boxes):
            if b.line:
                spans = line_spans(cfg, tl, b, t0, t1)
                if not loupe_src:
                    loupe_src = spans
                visible = [s for s in spans if not _intersects(s.rect, p.cap_rect)]
                p.hidden += sum(s.t1 - s.t0 for s in spans if _intersects(s.rect, p.cap_rect))
                if not spans:
                    raise CalloutError(f"{where}: box {j + 1} ({b.line!r}) matches no visible row of {b.scene} "
                                       f"between {t0:.2f}s and {t1:.2f}s")
                if not visible and not c.zoom:
                    raise CalloutError(f"{where}: box {j + 1} is only on screen under the caption; add `zoom` or move the window")
                p.spans += visible
            else:
                spans = status_spans(cfg, tl, b, t0, t1) if b.status else still_spans(cfg, tl, b, t0, t1)
                if not spans:
                    raise CalloutError(f"{where}: box {j + 1} is not on screen between {t0:.2f}s and {t1:.2f}s")
                if any(_intersects(s.rect, p.cap_rect) for s in spans):
                    raise CalloutError(f"{where}: box {j + 1} would sit under the caption; point at something above it")
                p.spans += spans
        if c.zoom:
            first, k = loupe_src[0].t0, round(BOX_PAD * scale)  # the loupe crops the rows exactly, without the box's pad
            src = [Span(s.t0, min(s.t1, first + c.zoom_seconds), (s.rect[0] + k, s.rect[1] + k, s.rect[2] - k, s.rect[3] - k))
                   for s in loupe_src if s.t0 < first + c.zoom_seconds]
            p.loupe = _loupe(cfg, src, c.zoom, p.cap_rect, where)
        found, missing = check_numbers(c.text, shown_text(cfg, tl, t0, t1))
        if missing:
            raise CalloutError(f"{where}: {', '.join(missing)} is not on screen while the caption shows; "
                               "a caption may only repeat numbers the video already shows")
        p.numbers = found
        p.preview_at = c.preview_at if c.preview_at is not None else _preview_time(p)
        plans.append(p)
    return plans


def _loupe(cfg: Config, spans: list[Span], zoom: float, cap: Rect, where: str) -> Loupe:
    scale = cfg.height / 1080
    w = max(s.rect[2] - s.rect[0] for s in spans) // 2 * 2
    h = max(s.rect[3] - s.rect[1] for s in spans) // 2 * 2
    border, margin = max(2, round(4 * scale)) + round(10 * scale), round(20 * scale)  # peach rule + dark inner margin
    zoom = min(zoom, (cfg.width - 2 * margin - 2 * border) / w)
    zw, zh = int(w * zoom) // 2 * 2 + 2 * border, int(h * zoom) // 2 * 2 + 2 * border
    cx = sum((s.rect[0] + s.rect[2]) / 2 for s in spans) / len(spans)
    x = int(min(max(cx - zw / 2, margin), cfg.width - margin - zw))
    # Top of the terminal first: in a scrolling terminal the oldest text is there, the newest at the bottom.
    for y in (round(110 * scale), cap[1] - round(24 * scale) - zh):
        r = (x, y, x + zw, y + zh)
        if not _intersects(r, cap) and not any(_intersects(r, s.rect) for s in spans):
            return Loupe(spans=spans, w=w, h=h, zoom=round(zoom, 3), x=x, y=y)
    raise CalloutError(f"{where}: no room for the loupe that covers neither its line nor the caption")


def _preview_time(p: Plan) -> float:
    lo, hi = p.t0 + FADE + 0.2, p.t1 - FADE - 0.2
    src = p.loupe.spans if p.loupe else p.spans
    if src:
        s = max(src, key=lambda s: min(s.t1, hi) - max(s.t0, lo))
        a, b = max(s.t0, lo), min(s.t1, hi)
        if b > a:
            return round((a + b) / 2, 3)
    return round((lo + hi) / 2, 3)


def _expr(spans: list[Span], key) -> str:
    """A piecewise value over time: spans are sorted and do not overlap."""
    e = str(key(spans[-1]))
    for s in reversed(spans[:-1]):
        e = f"if(lt(t,{s.t1:.3f}),{key(s)},{e})"
    return e


def graph(cfg: Config, plans: list[Plan], shift: float | None = None) -> tuple[list[str], str, str]:
    """The ffmpeg inputs after the video, the filter graph and its output label. With `shift`, every input's
    timestamps start at that second, so a single frame comes out exactly as the full render shows it then."""
    scale = cfg.height / 1080
    sh = f",setpts=PTS+{shift:.3f}/TB" if shift is not None else ""
    inputs, filters, last = [], [f"[0:v]format=yuv420p{sh}[b0]"], "b0"
    n = 0
    for p in plans:
        if p.loupe:
            lp, b, m = p.loupe, max(2, round(4 * scale)), round(10 * scale)
            on = "+".join(f"between(t,{s.t0:.3f},{s.t1:.3f})" for s in lp.spans)
            zw, zh = int(lp.w * lp.zoom) // 2 * 2, int(lp.h * lp.zoom) // 2 * 2
            filters += [f"[{last}]split[m{p.index}][s{p.index}]",
                        f"[s{p.index}]crop=w={lp.w}:h={lp.h}:x='{_expr(lp.spans, lambda s: min(max(0, s.rect[0]), cfg.width - lp.w))}'"
                        f":y='{_expr(lp.spans, lambda s: min(max(0, s.rect[1]), cfg.height - lp.h))}',"
                        f"scale={zw}:{zh}:flags=lanczos,pad={zw + 2 * m}:{zh + 2 * m}:{m}:{m}:color={BASE},"
                        f"pad={zw + 2 * (m + b)}:{zh + 2 * (m + b)}:{b}:{b}:color={PEACH}[z{p.index}]",
                        f"[m{p.index}][z{p.index}]overlay=x={lp.x}:y={lp.y}:enable='{on}'[l{p.index}]"]
            last = f"l{p.index}"
        for k, s in enumerate(p.spans):
            x0, y0, x1, y1 = s.rect
            filters.append(f"[{last}]drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color={PEACH}:"
                           f"t={max(2, round(BOX_LINE * scale))}:enable='between(t,{s.t0:.3f},{s.t1:.3f})'[d{p.index}_{k}]")
            last = f"d{p.index}_{k}"
        n += 1
        inputs += ["-loop", "1", "-framerate", str(cfg.fps), "-t", f"{cfg_seconds(cfg, shift):.3f}", "-i", str(p.caption)]
        x, y = p.cap_rect[0], p.cap_rect[1]
        filters += [f"[{n}:v]format=rgba{sh},fade=t=in:st={p.t0:.3f}:d={FADE}:alpha=1,"
                    f"fade=t=out:st={p.t1 - FADE:.3f}:d={FADE}:alpha=1[c{p.index}]",
                    f"[{last}][c{p.index}]overlay=x={x}:y={y}:enable='between(t,{p.t0:.3f},{p.t1:.3f})'[o{p.index}]"]
        last = f"o{p.index}"
    filters.append(f"[{last}]format=yuv420p[v]")
    return inputs, ";".join(filters), "v"


def cfg_seconds(cfg: Config, shift: float | None) -> float:
    return 1.0 if shift is not None else duration(cfg.output) + 1.0


def _gate(plans: list[Plan]) -> None:
    scanner = Scanner.from_env()
    hits = [h for p in plans for h in scanner.scan(f"callout {p.index}", p.callout.text)]
    if hits:
        raise CalloutError(f"privacy gate: {len(hits)} hit(s) in callout captions; nothing was rendered")


def _record(cfg: Config, plans: list[Plan], out_dir: Path, extra: dict) -> dict:
    data = {"source": str(cfg.output), "output": str(cfg.callouts.output), **extra, "callouts": [
        {"index": p.index, "beat": p.callout.beat, "text": p.callout.text, "from": p.callout.start, "to": p.callout.end,
         "in": round(p.t0, 3), "out": round(p.t1, 3), "numbers": p.numbers, "preview_at": p.preview_at,
         "boxes": len(p.callout.boxes), "box_spans": [asdict(s) for s in p.spans],
         "line_box_hidden_under_caption_s": round(p.hidden, 2),
         "loupe": ({"zoom": p.loupe.zoom, "x": p.loupe.x, "y": p.loupe.y, "spans": len(p.loupe.spans)} if p.loupe else None)}
        for p in plans]}
    state.write_json(out_dir / "callouts.json", data)
    return data


def preview(cfg: Config, log=print) -> dict:
    """One frame per callout, at its preview time, through the full filter graph; plus a contact sheet."""
    if not cfg.callouts:
        raise ConfigError("no `callouts` in truetake.yaml")
    tl = Timeline(cfg)
    out_dir = state.callouts_dir(cfg)
    plans = plan(cfg, tl, out_dir)
    _gate(plans)
    pdir = out_dir / "preview"
    pdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for p in plans:
        raw = pdir / f"raw-{p.index:02d}.png"
        ffmpeg("-ss", f"{p.preview_at:.3f}", "-i", str(cfg.output), "-frames:v", "1", str(raw))
        inputs, g, label = graph(cfg, plans, shift=p.preview_at)
        out = pdir / f"callout-{p.index:02d}.png"
        ffmpeg("-loop", "1", "-framerate", str(cfg.fps), "-t", "1", "-i", str(raw), *inputs,
               "-filter_complex", g, "-map", f"[{label}]", "-frames:v", "1", str(out))
        raw.unlink()
        frames.append(out)
        log(f"  {p.index:2d}  {p.t0:6.2f}–{p.t1:6.2f}s  @{p.preview_at:6.2f}s  {p.callout.text}")
    sheet = _sheet(cfg, frames, plans, pdir / "sheet.png")
    data = _record(cfg, plans, out_dir, {"preview": [str(f) for f in frames], "sheet": str(sheet)})
    log(f"  {len(frames)} preview frame(s) and {sheet.name} in {pdir}")
    return data


def _sheet(cfg: Config, frames: list[Path], plans: list[Plan], out: Path) -> Path:
    cols, tw = 2, 960
    ims = [Image.open(f).convert("RGB") for f in frames]
    th = round(tw * ims[0].height / ims[0].width)
    bar = 44
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + bar)), "#11111b")
    d = ImageDraw.Draw(sheet)
    font = fonts.load(fonts.resolve(cfg.fonts, "sans", cfg.terminal.font_family, "–"), 26)
    for k, (im, p) in enumerate(zip(ims, plans)):
        x, y = (k % cols) * tw, (k // cols) * (th + bar)
        d.text((x + 12, y + 8), f"{p.index}. {p.callout.beat}  ({p.t0:.1f}–{p.t1:.1f}s, frame @{p.preview_at:.1f}s)",
               fill="#cdd6f4", font=font)
        sheet.paste(im.resize((tw, th)), (x, y + bar))
    sheet.save(out)
    return out


def render(cfg: Config, log=print) -> dict:
    if not cfg.callouts:
        raise ConfigError("no `callouts` in truetake.yaml")
    tl = Timeline(cfg)
    out_dir = state.callouts_dir(cfg)
    plans = plan(cfg, tl, out_dir)
    _gate(plans)
    inputs, g, label = graph(cfg, plans)
    out = cfg.callouts.output
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg("-i", str(cfg.output), *inputs, "-filter_complex", g, "-map", f"[{label}]", "-map", "0:a?",
           "-c:a", "copy", "-c:v", "libx264", "-crf", "18", "-r", str(cfg.fps), "-pix_fmt", "yuv420p",
           "-t", f"{tl.seconds:.3f}", "-movflags", "+faststart", str(tmp))
    got = duration(tmp)
    if abs(got - tl.seconds) > 1.0 / cfg.fps + 1e-3:
        tmp.unlink()
        raise CalloutError(f"the callout video is {got:.3f}s, the source {tl.seconds:.3f}s; nothing was written")
    tmp.replace(out)
    data = _record(cfg, plans, out_dir, {"seconds": round(got, 3), "source_seconds": round(tl.seconds, 3)})
    log(f"  wrote {out.name}: {got:.2f}s (source {tl.seconds:.2f}s), {len(plans)} callout(s)")
    return data
