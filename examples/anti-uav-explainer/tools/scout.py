"""Scout: run the tracker alone and the paper's fusion (Algorithm 1) on every tracking sequence, log every frame.

    scout.py <images dir> <gt dir> <out dir> [video01 video02 ...]

For each sequence: frames.json (one record per frame) and a summary line in summary.json. Accuracy figures here are
OUR run (COCO detector, public SiamFC weights), used only to choose a sequence; the film quotes the paper's tables.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import FasterRCNN, SiamFC  # noqa: E402

TAU_T = TAU_D = 0.9  # "In this paper, hyper-parameters τt and τd are set to 0.9." (Sec. IV-C)
WEIGHTS = Path(__file__).resolve().parent / "pretrained" / "siamfc_alexnet_e50.pth"


def iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def frames_of(images: Path, seq: str) -> list[Path]:
    cands = [d for d in images.rglob(seq) if d.is_dir()]
    if not cands:
        raise SystemExit(f"no folder named {seq} under {images}")
    files = sorted(p for p in cands[0].iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    return files


def load(p: Path) -> np.ndarray:
    return cv2.cvtColor(cv2.imread(str(p), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def success_auc(ious: list[float]) -> float:
    th = np.linspace(0, 1, 21)
    a = np.array(ious)
    return float(np.mean([(a > t).mean() for t in th]))


def run(seq: str, files: list[Path], gt: np.ndarray, trk: SiamFC, det: FasterRCNN) -> tuple[list, dict]:
    first = load(files[0])
    records = []
    # A: the tracker alone
    trk.init(first, gt[0])
    alone = [list(map(float, gt[0]))]
    for f in files[1:]:
        box, _, _ = trk.update(load(f))
        alone.append(box.tolist())
    # B: Algorithm 1
    trk.init(first, gt[0])
    fused = [list(map(float, gt[0]))]
    records.append({"frame": 0, "gt": gt[0].tolist(), "source": "init"})
    det_calls = 0
    for i, f in enumerate(files[1:], start=1):
        img = load(f)
        bbox_t, score_t, _ = trk.update(img)
        rec = {"frame": i, "gt": gt[i].tolist(), "bbox_t": bbox_t.tolist(), "score_t": round(score_t, 4),
               "alone": alone[i], "source": "tracker"}
        result = bbox_t.tolist()
        if score_t < TAU_T:
            dets, _ = det.detect(img)
            det_calls += 1
            rec["dets"] = [[d[0], round(d[1], 4), d[2]] for d in dets[:5]]
            if dets and dets[0][1] > TAU_D and dets[0][1] > score_t:
                result = dets[0][0]
                rec["source"] = "detector"
                trk.move_to(result)
        rec["result"] = result
        rec["iou_fused"] = round(iou(result, gt[i]), 4)
        rec["iou_alone"] = round(iou(alone[i], gt[i]), 4)
        fused.append(result)
        records.append(rec)
    body = records[1:]
    summary = {"seq": seq, "frames": len(files),
               "success_alone": round(success_auc([r["iou_alone"] for r in body]), 3),
               "success_fused": round(success_auc([r["iou_fused"] for r in body]), 3),
               "gate_fired": sum(1 for r in body if r["score_t"] < TAU_T),
               "detector_won": sum(1 for r in body if r["source"] == "detector"),
               "detector_calls": det_calls,
               "frame_size": list(first.shape[:2]),
               "gt_area_ratio_first": round(float(gt[0][2] * gt[0][3]) / (first.shape[0] * first.shape[1]), 5)}
    return records, summary


def main() -> None:
    images, gt_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    seqs = sys.argv[4:] or sorted(p.name.split("_gt")[0] for p in gt_dir.rglob("*_gt.txt"))
    out.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    trk, det = SiamFC(WEIGHTS, device), FasterRCNN(device)
    summaries = []
    for seq in seqs:
        t0 = time.time()
        gt = np.loadtxt(next(gt_dir.rglob(f"{seq}_gt.txt")), dtype=np.float32).reshape(-1, 4)
        files = frames_of(images, seq)
        n = min(len(files), len(gt))
        records, summary = run(seq, files[:n], gt[:n], trk, det)
        summary["seconds"] = round(time.time() - t0, 1)
        (out / f"{seq}.frames.json").write_text(json.dumps(records, default=float))
        summaries.append(summary)
        print(json.dumps(summary, default=float), flush=True)
    (out / "summary.json").write_text(json.dumps(summaries, indent=1, default=float))


if __name__ == "__main__":
    main()
