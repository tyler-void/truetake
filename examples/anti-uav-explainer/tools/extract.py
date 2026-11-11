"""Extract every intermediate the film shows, from one sequence and two chosen frames.

    extract.py <images dir> <gt dir> <out dir> <seq> <detect frame> <handover frame>

<detect frame>: the frame the detector scenes follow (the drone visible, the detector firing).
<handover frame>: a frame where the tracker's score falls below τt and the detector's box wins (from the scout).

Writes arrays as .npy and pictures as .png into <out>/assets, and manifest.json describing each: what it is, its
shape, its source (model, weights, frame), and the paper section it illustrates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import FasterRCNN, SiamFC  # noqa: E402
from scout import TAU_D, TAU_T, WEIGHTS, frames_of, iou, load  # noqa: E402


def main() -> None:
    images, gt_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    seq, f_det, f_hand = sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
    a = out / "assets"
    a.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    gt = np.loadtxt(next(gt_dir.rglob(f"{seq}_gt.txt")), dtype=np.float32).reshape(-1, 4)
    files = frames_of(images, seq)
    manifest: dict = {"sequence": seq, "frames": len(files), "detect_frame": f_det, "handover_frame": f_hand,
                      "tau_t": TAU_T, "tau_d": TAU_D, "assets": {}}

    def save(name: str, arr: np.ndarray, what: str, source: str, paper: str = "") -> None:
        arr = np.asarray(arr)
        np.save(a / f"{name}.npy", arr)
        manifest["assets"][name] = {"what": what, "shape": list(arr.shape), "dtype": str(arr.dtype),
                                    "min": float(arr.min()), "max": float(arr.max()), "source": source, "paper": paper}

    def save_png(name: str, rgb: np.ndarray, what: str, source: str) -> None:
        cv2.imwrite(str(a / f"{name}.png"), cv2.cvtColor(np.asarray(rgb, np.uint8), cv2.COLOR_RGB2BGR))
        manifest["assets"][name] = {"what": what, "shape": list(np.asarray(rgb).shape), "source": source}

    # ---------- the detector on the chosen frame ----------
    det = FasterRCNN(device)
    img = load(files[f_det])
    save_png("det_frame", img, "the frame the detector scenes follow", f"{seq} frame {f_det}")
    manifest["det_frame_gt"] = gt[f_det].tolist()
    dets, rec = det.detect(img, record=True)
    src = "torchvision fasterrcnn_resnet50_fpn, COCO_V1 weights"
    manifest["detector"] = {"weights": str(det.weights), "input_size": rec["input_size"],
                            "batched_shape": rec["batched_shape"], "uav_like_classes": "airplane, bird, kite",
                            "detections": [[d[0], d[1], d[2]] for d in dets[:10]]}
    r = rec["rec"]
    for i, name in enumerate(("layer1", "layer2", "layer3", "layer4"), start=1):
        t = r[f"backbone.{name}"][0]
        save(f"c{i + 1}_mean", t.mean(0).cpu().numpy(), f"ResNet-50 {name} output, mean over its {t.shape[0]} channels",
             src, "the detector's backbone (Sec. IV-A)")
        manifest["assets"][f"c{i + 1}_mean"]["channels"] = int(t.shape[0])
    for key, t in r["fpn"].items():
        save(f"p_{key}_mean", t[0].mean(0).cpu().numpy(), f"FPN level {key}, mean over {t.shape[1]} channels", src)
    obj, deltas = r["rpn.head"]
    for i, o in enumerate(obj):
        save(f"rpn_obj_{i}", torch.sigmoid(o[0]).max(0).values.cpu().numpy(),
             f"RPN objectness on pyramid level {i}, max over its anchors (sigmoid)", src)
    proposals = r["rpn"][0][0].cpu().numpy()  # (N, 4) x0 y0 x1 y1 in the resized image
    save("proposals", proposals, "RPN proposals in the resized input, best first", src)
    # the proposal that covers the drone best: ground truth mapped into the resized input, highest IoU
    sy, sx = rec["input_size"][0] / img.shape[0], rec["input_size"][1] / img.shape[1]
    gx, gy, gw, gh = gt[f_det]
    g = [(gx - 1) * sx, (gy - 1) * sy, gw * sx, gh * sy]
    ious = [iou([p[0], p[1], p[2] - p[0], p[3] - p[1]], g) for p in proposals]
    best = int(np.argmax(ious))
    manifest["detector"]["roi_proposal"] = {"index": best, "iou_with_gt": float(ious[best]),
                                            "box_resized": proposals[best].tolist()}
    save("roi_feature", r["roi_pool"][best].mean(0).cpu().numpy(),
         "7x7 RoI-aligned features of the proposal covering the drone, mean over 256 channels", src)
    cls_logits, box_deltas = r["box_predictor"]
    save("roi_cls_probs", torch.softmax(cls_logits[best], -1).cpu().numpy(),
         "that proposal's class probabilities (91 COCO classes)", src)
    # the padded input the backbone sees, de-normalised back to a picture
    t_in = det.model.transform([torch.from_numpy(img).permute(2, 0, 1).float().div(255).to(device)])[0].tensors[0]
    mean = torch.tensor(det.model.transform.image_mean, device=device)[:, None, None]
    std = torch.tensor(det.model.transform.image_std, device=device)[:, None, None]
    pic = ((t_in * std + mean).clamp(0, 1) * 255).byte().permute(1, 2, 0).cpu().numpy()
    save_png("det_input", pic, "the resized, padded input the backbone sees (de-normalised)", src)
    save("all_boxes", rec["all_boxes"], "post-NMS boxes in original frame pixels", src)
    save("all_scores", rec["all_scores"], "post-NMS scores", src)
    save("all_labels", rec["all_labels"], "post-NMS COCO labels", src)
    h, w = img.shape[:2]
    rh, rw = rec["input_size"]
    manifest["detector"]["scale"] = [rh / h, rw / w]

    # ---------- the tracker: init, one ordinary frame, and the handover ----------
    trk = SiamFC(WEIGHTS, device)
    first = load(files[0])
    save_png("frame0", first, "the first frame", f"{seq} frame 0")
    init = trk.init(first, gt[0])
    save_png("exemplar", init["exemplar"], "SiamFC exemplar crop (127x127) around the ground-truth box of frame 0", "SiamFC")
    save("kernel_mean", init["kernel_mean"], f"exemplar features {init['kernel_shape']}, channel mean", "SiamFC")
    manifest["tracker"] = {"weights": "siamfc_alexnet_e50.pth (huanglianghua/siamfc-pytorch, MIT)",
                           "gt0": gt[0].tolist(), "exemplar_side_in_frame": init["exemplar_side_in_frame"],
                           "kernel_shape": init["kernel_shape"], "score": "sigmoid of the response peak (our assumption)"}
    log = []
    for i in range(1, max(f_hand, 2) + 1):
        frame = load(files[i])
        want = i in (1, f_hand)
        bbox_t, score_t, rec_t = trk.update(frame, record=want)
        entry = {"frame": i, "bbox_t": bbox_t.tolist(), "score_t": score_t, "gt": gt[i].tolist(), "source": "tracker"}
        result = bbox_t.tolist()
        if score_t < TAU_T:
            dets_i, _ = det.detect(frame)
            entry["dets"] = [[d[0], d[1], d[2]] for d in dets_i[:5]]
            if dets_i and dets_i[0][1] > TAU_D and dets_i[0][1] > score_t:
                result = dets_i[0][0]
                entry["source"] = "detector"
                if i != f_hand:
                    trk.move_to(result)
        entry["result"] = result
        entry["iou"] = iou(result, gt[i])
        log.append(entry)
        if want:
            tag = "next" if i == 1 else "hand"
            save_png(f"{tag}_frame", frame, f"frame {i}", f"{seq} frame {i}")
            save_png(f"{tag}_search", rec_t["search"], "SiamFC search region (255x255)", "SiamFC")
            save(f"{tag}_features", rec_t["features_mean"], f"search features {rec_t['features_shape']}, channel mean", "SiamFC")
            save(f"{tag}_resp_raw", rec_t["response_raw"], "cross-correlation response 17x17 (logits)", "SiamFC")
            save(f"{tag}_resp_up", rec_t["response_up"], "response upsampled to 272x272", "SiamFC")
            save(f"{tag}_resp_final", rec_t["response_final"], "normalised response with the cosine window", "SiamFC")
            manifest["tracker"][tag] = {k: rec_t[k] for k in ("search_center", "search_side_in_frame", "peak",
                                                              "peak_logit", "scale_id")}
            manifest["tracker"][tag].update(entry)
        if i == f_hand and entry["source"] == "detector":
            trk.move_to(result)
    (out / "fusion_log.json").write_text(json.dumps(log, default=float))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=float))
    print(f"wrote {len(manifest['assets'])} assets; handover frame source: {log[f_hand - 1]['source']}, "
          f"score_t {log[f_hand - 1]['score_t']:.3f}")


if __name__ == "__main__":
    main()
