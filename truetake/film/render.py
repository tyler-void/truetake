"""Draw a film frame by frame with Skia and encode it once with FFmpeg.

Every frame is a pure function of film time: the script says what text is on screen, springs say where each
pane, its scroll, its cursor and the camera are. Frames can be drawn in any order, so `--preview` draws
exactly the frames the full render will.

The set: each pane is drawn at its natural size and placed by layout springs. When a thread starts, its pane
opens and every pane glides to the next layout: one pane centred; two side by side; three with the first on
the left and the others stacked on the right.

Outputs, besides the video: `film.json` (what the report lists: the sources, every change to their text, the
pacing, the time-lapse spans, the camera, the time map) and `cues.json` (visible events, for truetake-score).
"""
from __future__ import annotations

import datetime as dt
import math
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import state
from ..media import ffmpeg, need
from .motion import Spring
from .script import FilmError, Line, Script
from .theme import Theme

W_REF = 1920  # sizes below are at 1920 wide and scale with the output
LAYOUT_W = 5.0  # how briskly panes glide to a new layout


@dataclass
class Camera:
    at: float
    zoom: float
    pane: str = ""  # push toward this pane (default: the first)


@dataclass
class PaneSpec:
    id: str
    title: str = ""
    subtitle: str = ""


@dataclass
class FilmSpec:
    output: Path
    width: int
    height: int
    fps: int
    duration: float
    cols: int
    rows: int
    panes: list[PaneSpec]
    camera: list[Camera]
    utc_offset: float | None  # hours; the clock is shown only for sources with timestamps
    score: Path | None = None
    loudness: float = -21.0
    beats: list[tuple[float, float]] = field(default_factory=list)  # real seconds, for the record
    camera_auto: bool = False  # derive the camera from the script: close on the working pane while reading


class PaneView:
    """One pane's motion: its scroll and cursor springs, and where the layout puts it."""

    def __init__(self, spec: PaneSpec, lines: list[Line], rows: int, appear: float):
        self.spec, self.lines, self.appear = spec, lines, appear
        self.scroll = Spring(w=6.0)
        self.head_row, self.head_col = Spring(w=34.0), Spring(w=34.0)
        self.tail_row, self.tail_col = Spring(w=18.0), Spring(w=18.0)
        for i, l in enumerate(lines):
            self.scroll.to(l.t0, max(0, i + 2 - rows))
            for s in (self.head_row, self.tail_row):
                s.to(l.t0, i)
            for s in (self.head_col, self.tail_col):
                s.to(l.t0, l.indent)
                if l.cps:
                    s.glide(l.t0, l.t1, l.indent + len(l.text))
                else:
                    s.to(l.t0 + 1e-3, l.indent + len(l.text))
        self.x = self.y = self.s = None  # layout springs, set by the stage
        self.alpha = Spring(w=8.0)
        self.alpha.to(appear, 1.0)
        self.starts = [l.t0 for l in lines]

    def last_line_at(self, t: float) -> float:
        best = -1.0
        for t0 in self.starts:
            if t0 > t:
                break
            best = t0
        return best


class Stage:
    """The film's motion, built once from the script; `draw(canvas, t)` is pure."""

    def __init__(self, spec: FilmSpec, script: Script, theme: Theme):
        import skia

        self.skia, self.spec, self.script, self.theme = skia, spec, script, theme
        k = spec.width / W_REF
        self.k = k
        self.mono = skia.Font(skia.Typeface.MakeFromFile(theme.mono), 21 * k)
        self.mono.setSubpixel(True)
        self.label = skia.Font(skia.Typeface.MakeFromFile(theme.sans), 17 * k)
        self.small = skia.Font(skia.Typeface.MakeFromFile(theme.sans), 15 * k)
        self.clock = skia.Font(skia.Typeface.MakeFromFile(theme.serif), 44 * k)
        for f, role in ((self.mono, "mono"), (self.label, "sans"), (self.clock, "serif")):
            if f.getTypeface() is None:
                raise FilmError(f"film font `{role}` could not be loaded by Skia")
        self.cw, self.lh = self.mono.measureText("M"), round(31 * k)
        self.pad_x, self.pad_y = round(32 * k), round(26 * k)
        self.pw = round(spec.cols * self.cw) + 2 * self.pad_x
        self.ph = spec.rows * self.lh + 2 * self.pad_y
        self.band = round(60 * k)  # the label band above a pane
        self.area = (80 * k, 150 * k, spec.width - 80 * k, spec.height - 50 * k)  # where panes may go
        if self.pw > spec.width * 0.94 or self.ph > spec.height * 0.8:
            raise FilmError(f"a {spec.cols}x{spec.rows} pane does not fit a {spec.width}x{spec.height} film; "
                            "use fewer columns or rows")

        names = {p.id: p for p in spec.panes}
        self.views: list[PaneView] = []
        for i, (pid, lines) in enumerate(script.panes.items()):
            appear = 0.0 if i == 0 else max(0.0, (lines[0].t0 if lines else 1e9) - 0.5)
            self.views.append(PaneView(names.get(pid, PaneSpec(pid)), lines, spec.rows, appear))
        self.views.sort(key=lambda v: v.appear)
        self.layout()

        if spec.camera_auto:
            spec.camera = self.auto_camera()
        self.zoom, self.focus = Spring(w=1.5, x0=1.0), Spring(w=1.3, x0=0.0)
        for c in spec.camera:
            self.zoom.to(c.at, c.zoom)
            self.focus.to(c.at, 1.0 if c.zoom != 1.0 else 0.0)
        self.pushes = [(a.at, b.at) for a, b in zip(spec.camera, spec.camera[1:]) if a.zoom != 1.0]

    # ---------- layout ----------

    def slots(self, n: int) -> list[tuple[float, float, float]]:
        """(x, y, scale) of the pane tops for n visible panes."""
        x0, y0, x1, y1 = self.area
        gap = 48 * self.k
        w, h = x1 - x0, y1 - y0
        if n == 1:
            regions = [(x0, y0, w, h)]
        elif n == 2:
            cw = (w - gap) / 2
            regions = [(x0, y0, cw, h), (x0 + cw + gap, y0, cw, h)]
        else:
            cw, rh = (w - gap) / 2, (h - gap) / 2
            regions = [(x0, y0, cw, h)] + [(x0 + cw + gap, y0 + j * (rh + gap), cw, rh) for j in range(n - 1)]
            if n > 3:
                rh = (h - gap * (n - 2)) / (n - 1)
                regions = [(x0, y0, cw, h)] + [(x0 + cw + gap, y0 + j * (rh + gap), cw, rh) for j in range(n - 1)]
        out = []
        for rx, ry, rw, rh in regions:
            s = min(1.0, rw / self.pw, rh / (self.ph + self.band))
            out.append((rx + (rw - self.pw * s) / 2, ry + (rh - (self.ph + self.band) * s) / 2 + self.band * s, s))
        return out

    def layout(self) -> None:
        for i, v in enumerate(self.views):
            visible = self.views[: i + 1]
            for view, (x, y, s) in zip(visible, self.slots(len(visible))):
                if view.x is None:  # a pane opens where it will sit
                    view.x, view.y, view.s = Spring(LAYOUT_W, x0=x), Spring(LAYOUT_W, x0=y), Spring(LAYOUT_W, x0=s)
                else:
                    view.x.to(v.appear, x)
                    view.y.to(v.appear, y)
                    view.s.to(v.appear, s)

    def auto_camera(self) -> list[Camera]:
        """While the film reads, push in on the pane that writes the most in that stretch, far enough that its
        text is near full size; during a time-lapse, pull back to the whole set. Nothing is framed that the
        script does not put there."""
        end = self.script.end
        spans, t = [], self.script.lines[0].t0 if self.script.lines else 0.0
        for a, b in sorted(self.script.lapses):
            if a - t > 2.0:
                spans.append((t, a))
            t = max(t, b)
        if end - t > 2.0:
            spans.append((t, end))
        cams: list[Camera] = []
        for a, b in spans:
            counts = {v.spec.id: sum(1 for l in v.lines if a <= l.t0 <= b and l.text) for v in self.views}
            pid = max(counts, key=counts.get)
            v = next(x for x in self.views if x.spec.id == pid)
            scale = v.s.at(a + 1.0)
            zoom = min(1.8, max(1.0, 0.92 / scale))
            if zoom < 1.05:
                zoom = 1.15  # a single full-size pane still gets a gentle push
            cams.append(Camera(at=round(a + 0.3, 3), zoom=round(zoom, 3), pane=pid))
            cams.append(Camera(at=round(b, 3), zoom=1.0))
        return cams

    # ---------- drawing ----------

    def paint(self, colour: int, alpha: float = 1.0, **kw):
        p = self.skia.Paint(Color=colour, AntiAlias=True, **kw)
        p.setAlphaf(alpha * ((colour >> 24) & 0xFF) / 255)
        return p

    def breath(self, t: float) -> float:
        """A slow push that keeps a held shot alive: seconds into the shot, eased out after it."""
        for t0, t1 in self.pushes:
            if t0 < t < t1:
                return t - t0
            if t >= t1:
                return (t1 - t0) * math.exp(-(t - t1) * 1.5)
        return 0.0

    def active(self, t: float) -> PaneView | None:
        return max(self.views, key=lambda v: v.last_line_at(t), default=None)

    def focus_point(self, t: float) -> tuple[float, float]:
        target, shot_zoom = "", 1.0
        for c in self.spec.camera:
            if c.at <= t and c.zoom != 1.0:
                target, shot_zoom = c.pane, c.zoom
        v = next((v for v in self.views if v.spec.id == target), self.views[0])
        s, y = v.s.at(t), v.y.at(t)
        row = v.head_row.at(t) - v.scroll.at(t)
        cursor_y = y + (self.pad_y + row * self.lh) * s
        # Frame the whole pane (with its label) when it fits the zoomed view; otherwise follow the cursor, but
        # never past the pane's edges, so the shot does not drift onto empty stage or under the clock.
        top, bottom = y - self.band * s, y + self.ph * s
        view = self.spec.height / shot_zoom * 0.92
        if bottom - top <= view:
            fy = (top + bottom) / 2
        else:
            fy = min(max(cursor_y, top + view / 2), bottom - view / 2)
        return v.x.at(t) + self.pw * s / 2, fy

    def draw(self, canvas, t: float) -> None:
        spec, th = self.spec, self.theme
        canvas.clear(th.stage)
        s = self.zoom.at(t) * (1 + 0.006 * self.breath(t))
        fx, fy = self.focus_point(t)
        mix = self.focus.at(t)
        cx = spec.width / 2 + (fx - spec.width / 2) * mix
        cy = spec.height / 2 + (fy - spec.height / 2) * mix
        canvas.save()
        canvas.translate(spec.width / 2, spec.height / 2)
        canvas.scale(s, s)
        canvas.translate(-cx, -cy)
        live = self.active(t)
        for v in self.views:
            a = v.alpha.at(t) if v.appear > 0 else 1.0
            if a < 0.01:
                continue
            self.draw_pane(canvas, v, t, a, v is live)
        canvas.restore()
        self.draw_clock(canvas, t)

    def draw_pane(self, canvas, v: PaneView, t: float, alpha: float, live: bool) -> None:
        sk, th, k = self.skia, self.theme, self.k
        s = v.s.at(t) * (0.94 + 0.06 * alpha)  # a pane opens with a small scale-in
        canvas.save()
        canvas.translate(v.x.at(t) + self.pw * (v.s.at(t) - s) / 2, v.y.at(t))
        canvas.scale(s, s)
        if alpha < 0.99:
            canvas.saveLayerAlpha(sk.Rect.MakeXYWH(-40 * k, -self.band - 40 * k, self.pw + 80 * k,
                                                   self.ph + self.band + 120 * k), int(alpha * 255))
        title, sub = v.spec.title or v.spec.id.upper(), v.spec.subtitle
        canvas.drawString(title, 0, -44 * k, self.label, self.paint(th.ink))
        if sub:
            canvas.drawString(sub, self.label.measureText(title) + 12 * k, -44 * k, self.label, self.paint(th.ink_soft))
        canvas.drawRect(sk.Rect.MakeXYWH(0, -26 * k, self.pw, 1.2 * k), self.paint(th.accent if live else th.rule))
        rr = sk.RRect.MakeRectXY(sk.Rect.MakeXYWH(0, 0, self.pw, self.ph), 16 * k, 16 * k)
        canvas.save()
        canvas.translate(0, 20 * k)
        canvas.drawRRect(rr, self.paint(th.shadow, 0.22, MaskFilter=sk.MaskFilter.MakeBlur(sk.kNormal_BlurStyle, 26 * k)))
        canvas.restore()
        canvas.drawRRect(rr, self.paint(th.pane))
        canvas.save()
        canvas.clipRRect(rr, doAntiAlias=True)
        top = v.scroll.at(t)
        self.draw_cursor(canvas, v, t, self.pad_x, self.pad_y, top, live)
        first = max(0, int(math.floor(top)) - 1)
        for i in range(first, min(len(v.lines), first + self.spec.rows + 3)):
            l = v.lines[i]
            n = l.shown(t)
            if n == 0:
                continue
            y = self.pad_y + (i - top) * self.lh + self.lh * 0.72
            x = self.pad_x + l.indent * self.cw
            fresh = 0  # the newest characters fade in over 90 ms
            while fresh < n and t - l.char_time(n - 1 - fresh) < 0.09:
                fresh += 1
            colour = th.role(l.role)
            if l.cps or fresh == 0:
                canvas.drawString(l.text[: n - fresh], x, y, self.mono, self.paint(colour))
                for c in range(n - fresh, n):
                    a = min(1.0, (t - l.char_time(c)) / 0.09)
                    canvas.drawString(l.text[c], x + c * self.cw, y, self.mono, self.paint(colour, a))
            else:  # a whole line lands at once: fade it in as one
                canvas.drawString(l.text, x, y, self.mono, self.paint(colour, min(1.0, (t - l.t0) / 0.09)))
        canvas.restore()
        if alpha < 0.99:
            canvas.restore()
        canvas.restore()

    def draw_cursor(self, canvas, v: PaneView, t, ox, oy, top, live: bool) -> None:
        sk, th, k = self.skia, self.theme, self.k
        hr, hc = v.head_row.at(t) - top, v.head_col.at(t)
        tr, tc = v.tail_row.at(t) - top, v.tail_col.at(t)
        w, h = self.cw, self.lh - 8 * k
        hx, hy = ox + hc * self.cw, oy + hr * self.lh + 4 * k
        tx, ty = ox + tc * self.cw, oy + tr * self.lh + 4 * k
        if live:
            hull = convex_hull([(hx, hy), (hx + w, hy), (hx + w, hy + h), (hx, hy + h),
                                (tx, ty), (tx + w, ty), (tx + w, ty + h), (tx, ty + h)])
            path = sk.Path()
            path.moveTo(*hull[0])
            for p in hull[1:]:
                path.lineTo(*p)
            path.close()
            canvas.drawPath(path, self.paint(th.cursor, 0.16))  # the smear, under the text
            still = abs(hc - tc) + abs(hr - tr) < 0.05
            blink = 0.65 + 0.35 * math.cos(t * 2 * math.pi / 1.1) if still else 1.0
            canvas.drawRect(sk.Rect.MakeXYWH(hx, hy, w, h), self.paint(th.cursor, 0.85 * blink))
        else:  # a waiting pane keeps a quiet outline cursor
            p = self.paint(th.cursor, 0.35, Style=sk.Paint.kStroke_Style, StrokeWidth=1.5 * k)
            canvas.drawRect(sk.Rect.MakeXYWH(hx, hy, w, h), p)

    def draw_clock(self, canvas, t: float) -> None:
        sc, spec, th, k = self.script, self.spec, self.theme, self.k
        if not sc.timemap or sc.started is None or spec.utc_offset is None:
            return
        sk = self.skia
        x = spec.width - 300 * k
        canvas.drawRRect(sk.RRect.MakeRectXY(sk.Rect.MakeXYWH(x - 30 * k, 40 * k, 290 * k, 100 * k), 12 * k, 12 * k),
                         self.paint(th.stage, 0.96))
        when = sc.started + dt.timedelta(seconds=sc.real_at(t), hours=spec.utc_offset)
        canvas.drawString(when.strftime("%H:%M:%S"), x, 92 * k, self.clock, self.paint(th.ink))
        rate = sc.rate_at(t, h=1.0)  # averaged over two seconds of film, so the label reads steadily
        lapse = any(a <= t <= b for a, b in sc.lapses)
        if lapse:
            chip = f"×{rate:.0f} real time · time-lapse"
        elif 0.8 <= rate < 1.5:
            chip = "real time"
        elif rate < 0.05:
            chip = "paused to read"
        elif rate < 0.8:
            chip = f"×{rate:.1f} real time · slowed to read"
        else:
            chip = f"×{rate:.0f} real time"
        canvas.drawString(chip, x, 122 * k, self.small, self.paint(th.accent))


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


CUE_KIND = {"command": "command", "call": "mcp_call", "ok": "mcp_return", "output": "line", "dim": "line",
            "prompt": "command"}
WEIGHT = {"line": (0.25, 0.5), "command": (0.6, 0.3), "mcp_call": (0.5, 0.6), "mcp_return": (0.6, 0.7)}
LAPSE_GAP = 0.25  # in a time-lapse, at most one sound this often: texture, not a rattle


def cues(spec: FilmSpec, script: Script, stage: Stage) -> dict:
    """Visible events in truetake-score's cue list format. In a time-lapse, events are thinned to a texture."""
    events, last_lapse = [], -1.0
    lines = sorted((l for ls in script.panes.values() for l in ls), key=lambda l: l.t0)
    for l in lines:
        if not l.text.strip() or l.t0 > spec.duration:
            continue
        if any(a <= l.t0 <= b for a, b in script.lapses):
            if l.t0 - last_lapse < LAPSE_GAP:
                continue
            last_lapse = l.t0
            events.append({"t": round(l.t0, 4), "kind": "line", "intensity": 0.2, "sharpness": 0.6})
            continue
        if l.role == "message":
            if l.text.startswith("• "):
                events.append({"t": round(l.t0, 4), "kind": "message", "intensity": 0.4, "sharpness": 0.2})
            continue
        kind = CUE_KIND.get(l.role, "line")
        events.append({"t": round(l.t0, 4), "kind": kind, "intensity": WEIGHT[kind][0], "sharpness": WEIGHT[kind][1]})
    return {"version": 1, "duration": spec.duration, "fps": spec.fps, "key": "D major",
            "camera_spring_w": stage.zoom.w, "events": events,
            "camera": [{"t": c.at, "zoom": c.zoom} for c in spec.camera]}


def record(spec: FilmSpec, script: Script, stage: Stage, pacing) -> dict:
    shown = {pid: sum(1 for l in ls if l.t0 <= spec.duration) for pid, ls in script.panes.items()}
    return {"output": str(spec.output), "source": script.origin, "duration": spec.duration, "fps": spec.fps,
            "size": [spec.width, spec.height], "pane": {"cols": spec.cols, "rows": spec.rows},
            "panes": [{"id": v.spec.id, "title": v.spec.title, "opens_at": round(v.appear, 3),
                       "lines_shown": shown.get(v.spec.id, 0), "transforms": script.pane_transforms.get(v.spec.id)}
                      for v in stage.views],
            "lines_shown": sum(shown.values()), "transforms": script.transforms, "pacing": asdict(pacing),
            "beats_real_seconds": [list(b) for b in spec.beats],
            "time_lapse_film_seconds": [[round(a, 3), round(b, 3)] for a, b in script.lapses if a <= spec.duration],
            "clock": bool(script.timemap and spec.utc_offset is not None),
            "real_seconds_shown": round(script.real_at(spec.duration), 3) if script.timemap else None,
            "timemap": [[round(f, 3), round(r, 3)] for f, r in script.timemap if f <= spec.duration + 1],
            "camera": [asdict(c) for c in spec.camera], "camera_spring": stage.zoom.keyframes()}


def render(spec: FilmSpec, script: Script, theme: Theme, pacing, work: Path, preview: list[float] | None = None,
           log=print) -> dict:
    need("ffmpeg", "Install FFmpeg.")
    import skia

    stage = Stage(spec, script, theme)
    out_dir = work / "film"
    out_dir.mkdir(parents=True, exist_ok=True)
    surface = skia.Surface(spec.width, spec.height)

    def frame(t: float) -> bytes:
        with surface as canvas:
            stage.draw(canvas, t)
        return surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType).tobytes()

    if preview is not None:
        paths = []
        for t in preview:
            p = out_dir / f"preview-{t:06.2f}.png"
            with surface as canvas:
                stage.draw(canvas, t)
            surface.makeImageSnapshot().save(str(p), skia.kPNG)
            paths.append(p)
        log(f"  {len(paths)} preview frame(s) in {out_dir}")
        return {"preview": [str(p) for p in paths]}

    n = int(round(spec.duration * spec.fps))
    video = out_dir / "film-video.mp4"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                            "-s", f"{spec.width}x{spec.height}", "-r", str(spec.fps), "-i", "-",
                            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(video)], stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for f in range(n):
            enc.stdin.write(frame(f / spec.fps))
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode:
        raise FilmError("ffmpeg failed while encoding the film")
    took = time.time() - t0

    c = cues(spec, script, stage)
    state.write_json(out_dir / "cues.json", c)
    rec = record(spec, script, stage, pacing)
    rec.update(frames=n, render_seconds=round(took, 1))
    if spec.score:
        if not spec.score.exists():
            raise FilmError(f"film.score: {spec.score} not found (render it with truetake-score from {out_dir / 'cues.json'})")
        ffmpeg("-i", str(video), "-i", str(spec.score), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
               "-af", f"loudnorm=I={spec.loudness}:TP=-2", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
               "-t", f"{spec.duration}", str(spec.output))
        rec["score"] = {"file": str(spec.score), "loudness_lufs": spec.loudness}
    else:
        ffmpeg("-i", str(video), "-c", "copy", str(spec.output))
    state.write_json(out_dir / "film.json", rec)
    log(f"  wrote {spec.output.name}: {spec.duration:.1f}s, {n} frames in {took:.1f}s ({n / took:.0f} fps), "
        f"{rec['lines_shown']} lines in {len(stage.views)} pane(s), {len(c['events'])} cues")
    return rec
