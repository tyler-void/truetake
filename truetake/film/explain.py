"""The explainer stage: real images, feature maps, heatmaps, boxes and labels on a paper ground, scene by scene.

The film set (render.py) draws terminal panes. An explainer (how a model or a pipeline works, module by module,
on real data) needs pictures instead. This module gives the same guarantees as the film set:

- Every frame is a pure function of film time: scenes are `draw(ctx, t)` callbacks, motion comes from `ease`,
  `tween` and the springs in motion.py, so any frame can be drawn alone and `--preview` equals the full render.
- Words are subtitles held for their reading time, never captions racing the picture.
- A cue list (scene starts, visible events) goes out for truetake-score, and the music is mixed in once.
- One encode, exact frame count.

Scenes draw real arrays: `image(...)` takes an RGB uint8 array, `heatmap(...)` a 2-D float array and colours it with
a fixed map, so a value on screen is a value from the run.
"""
from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..media import ffmpeg

# ---------- time ----------


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def ease(t: float, t0: float, dur: float, kind: str = "out") -> float:
    """0 before t0, 1 after t0 + dur, eased in between. kinds: out (arrivals), inout (camera), in (exits)."""
    if dur <= 0:
        return 1.0 if t >= t0 else 0.0
    u = clamp((t - t0) / dur)
    if kind == "out":
        return 1 - (1 - u) ** 3
    if kind == "in":
        return u ** 3
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def tween(t: float, t0: float, dur: float, a, b, kind: str = "out"):
    """a → b over [t0, t0 + dur]; numbers or equal-length tuples."""
    k = ease(t, t0, dur, kind)
    if isinstance(a, (tuple, list)):
        return tuple(x + (y - x) * k for x, y in zip(a, b))
    return a + (b - a) * k


def window(t: float, t0: float, t1: float, fade: float = 0.3) -> float:
    """Opacity of something shown on [t0, t1]: fades in and out, 0 outside."""
    if t < t0 or t > t1:
        return 0.0
    return min(1.0, (t - t0) / fade if fade else 1.0, (t1 - t) / fade if fade else 1.0)


# ---------- colour ----------

def hexcol(s: str, alpha: float = 1.0) -> int:
    s = s.lstrip("#")
    return (int(alpha * 255) << 24) | int(s, 16)


# A perceptual dark-to-bright map (viridis-like control points), so heatmaps read the same in every scene.
_CMAP = np.array([[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137],
                  [53, 183, 121], [109, 205, 89], [180, 222, 44], [253, 231, 37]], dtype=np.float32)


def colormap(a: np.ndarray, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    """2-D float array → RGB uint8, with a fixed map; lo/hi default to the array's own range."""
    a = np.asarray(a, dtype=np.float32)
    lo = float(a.min()) if lo is None else lo
    hi = float(a.max()) if hi is None else hi
    u = np.clip((a - lo) / (hi - lo + 1e-12), 0, 1) * (len(_CMAP) - 1)
    i = np.floor(u).astype(int)
    j = np.minimum(i + 1, len(_CMAP) - 1)
    f = (u - i)[..., None]
    return (_CMAP[i] * (1 - f) + _CMAP[j] * f).astype(np.uint8)


# ---------- the stage ----------

@dataclass
class Theme:
    ground: str = "#f6f8fa"
    surface: str = "#ffffff"
    ink: str = "#1f2328"
    muted: str = "#59636e"
    line: str = "#d0d7de"
    modules: dict = field(default_factory=lambda: {"backbone": "#2f6fd1", "fpn": "#3f9a4b", "head": "#e07b39",
                                                   "tracker": "#8a5cd1", "fusion": "#c2364a"})
    sans: str = ""
    display: str = ""
    mono: str = ""


@dataclass
class Scene:
    id: str
    duration: float
    draw: Callable  # draw(ctx, t) with t from 0 to duration
    module: str = ""  # lit in the minimap
    subtitles: list = field(default_factory=list)  # (t0, t1, text), scene time
    cues: list = field(default_factory=list)  # (t, kind, intensity, sharpness), scene time


class Ctx:
    """What a scene draws with. Coordinates are 1920×1080 design pixels, scaled to the output."""

    def __init__(self, canvas, theme: Theme, width: int, height: int, fonts: dict):
        import skia

        self.sk, self.canvas, self.theme = skia, canvas, theme
        self.k = width / 1920
        self.width, self.height = width, height
        self.fonts = fonts
        self._images: dict[int, object] = {}

    # paints and fonts
    def paint(self, colour: str | int, alpha: float = 1.0, **kw):
        c = hexcol(colour) if isinstance(colour, str) else colour
        p = self.sk.Paint(Color=c, AntiAlias=True, **kw)
        p.setAlphaf(clamp(alpha) * ((c >> 24) & 0xFF) / 255)
        return p

    def font(self, role: str, size: float):
        f = self.sk.Font(self.fonts[role], size * self.k)
        f.setSubpixel(True)
        return f

    # primitives (all in design pixels)
    def text(self, s: str, x: float, y: float, size: float = 28, role: str = "sans", colour: str | None = None,
             alpha: float = 1.0, align: str = "left") -> float:
        if alpha <= 0 or not s:
            return 0.0
        f = self.font(role, size)
        w = f.measureText(s)
        xx = x * self.k - (w if align == "right" else w / 2 if align == "center" else 0)
        self.canvas.drawString(s, xx, y * self.k, f, self.paint(colour or self.theme.ink, alpha))
        return w / self.k

    def rect(self, x, y, w, h, colour: str, alpha: float = 1.0, radius: float = 0, stroke: float = 0):
        if alpha <= 0:
            return
        sk = self.sk
        kw = {"Style": sk.Paint.kStroke_Style, "StrokeWidth": stroke * self.k} if stroke else {}
        r = sk.Rect.MakeXYWH(x * self.k, y * self.k, w * self.k, h * self.k)
        if radius:
            self.canvas.drawRRect(sk.RRect.MakeRectXY(r, radius * self.k, radius * self.k), self.paint(colour, alpha, **kw))
        else:
            self.canvas.drawRect(r, self.paint(colour, alpha, **kw))

    def line(self, x0, y0, x1, y1, colour: str, alpha: float = 1.0, width: float = 3):
        if alpha > 0:
            self.canvas.drawLine(x0 * self.k, y0 * self.k, x1 * self.k, y1 * self.k,
                                 self.paint(colour, alpha, StrokeWidth=width * self.k))

    def arrow(self, x0, y0, x1, y1, colour: str, alpha: float = 1.0, width: float = 3, progress: float = 1.0):
        """An arrow drawn from (x0, y0) toward (x1, y1); `progress` draws it partway (for arrows that grow)."""
        if alpha <= 0 or progress <= 0:
            return
        xe, ye = x0 + (x1 - x0) * progress, y0 + (y1 - y0) * progress
        self.line(x0, y0, xe, ye, colour, alpha, width)
        ang = math.atan2(ye - y0, xe - x0)
        for da in (2.6, -2.6):
            self.line(xe, ye, xe + 14 * math.cos(ang + da), ye + 14 * math.sin(ang + da), colour, alpha, width)

    def image(self, rgb: np.ndarray, x, y, w, h, alpha: float = 1.0, radius: float = 0, smooth: bool = True):
        """Draw an RGB uint8 array into a rect. Small arrays (feature maps) stay crisp with `smooth=False`."""
        if alpha <= 0:
            return
        sk = self.sk
        # Keyed by the array object, and the entry keeps the array alive: an id is only unique while its object
        # lives, so a cache keyed by bare ids hands a freed array's image to a new one (that made frames blink).
        hit = self._images.get(id(rgb))
        if hit is not None and hit[0] is rgb:
            img = hit[1]
        else:
            a = np.ascontiguousarray(rgb)
            if a.ndim == 2:
                a = np.stack([a] * 3, -1)
            rgba = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], -1)
            img = sk.Image.fromarray(np.ascontiguousarray(rgba), colorType=sk.kRGBA_8888_ColorType)
            self._images[id(rgb)] = (rgb, img)
        r = sk.Rect.MakeXYWH(x * self.k, y * self.k, w * self.k, h * self.k)
        p = self.sk.Paint(AntiAlias=True)
        p.setAlphaf(clamp(alpha))
        sampling = sk.SamplingOptions(sk.FilterMode.kLinear) if smooth else sk.SamplingOptions(sk.FilterMode.kNearest)
        self.canvas.save()
        if radius:
            self.canvas.clipRRect(sk.RRect.MakeRectXY(r, radius * self.k, radius * self.k), True)
        self.canvas.drawImageRect(img, r, sampling, p)
        self.canvas.restore()

    def heatmap(self, a: np.ndarray, x, y, w, h, alpha: float = 1.0, lo=None, hi=None, smooth: bool = False):
        self.image(self._cached_cmap(a, lo, hi), x, y, w, h, alpha, smooth=smooth)

    def _cached_cmap(self, a, lo, hi):
        key = ("cmap", id(a), lo, hi)
        hit = self._images.get(key)
        if hit is None or hit[0] is not a:
            hit = (a, colormap(a, lo, hi))
            self._images[key] = hit
        return hit[1]

    def box(self, x, y, w, h, colour: str, alpha: float = 1.0, width: float = 3, label: str = "", size: float = 22):
        """A detection box with an optional label chip above it."""
        self.rect(x, y, w, h, colour, alpha, stroke=width)
        if label:
            f = self.font("mono", size)
            tw = f.measureText(label) / self.k
            self.rect(x - width / 2, y - size - 12, tw + 16, size + 10, colour, alpha, radius=4)
            self.text(label, x + 8 - width / 2, y - 9, size, "mono", "#ffffff", alpha)

    def chip(self, s: str, x, y, colour: str, alpha: float = 1.0, size: float = 22, filled: bool = False):
        f = self.font("mono", size)
        tw = f.measureText(s) / self.k
        self.rect(x, y, tw + 20, size + 14, colour if filled else self.theme.surface, alpha, radius=(size + 14) / 2)
        if not filled:
            self.rect(x, y, tw + 20, size + 14, colour, alpha, radius=(size + 14) / 2, stroke=2)
        self.text(s, x + 10, y + size + 2, size, "mono", "#ffffff" if filled else colour, alpha)
        return tw + 20

    # camera: scale around a point, for one scene's shot
    def push(self, cx: float, cy: float, zoom: float) -> None:
        self.canvas.save()
        self.canvas.translate(960 * self.k, 540 * self.k)
        self.canvas.scale(zoom, zoom)
        self.canvas.translate(-cx * self.k, -cy * self.k)

    def pop(self) -> None:
        self.canvas.restore()


# ---------- the film ----------

def subtitle(ctx: Ctx, text: str, alpha: float) -> None:
    """Bottom-centre subtitle on a paper card, at most two lines."""
    if alpha <= 0:
        return
    f = ctx.font("sans", 32)
    words, lines, cur = text.split(), [], ""
    for w in words:
        nxt = f"{cur} {w}".strip()
        if cur and f.measureText(nxt) / ctx.k > 1400:
            lines.append(cur)
            cur = w
        else:
            cur = nxt
    lines.append(cur)
    lh = 44
    width = max(f.measureText(l) for l in lines) / ctx.k + 56
    h = lh * len(lines) + 28
    x, y = (1920 - width) / 2, 1080 - 36 - h
    ctx.rect(x, y, width, h, ctx.theme.surface, 0.95 * alpha, radius=14)
    for i, l in enumerate(lines):
        ctx.text(l, 960, y + 14 + lh * i + lh * 0.74, 32, "sans", ctx.theme.ink, alpha, align="center")


def reading_time(text: str) -> float:
    return 0.8 + 0.3 * len(text.split())


@dataclass
class Film:
    scenes: list[Scene]
    theme: Theme
    width: int = 1920
    height: int = 1080
    fps: int = 30
    chrome: Callable | None = None  # draw(ctx, t_film, scene, t_scene): persistent minimap, kicker, credits

    @property
    def starts(self) -> list[float]:
        out, t = [], 0.0
        for s in self.scenes:
            out.append(t)
            t += s.duration
        return out

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def at(self, t: float) -> tuple[Scene, float]:
        for s, t0 in zip(self.scenes, self.starts):
            if t < t0 + s.duration:
                return s, t - t0
        return self.scenes[-1], self.scenes[-1].duration

    def check(self) -> list[str]:
        """Subtitles that cannot be read in the time they get, or overlap; returned as messages."""
        problems = []
        for s in self.scenes:
            last = -1.0
            for t0, t1, text in s.subtitles:
                if t1 - t0 < reading_time(text) - 1e-6:
                    problems.append(f"{s.id}: {text[:40]!r} shows {t1 - t0:.1f}s, needs {reading_time(text):.1f}s")
                if t0 < last:
                    problems.append(f"{s.id}: {text[:40]!r} overlaps the subtitle before it")
                if t1 > s.duration + 1e-6:
                    problems.append(f"{s.id}: {text[:40]!r} runs past the scene's end")
                last = t1
        return problems

    def draw(self, ctx: Ctx, t: float) -> None:
        scene, ts = self.at(t)
        ctx.canvas.clear(hexcol(self.theme.ground))
        scene.draw(ctx, ts)
        if self.chrome:
            self.chrome(ctx, t, scene, ts)
        for t0, t1, text in scene.subtitles:
            subtitle(ctx, text, window(ts, t0, t1, 0.25))

    def cues(self, key: str = "D major") -> dict:
        events = []
        for s, t0 in zip(self.scenes, self.starts):
            events.append({"t": round(t0 + 0.05, 4), "kind": "command", "intensity": 0.45, "sharpness": 0.3})
            for t, kind, inten, sharp in s.cues:
                events.append({"t": round(t0 + t, 4), "kind": kind, "intensity": inten, "sharpness": sharp})
        events.sort(key=lambda e: e["t"])
        return {"version": 1, "duration": round(self.duration, 4), "fps": self.fps, "key": key, "events": events,
                "camera": []}

    def render(self, out: Path, fonts: dict, preview: list[float] | None = None, score: Path | None = None,
               loudness: float = -21.0, log=print) -> dict:
        import skia

        tf = {role: skia.Typeface.MakeFromFile(p) for role, p in fonts.items()}
        for role, face in tf.items():
            if face is None:
                raise RuntimeError(f"font `{role}` could not be loaded: {fonts[role]}")
        surface = skia.Surface(self.width, self.height)
        if preview is not None:
            paths = []
            for t in preview:
                with surface as canvas:
                    self.draw(Ctx(canvas, self.theme, self.width, self.height, tf), t)
                p = out.parent / f"{out.stem}-{t:07.2f}.png"
                surface.makeImageSnapshot().save(str(p), skia.kPNG)
                paths.append(p)
            return {"preview": [str(p) for p in paths]}
        n = int(round(self.duration * self.fps))
        video = out.with_suffix(".video.mp4")
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                                "-s", f"{self.width}x{self.height}", "-r", str(self.fps), "-i", "-",
                                "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(video)],
                               stdin=subprocess.PIPE)
        started = time.time()
        buf = np.empty((self.height, self.width, 4), np.uint8)
        try:
            with surface as canvas:
                ctx = Ctx(canvas, self.theme, self.width, self.height, tf)
                for f in range(n):
                    canvas.save()
                    self.draw(ctx, f / self.fps)
                    canvas.restore()
                    surface.readPixels(skia.ImageInfo.Make(self.width, self.height, skia.kRGBA_8888_ColorType,
                                                           skia.kUnpremul_AlphaType), buf)
                    enc.stdin.write(buf.tobytes())
        finally:
            enc.stdin.close()
            enc.wait()
        took = time.time() - started
        if score:
            ffmpeg("-i", str(video), "-i", str(score), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                   "-af", f"loudnorm=I={loudness}:TP=-2", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                   "-t", f"{self.duration}", str(out))
        else:
            ffmpeg("-i", str(video), "-c", "copy", str(out))
        log(f"  wrote {out.name}: {self.duration:.1f}s, {n} frames in {took:.1f}s ({n / max(took, 1e-6):.0f} fps)")
        return {"frames": n, "seconds": round(self.duration, 3), "render_seconds": round(took, 1)}
