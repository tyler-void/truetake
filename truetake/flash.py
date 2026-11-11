"""Photosensitivity check: no part of the video may flash more than three times in any one second.

An approximation of WCAG 2.3.1 ("Three Flashes or Below Threshold"), not a certified Harding test. The frame is cut
into a grid of cells, each about the size of the guideline's small safe area (a quarter of a 10° field of view at a
typical viewing distance, about 1/24 of a 1080p screen). In each cell, the mean relative luminance is tracked over
time; a transition is a change of at least 10% of full luminance where the darker side is below 0.8; a flash is a
pair of opposing transitions. Saturated red (red ≥ 80% of R+G+B) changing by the same amount counts as a red flash.
Any cell with more than three flashes in a one-second window fails.

    python -m truetake.flash video.mp4
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

W, H = 192, 108  # analysis size
GRID = (6, 4)  # columns, rows: 24 cells


def _frames(video: Path):
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"scale={W}:{H}:flags=area",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = W * H * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    proc.wait()


def _fps(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                          "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip()
    num, den = out.split("/")
    return float(num) / float(den)


def _linear(c: np.ndarray) -> np.ndarray:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def series(video: Path) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell mean relative luminance and red share, one row per frame: shapes (frames, cells)."""
    cols, rows = GRID
    lum, red = [], []
    for f in _frames(video):
        lin = _linear(f.astype(np.float32))
        y = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
        s = f.astype(np.float32).sum(-1) + 1e-6
        r = np.clip((f[..., 0] / s - 0.8) / 0.2, 0, 1) * lin[..., 0]  # saturated red only
        cell_l, cell_r = [], []
        for j in range(rows):
            for i in range(cols):
                ys, xs = slice(j * H // rows, (j + 1) * H // rows), slice(i * W // cols, (i + 1) * W // cols)
                cell_l.append(float(y[ys, xs].mean()))
                cell_r.append(float(r[ys, xs].mean()))
        lum.append(cell_l)
        red.append(cell_r)
    return np.array(lum), np.array(red)


def flashes(sig: np.ndarray, darker_limit: float = 0.8, step: float = 0.1) -> list[int]:
    """Frame indices at which a flash completes: a transition of ≥ `step` opposite to the previous one."""
    out, ref, direction = [], sig[0], 0
    for k in range(1, len(sig)):
        d = sig[k] - ref
        if abs(d) >= step and min(sig[k], ref) < darker_limit:
            new = 1 if d > 0 else -1
            if direction and new != direction:
                out.append(k)
            direction, ref = new, sig[k]
        elif (d > 0) == (direction > 0):
            ref = sig[k] if direction == 0 else (max(ref, sig[k]) if direction > 0 else min(ref, sig[k]))
    return out


def check(video: Path) -> dict:
    fps = _fps(video)
    lum, red = series(video)
    worst, failures = 0, []
    for kind, data in (("luminance", lum), ("red", red)):
        for c in range(data.shape[1]):
            fl = flashes(data[:, c])
            j = 0
            for i, k in enumerate(fl):
                while fl[j] < k - fps:
                    j += 1
                n = i - j + 1
                worst = max(worst, n)
                if n > 3:
                    failures.append({"kind": kind, "cell": c, "second": round(k / fps, 2), "flashes": n})
                    break
    return {"video": str(video), "frames": len(lum), "fps": fps, "worst_flashes_per_second": worst,
            "passed": not failures, "failures": failures[:20]}


if __name__ == "__main__":
    res = check(Path(sys.argv[1]))
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["passed"] else 1)
