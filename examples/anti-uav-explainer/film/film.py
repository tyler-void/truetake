"""Vision-Based Anti-UAV Detection and Tracking, as one continuous shot.

The method is a world: the tracker's lane on top, the detector's lane below, the rule where they meet. Each module
is a station; the camera travels between them and never cuts. A station keeps its last state after its moment, so
pulling back shows the path so far. Frame 42 is the actor: the tracker loses the drone on it, the detector takes it
in, shrinks it through the backbone, grows it back through the pyramid, and draws the box on it.

    python film.py [--preview t ...] [--cues] [--score score.wav]

Every picture is an asset extracted from running the models on DUT Anti-UAV video05; every number is read from those
files or from the paper, cited on screen. Motion: nothing appears faster than 0.35 s, crossfades take at least
0.6 s, and the camera eases; `python -m truetake.flash` checks the result for flashes.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
try:
    from truetake.film.explain import Film, Scene, Theme, ease, reading_time, tween
except ImportError:  # run from inside the truetake repository without installing it
    sys.path.insert(0, str(HERE.parents[1]))
    from truetake.film.explain import Film, Scene, Theme, ease, reading_time, tween  # noqa: E402

A = HERE / "assets"
M = json.loads((HERE / "manifest.json").read_text())
SEQ = M["sequence"]
LOG = json.loads((HERE.parent / "scout" / f"{SEQ}.frames.json").read_text())
IMAGES = HERE.parent / "data" / "images" / "Anti-UAV-Tracking-V0" / SEQ
F = HERE.parent / "fonts"
FONTS = {"display": str(F / "SpaceGrotesk-Bold.ttf"), "sans": str(F / "Inter-Regular.ttf"),
         "semi": str(F / "Inter-SemiBold.ttf"), "mono": str(F / "JetBrainsMono-Regular.ttf")}  # made by setup.sh

T = Theme(ground="#0d1117", surface="#161b22", ink="#e6edf3", muted="#8b949e", line="#30363d",
          modules={"backbone": "#58a6ff", "fpn": "#3fb950", "head": "#f0883e", "tracker": "#bc8cff",
                   "fusion": "#ff7b72"})
C, INK, MUTED, LINE = T.modules, T.ink, T.muted, T.line
GT_C = "#e3b341"  # ground truth: gold, never a module colour

_cache: dict[str, np.ndarray] = {}


def png(name):
    if name not in _cache:
        _cache[name] = np.asarray(Image.open(A / f"{name}.png").convert("RGB"))
    return _cache[name]


def npy(name):
    if name not in _cache:
        _cache[name] = np.load(A / f"{name}.npy")
    return _cache[name]


FRAME_HAND = M["handover_frame"]
SHOWN = FRAME_HAND + 1  # the dataset's own frame number
H0, W0 = png("det_frame").shape[:2]
GT = M["det_frame_gt"]
DET, TRK = M["detector"], M["tracker"]
SY, SX = DET["scale"]
IN_H, IN_W = DET["input_size"]
PAD_H, PAD_W = DET["batched_shape"][2:]
BEST = DET["detections"][0]
HAND, NEXT = TRK["hand"], TRK["next"]
SCORE_T, SCORE_D = HAND["score_t"], BEST[1]

# ---------- the world ----------
PX, PY = 2200, 1300  # station pitch in world units; a station is a 1920×1080 frame


def origin(col, row):
    return col * PX, row * PY


@dataclass
class Beat:
    id: str
    station: tuple[int, int]
    t0: float
    dur: float
    draw: Callable  # draw(ctx, tb) in the station's own 1920×1080 coordinates
    subs: list = field(default_factory=list)  # (offset, text)
    cues: list = field(default_factory=list)  # (offset, kind, intensity, sharpness)
    camera: tuple | None = None  # (cx, cy, zoom) in world units, overriding the station's framing


def frame_in(x, y, w):
    return x, y, w, w * H0 / W0


def to_rect(box, rect, sw=W0):
    s = rect[2] / sw
    return rect[0] + (box[0] - 1) * s, rect[1] + (box[1] - 1) * s, box[2] * s, box[3] * s


def label(ctx, s, x, y, colour=MUTED, a=1.0, size=24):
    ctx.text(s, x, y, size, "mono", colour, a)


def heading(ctx, tb, kicker, text, colour):
    a = ease(tb, 0.2, 0.6)
    ctx.rect(160, 92, 5, 26, colour, a)
    ctx.text(kicker.upper(), 180, 113, 22, "mono", colour, a)
    ctx.text(text, 160, 178, 52, "display", INK, ease(tb, 0.3, 0.7))


def gt_box(ctx, rect, box, a, text=""):
    x, y, w, h = to_rect(box, rect)
    ctx.rect(x - 4, y - 4, w + 8, h + 8, GT_C, a, radius=3, stroke=2.5)
    if text:
        label(ctx, text, x - 4, y - 14, GT_C, a)


# ---------- tracker lane ----------

def st_template(ctx, tb):
    heading(ctx, tb, "Tracker · SiamFC · frame 1", "It starts from one given box", C["tracker"])
    r = frame_in(160, 250, 1120)
    ctx.image(png("frame0"), *r, alpha=1.0, radius=8)  # already on screen since the opening: no fade, no dip
    g0 = TRK["gt0"]
    gt_box(ctx, r, g0, ease(tb, 0.6, 0.6), "the given box")
    x, y, w, h = to_rect(g0, r)
    side = TRK["exemplar_side_in_frame"] * r[2] / W0
    cx, cy = x + w / 2, y + h / 2
    ctx.rect(cx - side / 2, cy - side / 2, side, side, C["tracker"], ease(tb, 2.0, 0.6), radius=4, stroke=3)
    k = ease(tb, 2.8, 1.4, "inout")
    ex, ey, es = tween(tb, 2.8, 1.4, (cx - side / 2, cy - side / 2, side), (1400, 250, 300), "inout")
    ctx.image(png("exemplar"), ex, ey, es, es, alpha=min(1.0, k * 3), radius=6)
    label(ctx, "127 × 127 template", 1400, 584, C["tracker"], ease(tb, 4.0, 0.6))
    ctx.heatmap(npy("kernel_mean"), 1400, 640, 200, 200, alpha=ease(tb, 5.2, 0.8))
    ks = TRK["kernel_shape"]
    label(ctx, f"its features: {ks[2]} × {ks[3]} × {ks[1]}", 1400, 874, C["tracker"], ease(tb, 5.4, 0.6))


def st_search(ctx, tb):
    heading(ctx, tb, "Tracker · frame 2", "It searches around the last position", C["tracker"])
    r = frame_in(160, 250, 960)
    ctx.image(png("next_frame"), *r, alpha=ease(tb, 0.0, 0.8), radius=8)
    s = r[2] / W0
    cy, cx = NEXT["search_center"]
    side = NEXT["search_side_in_frame"] * s
    x0, y0 = r[0] + cx * s - side / 2, r[1] + cy * s - side / 2
    ctx.rect(x0, y0, side, side, C["tracker"], ease(tb, 0.8, 0.6), radius=4, stroke=3)
    k = ease(tb, 1.6, 1.3, "inout")
    sx, sy, ss = tween(tb, 1.6, 1.3, (x0, y0, side), (1220, 250, 250), "inout")
    ctx.image(png("next_search"), sx, sy, ss, ss, alpha=min(1.0, k * 3), radius=6)
    label(ctx, "255 × 255 search", 1220, 530, C["tracker"], ease(tb, 2.9, 0.6))
    feats = npy("next_features")
    fx, fy, fs = 1500, 250, 250
    fa = ease(tb, 3.4, 0.8)
    ctx.heatmap(feats, fx, fy, fs, fs, alpha=fa)
    label(ctx, f"features {feats.shape[0]} × {feats.shape[1]}", fx, 530, C["tracker"], fa)
    # the template's 6×6 window glides over the 22×22 features; the response fills in softly behind it
    raw = npy("next_resp_raw")
    n = raw.shape[0]
    p = ease(tb, 4.4, 5.0, "inout") * (n * n - 1)
    row, col = divmod(p, n)
    cell = fs / feats.shape[0]
    wa = fa * (1 - ease(tb, 9.6, 0.6))
    ctx.rect(fx + col * cell, fy + int(row) * cell, 6 * cell, 6 * cell, INK, wa * 0.9, radius=3, stroke=2)
    rx, ry, rs = 1220, 600, 250
    ra = ease(tb, 4.2, 0.6)
    done = np.clip((p - np.arange(n * n)) / 6.0, 0, 1).reshape(n, n)  # each cell fades in over ~6 steps
    lo, hi = float(raw.min()), float(raw.max())
    ctx.rect(rx, ry, rs, rs, LINE, ra, stroke=1.5)
    for i in range(n):
        for j in range(n):
            if done[i, j] > 0:
                ctx.heatmap(raw[i:i + 1, j:j + 1], rx + j * rs / n, ry + i * rs / n, rs / n + 0.5, rs / n + 0.5,
                            alpha=ra * done[i, j], lo=lo, hi=hi)
    label(ctx, f"response {n} × {n}", rx, ry + rs + 34, C["tracker"], ra)
    up = npy("next_resp_final")
    ua = ease(tb, 10.0, 0.8)
    ctx.heatmap(up, 1500, 600, 250, 250, alpha=ua, smooth=True)
    py, px = NEXT["peak"]
    ctx.rect(1500 + px * 250 / up.shape[1] - 8, 600 + py * 250 / up.shape[0] - 8, 16, 16, INK, ua, radius=8, stroke=2.5)
    label(ctx, "peak = where it moved", 1500, 884, C["tracker"], ua)
    bx, by, bw, bh = to_rect(NEXT["bbox_t"], r)
    ctx.rect(bx, by, bw, bh, C["tracker"], ease(tb, 11.0, 0.6), radius=2, stroke=3)


def gauge(ctx, x, y, w, v, a):
    ctx.rect(x, y, w, 14, LINE, a, radius=7)
    ctx.rect(x, y, w * v, 14, C["tracker"], a, radius=7)
    tx = x + w * 0.9
    ctx.rect(tx - 1.5, y - 16, 3, 46, C["fusion"], a)
    label(ctx, "τt = 0.9", tx - 44, y - 26, C["fusion"], a)
    ctx.text(f"{v:.3f}", x, y + 64, 44, "display", INK, a)
    label(ctx, "tracker confidence", x + 170, y + 56, MUTED, a)


def st_lost(ctx, tb):
    heading(ctx, tb, f"Tracker · frame {SHOWN}", "Now it is searching a rooftop", C["tracker"])
    r = frame_in(160, 250, 1120)
    ctx.image(png("hand_frame"), *r, alpha=ease(tb, 0.0, 0.8), radius=8)
    s = r[2] / W0
    cy, cx = HAND["search_center"]
    side = HAND["search_side_in_frame"] * s
    ctx.rect(r[0] + cx * s - side / 2, r[1] + cy * s - side / 2, side, side, C["tracker"], ease(tb, 0.8, 0.6), radius=4, stroke=3)
    gt_box(ctx, r, LOG[FRAME_HAND]["gt"], ease(tb, 2.4, 0.7), "the drone")
    ctx.heatmap(npy("hand_resp_final"), 1400, 250, 300, 300, alpha=ease(tb, 3.4, 0.8), smooth=True)
    label(ctx, "its response: broad, unsure", 1400, 590, C["tracker"], ease(tb, 3.6, 0.6))
    v = tween(tb, 4.8, 2.2, 1.0, SCORE_T, "inout")
    gauge(ctx, 1400, 690, 360, v, ease(tb, 4.6, 0.6))


def st_gate1(ctx, tb):
    heading(ctx, tb, "The rule · Algorithm 1", "Below 0.9, ask the detector", C["fusion"])
    a = ease(tb, 0.6, 0.7)
    ctx.text(f"{SCORE_T:.3f}", 160, 470, 150, "display", C["tracker"], a)
    ctx.text("<", 760, 450, 110, "display", MUTED, ease(tb, 1.2, 0.6))
    ctx.text("0.9", 900, 470, 150, "display", C["fusion"], ease(tb, 1.6, 0.6))
    label(ctx, "score_t, the tracker's confidence", 160, 540, C["tracker"], a)
    label(ctx, "τt, the paper's threshold (Sec. IV-C)", 900, 540, C["fusion"], ease(tb, 1.6, 0.6))
    label(ctx, "Confidence: the response's peak sharpness (APCE) against the first frames.", 160, 700, MUTED, ease(tb, 3.0, 0.6))
    label(ctx, "The paper does not define it; this is our choice.", 160, 734, MUTED, ease(tb, 3.0, 0.6))
    ctx.arrow(1640, 560, 1640, 860, C["backbone"], ease(tb, 4.0, 0.5), 3, ease(tb, 4.0, 1.0))
    label(ctx, "to the detector", 1664, 720, C["backbone"], ease(tb, 4.0, 0.5))


# ---------- detector lane ----------

def st_resize(ctx, tb):
    heading(ctx, tb, f"Detector · Faster R-CNN · frame {SHOWN}", "Resize, then pad", C["backbone"])
    s = 720 / W0
    a = ease(tb, 0.0, 0.8)
    ctx.image(png("det_frame"), 160, 300, W0 * s, H0 * s, alpha=a, radius=6)
    label(ctx, f"{W0} × {H0}", 160, 300 + H0 * s + 36, MUTED, a)
    ctx.arrow(920, 500, 1000, 500, MUTED, ease(tb, 1.6, 0.5), 3, ease(tb, 1.6, 0.6))
    a1 = ease(tb, 2.2, 0.9)
    ctx.image(png("det_input"), 1040, 300, PAD_W * s, PAD_H * s, alpha=a1, radius=6)
    ctx.rect(1040 + IN_W * s, 300, (PAD_W - IN_W) * s, PAD_H * s, C["backbone"], a1 * 0.35)
    ctx.rect(1040, 300 + IN_H * s, PAD_W * s, (PAD_H - IN_H) * s, C["backbone"], a1 * 0.35)
    label(ctx, f"{IN_W} × {IN_H}, padded to {PAD_W} × {PAD_H}", 1040, 300 + PAD_H * s + 36, C["backbone"], ease(tb, 3.0, 0.6))


STAGES = [("c2_mean", "C2", 4, 256), ("c3_mean", "C3", 8, 512), ("c4_mean", "C4", 16, 1024), ("c5_mean", "C5", 32, 2048)]
STOPS = [(160, 560), (780, 392), (1232, 274), (1566, 192)]  # x and width of each stop: 0.7× each, inside the margins


def sheet(ctx, arr, x, y, w, a, mix):
    """The input picture under a feature map; `mix` 0 shows the picture, 1 the map over it."""
    h = w * PAD_H / PAD_W
    ctx.image(png("det_input"), x, y, w, h, alpha=a)
    if mix > 0:
        ctx.heatmap(arr, x, y, w, h, alpha=a * mix * 0.85)
    ctx.rect(x, y, w, h, LINE, a, stroke=1.2)
    # the drone, mapped into the input and then into this sheet
    sc = w / PAD_W
    gx, gy, gw, gh = (GT[0] - 1) * SX * sc, (GT[1] - 1) * SY * sc, GT[2] * SX * sc, GT[3] * SY * sc
    ctx.rect(x + gx - 3, y + gy - 3, gw + 6, gh + 6, GT_C, a * 0.9, radius=2, stroke=2)
    return h


def st_backbone(ctx, tb):
    heading(ctx, tb, "Detector · ResNet-50", "The picture shrinks, the channels grow", C["backbone"])
    cy = 520
    for i, ((name, lab, stride, ch), (x, w)) in enumerate(zip(STAGES, STOPS)):
        t_in = 0.8 + i * 2.2
        if tb < t_in:
            break
        # the travelling sheet: from the previous stop to this one, shrinking, its map crossfading in
        if i == 0:
            px, pw = x, w
        else:
            px, pw = STOPS[i - 1]
        k = ease(tb, t_in, 1.4, "inout")
        xx, ww = px + (x - px) * k, pw + (w - pw) * k
        hh = ww * PAD_H / PAD_W
        mix = ease(tb, t_in + (0.0 if i == 0 else 0.8), 0.9)
        sheet(ctx, npy(name), xx, cy - hh / 2, ww, ease(tb, t_in, 0.6), mix)
        if k >= 1:
            arr = npy(name)
            label(ctx, f"{lab} · stride {stride}", x, cy - hh / 2 - 18, C["backbone"], ease(tb, t_in + 1.2, 0.6))
            label(ctx, f"{arr.shape[0]} × {arr.shape[1]} × {ch}", x, cy + hh / 2 + 36, MUTED, ease(tb, t_in + 1.4, 0.6))
    label(ctx, "each map: the mean over its channels  ·  gold: the drone", 160, 880, MUTED, ease(tb, 9.6, 0.8))


PYR = [("p_pool_mean", "P6", 21), ("p_3_mean", "P5", 42), ("p_2_mean", "P4", 84), ("p_1_mean", "P3", 168), ("p_0_mean", "P2", 336)]
PSTOPS = [(1640, 120), (1440, 170), (1180, 240), (840, 320), (160, 560)]


def st_pyramid(ctx, tb):
    heading(ctx, tb, "Detector · feature pyramid", "And grows back, 256 channels at every size", C["fpn"])
    cy = 520
    for i, ((name, lab, _), (x, w)) in enumerate(zip(PYR, PSTOPS)):
        t_in = 0.8 + i * 1.8
        if tb < t_in:
            break
        px, pw = (x, w) if i == 0 else PSTOPS[i - 1]
        k = ease(tb, t_in, 1.3, "inout")
        xx, ww = px + (x - px) * k, pw + (w - pw) * k
        hh = ww * PAD_H / PAD_W
        sheet(ctx, npy(name), xx, cy - hh / 2, ww, ease(tb, t_in, 0.6), ease(tb, t_in, 0.8))
        if k >= 1:
            arr = npy(name)
            label(ctx, lab, x, cy - hh / 2 - 18, C["fpn"], ease(tb, t_in + 1.0, 0.6))
            label(ctx, f"{arr.shape[0]} × {arr.shape[1]}", x, cy + hh / 2 + 36, MUTED, ease(tb, t_in + 1.1, 0.6))
    label(ctx, "each level: the deeper map enlarged ×2, added to this one", 160, 880, MUTED, ease(tb, 9.0, 0.8))


def st_proposals(ctx, tb):
    heading(ctx, tb, "Detector · region proposals", "Where could something be?", C["fpn"])
    w = 1100
    h = w * PAD_H / PAD_W
    x, y = 160, 250
    a = ease(tb, 0.0, 0.8)
    ctx.image(png("det_input"), x, y, w, h, alpha=a, radius=6)
    obj = npy("rpn_obj_0")
    ctx.heatmap(obj, x, y, w, h, alpha=a * tween(tb, 4.6, 1.2, 0.8, 0.25), lo=0.0, hi=1.0, smooth=True)
    label(ctx, "objectness on P2, 0 → 1", x, y + h + 36, C["fpn"], ease(tb, 1.0, 0.6))
    props = npy("proposals")
    s = w / PAD_W
    for i, p in enumerate(props[:30]):
        ai = ease(tb, 5.0 + i * 0.1, 0.6) * 0.7
        ctx.rect(x + p[0] * s, y + p[1] * s, (p[2] - p[0]) * s, (p[3] - p[1]) * s, C["fpn"], ai, stroke=1.4)
    ri = DET["roi_proposal"]
    p = ri["box_resized"]
    ab = ease(tb, 9.0, 0.7)
    ctx.rect(x + p[0] * s - 4, y + p[1] * s - 4, (p[2] - p[0]) * s + 8, (p[3] - p[1]) * s + 8, C["head"], ab, radius=3, stroke=3)
    label(ctx, f"the top 30 of {len(props)} proposals", 1320, 300, C["fpn"], ease(tb, 5.0, 0.6))
    label(ctx, f"this one covers the drone", 1320, 360, C["head"], ab)
    label(ctx, f"IoU {ri['iou_with_gt']:.2f} with the ground truth", 1320, 396, MUTED, ab)


def st_roi(ctx, tb):
    heading(ctx, tb, "Detector · RoI head", "Look closer, then name it", C["head"])
    p = DET["roi_proposal"]["box_resized"]
    cx, cy, side = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2, max(p[2] - p[0], p[3] - p[1]) * 2.4
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    crop = png("det_input")[y0:int(y0 + side), x0:int(x0 + side)]
    a = ease(tb, 0.0, 0.8)
    ctx.image(crop, 160, 280, 420, 420 * crop.shape[0] / crop.shape[1], alpha=a, radius=8)
    label(ctx, "the proposal", 160, 760, C["head"], a)
    ctx.arrow(620, 490, 720, 490, MUTED, ease(tb, 1.6, 0.5), 3, ease(tb, 1.6, 0.6))
    a2 = ease(tb, 2.2, 0.8)
    ctx.heatmap(npy("roi_feature"), 760, 310, 360, 360, alpha=a2)
    for k in range(8):
        ctx.line(760 + k * 360 / 7, 310, 760 + k * 360 / 7, 670, T.ground, a2 * 0.8, 1.5)
        ctx.line(760, 310 + k * 360 / 7, 1120, 310 + k * 360 / 7, T.ground, a2 * 0.8, 1.5)
    label(ctx, "7 × 7 × 256 features", 760, 760, C["head"], a2)
    ctx.arrow(1160, 490, 1260, 490, MUTED, ease(tb, 3.6, 0.5), 3, ease(tb, 3.6, 0.6))
    for i, (name, prob) in enumerate(DET["roi_top5"][:4]):
        at = 4.2 + i * 0.5
        ai = ease(tb, at, 0.6)
        yy = 330 + i * 92
        ctx.text("background" if name == "__background__" else name, 1300, yy + 28, 30, "sans", INK, ai)
        bw = 360 * prob * ease(tb, at, 1.0)
        ctx.rect(1300, yy + 42, max(bw, 3), 14, C["head"] if i == 0 else LINE, ai, radius=7)
        label(ctx, f"{prob:.3f}", 1310 + max(bw, 3), yy + 56, MUTED, ai)


def st_output(ctx, tb):
    heading(ctx, tb, "Detector · output", "One box, back on the picture", C["head"])
    r = frame_in(160, 250, 1120)
    ctx.image(png("det_frame"), *r, alpha=ease(tb, 0.0, 0.8), radius=8)
    boxes, names = npy("all_boxes"), DET["post_nms_names"]
    keep = {"airplane", "bird", "kite"}
    fade = ease(tb, 4.4, 1.2)
    s = r[2] / W0
    for i, (b, nm) in enumerate(zip(boxes, names)):
        ai = ease(tb, 1.0 + i * 0.04, 0.6) * (1 if nm in keep else 1 - fade)
        ctx.rect(r[0] + b[0] * s, r[1] + b[1] * s, (b[2] - b[0]) * s, (b[3] - b[1]) * s,
                 C["backbone"] if nm in keep else MUTED, ai * 0.8, stroke=1.6)
    bx, by, bw, bh = to_rect(BEST[0], r)
    ab = ease(tb, 6.0, 0.7)
    ctx.box(bx - 2, by - 2, bw + 4, bh + 4, C["backbone"], ab, width=3, label=f"{BEST[2]} {BEST[1]:.3f}", size=20)
    label(ctx, f"{len(boxes)} boxes after overlaps are removed", 1340, 300, MUTED, ease(tb, 1.0, 0.6))
    label(ctx, "COCO has no drone class:", 1340, 360, C["backbone"], ease(tb, 4.4, 0.6))
    label(ctx, "only airplane, bird, kite count", 1340, 394, C["backbone"], ease(tb, 4.4, 0.6))
    label(ctx, f"mapped back ÷ {SX:.3f} to {W0} × {H0}", 1340, 454, C["head"], ease(tb, 7.0, 0.6))


# ---------- the rule decides, and what it does ----------

def st_gate2(ctx, tb):
    heading(ctx, tb, "The rule · Algorithm 1", "The detector's box wins", C["fusion"])
    rows = [(f"{SCORE_D:.3f}  >  0.9", "the detector beats τd", C["backbone"], 0.6),
            (f"{SCORE_D:.3f}  >  {SCORE_T:.3f}", "and beats the tracker", C["backbone"], 2.0)]
    for i, (big, small, col, at) in enumerate(rows):
        a = ease(tb, at, 0.7)
        ctx.text(big, 160, 380 + i * 190, 84, "display", col, a)
        label(ctx, small, 160, 430 + i * 190, MUTED, a, 22)
    r = frame_in(1060, 280, 700)
    ctx.image(png("hand_frame"), *r, alpha=ease(tb, 0.3, 0.8), radius=8)
    tx, ty, tw, th = to_rect(HAND["bbox_t"], r)
    ctx.rect(tx, ty, tw, th, C["tracker"], ease(tb, 0.6, 0.6) * (1 - ease(tb, 4.0, 1.0)), radius=2, stroke=3)
    bx, by, bw, bh = to_rect(BEST[0], r)
    ctx.box(bx - 2, by - 2, bw + 4, bh + 4, C["backbone"], ease(tb, 4.0, 1.0), width=3, label="result", size=18)
    label(ctx, f"frame {SHOWN}", 1060, r[1] + r[3] + 36, MUTED, ease(tb, 0.3, 0.8))


_frames: list[np.ndarray] = []


def seq_frames():
    if not _frames:
        for f in sorted(IMAGES.glob("*.jpg"))[: len(LOG)][::2]:
            _frames.append(np.asarray(Image.open(f).convert("RGB").resize((720, 405), Image.BILINEAR)))
    return _frames


def st_every(ctx, tb):
    heading(ctx, tb, f"DUT Anti-UAV {SEQ} · {len(LOG)} frames · our run", "Every frame, side by side", C["fusion"])
    frames = seq_frames()
    play = min(1.0, max(0.0, (tb - 1.4) / 10.0))
    k = int(play * (len(frames) - 1))
    rec = LOG[min(k * 2, len(LOG) - 1)]
    for x, key, name, col in ((160, "alone", "the tracker alone", C["tracker"]),
                              (1040, "result", "with the paper's rule", C["fusion"])):
        a = ease(tb, 0.4, 0.8)
        ctx.image(frames[k], x, 260, 720, 405, alpha=a, radius=8)
        ctx.text(name, x, 240, 26, "semi", col, a)
        box = rec.get(key) or rec["gt"]
        bx, by, bw, bh = to_rect(box, (x, 260, 720, 405))
        ctx.rect(bx, by, bw, bh, col, a, radius=2, stroke=3)
        iou = rec.get("iou_alone" if key == "alone" else "iou_fused", 1.0)
        label(ctx, f"overlap with the true box {iou:.2f}", x, 712, MUTED, a)
    x0, y0, w = 160, 780, 1600
    n = len(LOG)
    a = ease(tb, 0.8, 0.8)
    for i, r in enumerate(LOG):
        src = r.get("source")
        col = C["fusion"] if src == "detector" else ("#6e5a9e" if "dets" in r else C["tracker"])
        ctx.rect(x0 + i * w / n, y0, max(w / n, 1.2), 26, col, a)
    ctx.rect(x0 + k * 2 * w / n - 1.5, y0 - 8, 3, 42, INK, a)
    label(ctx, "tracker", x0, y0 + 66, C["tracker"], a)
    label(ctx, "unsure, tracker kept", x0 + 160, y0 + 66, "#a08bd6", a)
    label(ctx, "detector took over", x0 + 480, y0 + 66, C["fusion"], a)


def st_result(ctx, tb):
    heading(ctx, tb, "Zhao et al., Table III · their retrained detectors", "What the rule buys", C["fusion"])
    rows = [("Success", 0.381, 0.616), ("Norm Pre", 0.526, 0.804), ("Precision", 0.623, 0.937)]
    for i, (name, before, after) in enumerate(rows):
        y = 320 + i * 170
        a = ease(tb, 0.6 + i * 0.5, 0.7)
        ctx.text(name, 160, y + 34, 32, "sans", INK, a)
        w0 = 1000 * before * ease(tb, 0.8 + i * 0.5, 1.2)
        ctx.rect(460, y, w0, 30, C["tracker"], a, radius=6)
        label(ctx, f"SiamFC {before:.3f}", 476 + w0, y + 24, MUTED, a)
        a1 = ease(tb, 3.0 + i * 0.5, 0.7)
        w1 = 1000 * after * ease(tb, 3.0 + i * 0.5, 1.4)
        ctx.rect(460, y + 46, w1, 30, C["fusion"], a1, radius=6)
        label(ctx, f"with Faster R-CNN ResNet-50  {after:.3f}", 476 + w1, y + 70, MUTED, a1)


def st_end(ctx, tb):
    a = ease(tb, 0.4, 0.9)
    ctx.text("Vision-Based Anti-UAV Detection and Tracking", 160, 330, 58, "display", INK, a)
    ctx.text("Zhao, Zhang, Li, Wang · IEEE T-ITS 23(12), 2022 · arXiv 2205.10851", 160, 392, 26, "sans", MUTED, a)
    lines = [f"Data      DUT Anti-UAV tracking set, {SEQ}",
             "Detector  torchvision Faster R-CNN ResNet-50 FPN, COCO weights (the paper retrained it)",
             "Tracker   SiamFC, public weights",
             "Rule      Algorithm 1, re-implemented; confidence = APCE vs. the first frames (our choice)"]
    for i, l in enumerate(lines):
        label(ctx, l, 160, 500 + i * 48, MUTED, ease(tb, 1.2 + i * 0.4, 0.8), 22)
    ctx.text("Every picture is a real intermediate of these models on this video.", 160, 740, 28, "semi", INK, ease(tb, 3.0, 0.8))


# ---------- the shot ----------

ROW_T, ROW_D = 0, 1
STATIONS = {"template": (0, ROW_T), "search": (1, ROW_T), "lost": (2, ROW_T), "gate1": (3, ROW_T),
            "resize": (3, ROW_D), "backbone": (4, ROW_D), "pyramid": (5, ROW_D), "proposals": (6, ROW_D),
            "roi": (7, ROW_D), "output": (8, ROW_D), "gate2": (8, ROW_T), "every": (9, ROW_T),
            "result": (10, ROW_T), "end": (11, ROW_T)}
DRAW = {"template": st_template, "search": st_search, "lost": st_lost, "gate1": st_gate1, "resize": st_resize,
        "backbone": st_backbone, "pyramid": st_pyramid, "proposals": st_proposals, "roi": st_roi,
        "output": st_output, "gate2": st_gate2, "every": st_every, "result": st_result, "end": st_end}
TRAVEL = 2.0  # seconds of camera travel into each station
LEAD = 1.2  # a station's own clock starts this long before the camera arrives


def g0():  # where the given box is, in world units, for the opening close-up
    r = frame_in(160, 250, 1120)
    x, y, w, h = to_rect(TRK["gt0"], r)
    return x + w / 2, y + h / 2


PLAN = [  # (station, seconds after arrival, subtitles as (offset, text), cues as (offset, kind, intensity, sharpness))
    ("template", 9.0, [(2.4, f"A drone, {int(TRK['gt0'][2])} by {int(TRK['gt0'][3])} pixels, at the start of a video."),
                       (6.4, "The tracker cuts a template around the box it is given.")], [(4.8, "accent", 0.5, 0.4)]),
    ("search", 12.5, [(0.6, "On the next frame it searches around the last position."),
                      (4.6, "It slides the template's features across, and keeps the strongest response.")], []),
    ("lost", 9.5, [(0.6, f"By frame {SHOWN} the camera has panned. The tracker searches a rooftop."),
                   (5.0, f"Its response is broad. Confidence falls to {SCORE_T:.3f}.")], []),
    ("gate1", 7.0, [(0.6, "The paper's rule: below 0.9, the detector looks at the whole frame.")], [(1.6, "accent", 0.7, 0.5)]),
    ("resize", 7.5, [(0.6, f"The detector resizes the frame to {IN_W} by {IN_H}, then pads it.")], []),
    ("backbone", 11.0, [(0.6, "ResNet-50 halves the picture four times while its channels grow."),
                        (5.6, "By the last stage, the drone is one or two cells.")], []),
    ("pyramid", 10.5, [(0.6, "The feature pyramid adds the deep maps back onto the finer ones."),
                       (5.2, "Five sizes, 256 channels each.")], []),
    ("proposals", 11.0, [(0.6, "The proposal network scores every position. The drone already glows."),
                         (5.4, "Its best proposals land on the picture; one covers the drone.")], []),
    ("roi", 9.0, [(0.6, "That proposal becomes a 7 by 7 grid of features, then a class."),
                  (4.6, "COCO has no drone class. The closest is airplane.")], []),
    ("output", 9.5, [(0.6, f"Of {len(npy('all_boxes'))} boxes, only flying classes count."),
                     (5.6, f"The best is airplane at {SCORE_D:.3f}, back on the original pixels.")], []),
    ("gate2", 8.5, [(0.6, f"{SCORE_D:.3f} beats 0.9, and it beats the tracker's {SCORE_T:.3f}."),
                    (4.4, "So the detector's box becomes the result.")], [(4.0, "accent", 1.0, 0.6)]),
    ("every", 12.5, [(1.4, "The whole video. Left: the tracker alone. Right: with the rule."),
                     (6.8, "The detector took over once, and the drone was found again.")], []),
    ("result", 8.5, [(0.6, "In the paper, the rule lifts SiamFC's Success from 0.381 to 0.616."),
                     (4.8, "Their detector was retrained on this data. Ours keeps COCO weights.")], [(3.0, "accent", 0.6, 0.5)]),
    ("end", 7.0, [], []),
]

OPENING = 6.0  # the close-up on the drone, pulling back to frame 1, before the tracker's station starts


def build_beats() -> list[Beat]:
    beats, t = [], OPENING
    for i, (st, dur, subs, cues) in enumerate(PLAN):
        travel = 0.0 if i == 0 else TRAVEL
        beats.append(Beat(st, STATIONS[st], t + travel, dur, DRAW[st], subs, cues))
        t += travel + dur
    return beats


BEATS = build_beats()
TOTAL = BEATS[-1].t0 + BEATS[-1].dur


def station_center(st):
    ox, oy = origin(*STATIONS[st])
    return ox + 960, oy + 540


def camera(t: float) -> tuple[float, float, float]:
    """Eased travel between stations, with a slight pull-back on long moves; an opening push-out from the drone."""
    first = BEATS[0]
    ox, oy = origin(*first.station)
    if t < OPENING:
        gx, gy = g0()
        z = tween(t, 0.6, OPENING - 0.8, 7.0, 1.0, "inout")
        cx, cy = tween(t, 0.6, OPENING - 0.8, (ox + gx, oy + gy), (ox + 960, oy + 540), "inout")
        return cx, cy, z
    prev = BEATS[0]
    for b in BEATS[1:]:
        arrive_start = b.t0 - TRAVEL
        if t < arrive_start:
            break
        if t < b.t0:
            (x0, y0), (x1, y1) = station_center(prev.id), station_center(b.id)
            k = ease(t, arrive_start, TRAVEL, "inout")
            dist = math.hypot(x1 - x0, y1 - y0)
            dip = min(0.35, dist / PX * 0.12) * math.sin(math.pi * k)
            return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, 1.0 - dip
        prev = b
    x, y = station_center(prev.id)
    return x, y, 1.0


def world(ctx, t: float):
    cx, cy, z = camera(t)
    k = ctx.k
    cv = ctx.canvas
    cv.save()
    cv.translate(960 * k, 540 * k)
    cv.scale(z, z)
    cv.translate(-cx * k, -cy * k)
    vw, vh = 1920 / z, 1080 / z
    for b in BEATS:
        # a station starts drawing LEAD seconds before the camera arrives, so it settles in while the camera glides
        start = b.t0 - (0.0 if b is BEATS[0] else LEAD)
        opening = b is BEATS[0] and t < b.t0
        if t < start and not opening:
            continue
        ox, oy = origin(*b.station)
        if ox + 1920 < cx - vw / 2 or ox > cx + vw / 2 or oy + 1080 < cy - vh / 2 or oy > cy + vh / 2:
            continue
        cv.save()
        cv.translate(ox * k, oy * k)
        # once the camera leaves a station it dims, so pans never sweep bright pictures across the screen
        dim = 1.0 - 0.7 * ease(t, b.t0 + b.dur, 0.8) if b is not BEATS[-1] else 1.0
        if dim < 0.999:
            cv.saveLayerAlpha(ctx.sk.Rect.MakeXYWH(0, 0, 1920 * k, 1080 * k), int(255 * dim))
        if opening:  # the opening shows frame 1 alone
            ctx.image(png("frame0"), *frame_in(160, 250, 1120), alpha=ease(t, 0.0, 1.0), radius=8)
        else:
            b.draw(ctx, min(b.dur + LEAD, t - start))
        if dim < 0.999:
            cv.restore()
        cv.restore()
    cv.restore()


def chrome(ctx, t_film, scene, ts):
    ctx.text("VISION-BASED ANTI-UAV DETECTION AND TRACKING · ZHAO ET AL. 2022", 1760, 56, 16, "mono", MUTED,
             0.85 * ease(t_film, 1.0, 1.0) * (1 - ease(t_film, TOTAL - 7.5, 1.0)), align="right")


def build() -> Film:
    subs, last = [], -1.0
    for b in BEATS:
        for off, text in b.subs:
            start = max(b.t0 + off, last + 0.25)
            end = start + reading_time(text)
            subs.append((start, end, text))
            last = end
    total = max(TOTAL, last + 0.8)
    scene = Scene("shot", total, world, "", subs,
                  [(b.t0 + off, kind, i, s) for b in BEATS for off, kind, i, s in b.cues])
    return Film([scene], T, 1920, 1080, 30, chrome=chrome)


def cue_list(film: Film) -> dict:
    """Sections for the arranger: the music follows the story's arc, not the UI's events."""
    at = {b.id: b.t0 for b in BEATS}
    sections = [{"t": 0.0, "progression": [0, 5, 3, 4], "intensity": 0.25},
                {"t": at["search"] - TRAVEL, "progression": [0, 4, 5, 3], "intensity": 0.45},
                {"t": at["lost"] - TRAVEL, "progression": [5, 3, 1, 4], "intensity": 0.6},
                {"t": at["resize"] - TRAVEL, "progression": [0, 3, 4, 5], "intensity": 0.5},
                {"t": at["proposals"] - TRAVEL, "progression": [5, 3, 4, 4], "intensity": 0.7},
                {"t": at["gate2"] - TRAVEL, "progression": [3, 4, 0, 0], "intensity": 0.9},
                {"t": at["result"] - TRAVEL, "progression": [3, 4, 0], "intensity": 0.5},
                {"t": at["end"] - TRAVEL, "progression": [0, 3, 0], "intensity": 0.25}]
    events = [{"t": round(t, 3), "kind": k, "intensity": i, "sharpness": s}
              for t, k, i, s in sorted(film.scenes[0].cues)]
    return {"version": 1, "duration": round(film.duration, 3), "fps": 30, "key": "D major", "tempo": 72,
            "sections": sections, "events": events}


def main():
    film = build()
    for p in film.check():
        print("subtitle:", p)
    args = sys.argv[1:]
    if "--cues" in args:
        (HERE / "cues.json").write_text(json.dumps(cue_list(film), indent=1))
        print(f"cues.json, {film.duration:.1f}s")
        return
    if "--preview" in args:
        times = [float(x) for x in args[args.index("--preview") + 1:]]
        out = HERE / "preview" / "p.png"
        out.parent.mkdir(exist_ok=True)
        print(film.render(out, FONTS, preview=times))
        return
    score = Path(args[args.index("--score") + 1]) if "--score" in args else None
    print(film.render(HERE / "anti-uav-explainer.mp4", FONTS, score=score))


if __name__ == "__main__":
    main()
