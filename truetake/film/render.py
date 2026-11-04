"""Draw a film frame by frame with Skia and encode it once with FFmpeg.

Every frame is a pure function of film time: the script says what text is on screen, springs say where the
scroll, the cursor and the camera are. Frames can be drawn in any order, so `--preview` draws exactly the
frames the full render will.

Outputs, besides the video: `film.json` (what the report lists: the source, every change to its text, the
pacing, the camera's moves, the time map) and `cues.json` (every visible event, for truetake-score).
"""
from __future__ import annotations

import datetime as dt
import math
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import state
from ..media import ffmpeg, need
from .motion import Spring
from .script import FilmError, Script
from .theme import Theme

W_REF = 1920  # sizes below are at 1920 wide and scale with the output


@dataclass
class Camera:
    at: float
    zoom: float


@dataclass
class FilmSpec:
    output: Path
    width: int
    height: int
    fps: int
    duration: float
    cols: int
    rows: int
    title: str
    subtitle: str
    camera: list[Camera]
    utc_offset: float | None  # hours; the clock is shown only for sources with timestamps
    score: Path | None = None
    loudness: float = -21.0


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
        if self.pw > spec.width * 0.94 or self.ph > spec.height * 0.8:
            raise FilmError(f"a {spec.cols}x{spec.rows} pane does not fit a {spec.width}x{spec.height} film; "
                            "use fewer columns or rows")
        self.px, self.py = (spec.width - self.pw) // 2, round(spec.height * 0.19)

        lines = script.lines
        self.scroll = Spring(w=6.0)
        self.head_row, self.head_col = Spring(w=34.0), Spring(w=34.0)
        self.tail_row, self.tail_col = Spring(w=18.0), Spring(w=18.0)
        for i, l in enumerate(lines):
            self.scroll.to(l.t0, max(0, i + 2 - spec.rows))
            for s in (self.head_row, self.tail_row):
                s.to(l.t0, i)
            for s in (self.head_col, self.tail_col):
                s.to(l.t0, l.indent)
                if l.cps:
                    s.glide(l.t0, l.t1, l.indent + len(l.text))
                else:
                    s.to(l.t0 + 1e-3, l.indent + len(l.text))
        self.zoom, self.focus = Spring(w=1.5, x0=1.0), Spring(w=1.3, x0=0.0)
        for c in spec.camera:
            self.zoom.to(c.at, c.zoom)
            self.focus.to(c.at, 1.0 if c.zoom != 1.0 else 0.0)
        self.pushes = [(a.at, b.at) for a, b in zip(spec.camera, spec.camera[1:]) if a.zoom != 1.0]

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

    def draw(self, canvas, t: float) -> None:
        sk, th, spec = self.skia, self.theme, self.spec
        canvas.clear(th.stage)
        top = self.scroll.at(t)
        s = self.zoom.at(t) * (1 + 0.006 * self.breath(t))
        row_y = self.py + self.pad_y + (self.head_row.at(t) - top) * self.lh
        focus_y = spec.height / 2 + (row_y - spec.height / 2) * self.focus.at(t)
        canvas.save()
        canvas.translate(spec.width / 2, spec.height / 2)
        canvas.scale(s, s)
        canvas.translate(-spec.width / 2, -focus_y)

        k = self.k
        canvas.drawString(spec.title, self.px, self.py - 44 * k, self.label, self.paint(th.ink))
        title_w = self.label.measureText(spec.title)
        canvas.drawString(spec.subtitle, self.px + title_w + 12 * k, self.py - 44 * k, self.label, self.paint(th.ink_soft))
        canvas.drawRect(sk.Rect.MakeXYWH(self.px, self.py - 26 * k, self.pw, 1.2 * k), self.paint(th.rule))

        rr = sk.RRect.MakeRectXY(sk.Rect.MakeXYWH(self.px, self.py, self.pw, self.ph), 16 * k, 16 * k)
        canvas.save()
        canvas.translate(0, 20 * k)
        canvas.drawRRect(rr, self.paint(th.shadow, 0.22, MaskFilter=sk.MaskFilter.MakeBlur(sk.kNormal_BlurStyle, 26 * k)))
        canvas.restore()
        canvas.drawRRect(rr, self.paint(th.pane))
        canvas.save()
        canvas.clipRRect(rr, doAntiAlias=True)
        ox, oy = self.px + self.pad_x, self.py + self.pad_y
        self.draw_cursor(canvas, t, ox, oy, top)
        first = max(0, int(math.floor(top)) - 1)
        lines = self.script.lines
        for i in range(first, min(len(lines), first + spec.rows + 3)):
            l = lines[i]
            n = l.shown(t)
            if n == 0:
                continue
            y = oy + (i - top) * self.lh + self.lh * 0.72
            x = ox + l.indent * self.cw
            fresh = 0  # the newest characters fade in over 90 ms
            while fresh < n and t - l.char_time(n - 1 - fresh) < 0.09:
                fresh += 1
            colour = th.role(l.role)
            canvas.drawString(l.text[: n - fresh], x, y, self.mono, self.paint(colour))
            for c in range(n - fresh, n):
                a = min(1.0, (t - l.char_time(c)) / 0.09)
                canvas.drawString(l.text[c], x + c * self.cw, y, self.mono, self.paint(colour, a))
        canvas.restore()
        canvas.restore()
        self.draw_clock(canvas, t)

    def draw_cursor(self, canvas, t, ox, oy, top) -> None:
        sk, th = self.skia, self.theme
        hr, hc = self.head_row.at(t) - top, self.head_col.at(t)
        tr, tc = self.tail_row.at(t) - top, self.tail_col.at(t)
        w, h = self.cw, self.lh - 8 * self.k
        hx, hy = ox + hc * self.cw, oy + hr * self.lh + 4 * self.k
        tx, ty = ox + tc * self.cw, oy + tr * self.lh + 4 * self.k
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
        if 0.8 <= rate < 1.5:
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


def cues(spec: FilmSpec, script: Script, stage: Stage) -> dict:
    """Every visible event, in truetake-score's cue list format: one per line that reaches the screen."""
    events = []
    for l in script.lines:
        if not l.text.strip() or l.t0 > spec.duration:
            continue
        if l.role == "message":
            if l.text.startswith("• "):
                events.append({"t": round(l.t0, 4), "kind": "message", "intensity": 0.4, "sharpness": 0.2})
            continue
        kind = CUE_KIND.get(l.role, "line")
        weight = {"line": (0.25, 0.5), "command": (0.6, 0.3), "mcp_call": (0.5, 0.6), "mcp_return": (0.6, 0.7)}[kind]
        events.append({"t": round(l.t0, 4), "kind": kind, "intensity": weight[0], "sharpness": weight[1]})
    return {"version": 1, "duration": spec.duration, "fps": spec.fps, "key": "D major",
            "camera_spring_w": stage.zoom.w, "events": events,
            "camera": [{"t": c.at, "zoom": c.zoom} for c in spec.camera]}


def record(spec: FilmSpec, script: Script, stage: Stage, pacing) -> dict:
    shown = [l for l in script.lines if l.t0 <= spec.duration]
    return {"output": str(spec.output), "source": script.origin, "duration": spec.duration, "fps": spec.fps,
            "size": [spec.width, spec.height], "pane": {"cols": spec.cols, "rows": spec.rows},
            "lines_shown": len(shown), "lines_in_script": len(script.lines),
            "transforms": script.transforms, "pacing": asdict(pacing),
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
        f"{rec['lines_shown']} lines, {len(c['events'])} cues")
    return rec
