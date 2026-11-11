"""The two models of the proof of concept, instrumented so every intermediate the film shows is recorded.

Tracker: SiamFC (huanglianghua/siamfc-pytorch, MIT, public weights `siamfc_alexnet_e50.pth`). Its backbone, head
and crop code are imported unchanged; `init` and `update` below follow siamfc.py line for line and only add records.
Its score for the fusion gate (the paper leaves score_t undefined) is the response's APCE, average
peak-to-correlation energy (Wang et al., CVPR 2017): (max - min)^2 / mean((F - min)^2), high for one sharp peak and
low for a flat or many-peaked map, divided by its mean over the first REF_FRAMES frames and capped at 1, so 1 means
"as sure as at the start". The sigmoid of the raw peak was tried first: it stays above 0.9 even when the tracker has
lost the drone, so the paper's gate could never fire. It is still recorded as `peak_prob`.

Detector: torchvision Faster R-CNN, ResNet-50 + FPN, COCO weights: the paper's architecture, not its retrained
weights. COCO has no "drone" class; the classes that a small flying object can be detected as (airplane, bird,
kite) are counted as UAV, and the film says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn

HERE = Path(__file__).resolve().parent


def _load(name: str):
    """One file of the SiamFC repo, unchanged, without its package __init__ (which needs got10k for training)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"siamfc_{name}", HERE / "siamfc-pytorch" / "siamfc" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backbones, heads, ops = _load("backbones"), _load("heads"), _load("ops")

COCO_UAV_LIKE = {5: "airplane", 16: "bird", 38: "kite"}  # torchvision COCO category ids


class _Net(nn.Module):  # the same composition as siamfc.Net, without the got10k import
    def __init__(self):
        super().__init__()
        self.backbone = backbones.AlexNetV1()
        self.head = heads.SiamFC(0.001)

    def forward(self, z, x):
        return self.head(self.backbone(z), self.backbone(x))


REF_FRAMES = 5


def apce(f: np.ndarray) -> float:
    lo, hi = float(f.min()), float(f.max())
    return (hi - lo) ** 2 / (float(np.mean((f - lo) ** 2)) + 1e-12)


class SiamFC:
    """siamfc.TrackerSiamFC's inference, recording crops, features and responses."""

    cfg = dict(exemplar_sz=127, instance_sz=255, context=0.5, scale_num=3, scale_step=1.0375, scale_lr=0.59,
               scale_penalty=0.9745, window_influence=0.176, response_sz=17, response_up=16, total_stride=8)

    def __init__(self, weights: Path, device: str):
        self.device = torch.device(device)
        self.net = _Net()
        self.net.load_state_dict(torch.load(weights, map_location="cpu"))
        self.net.to(self.device).eval()

    @torch.no_grad()
    def init(self, img: np.ndarray, box_ltwh) -> dict:
        c = self.cfg
        x, y, w, h = box_ltwh  # the repo's convention: 1-indexed left-top, as the dataset's ground truth
        self.center = np.array([y - 1 + (h - 1) / 2, x - 1 + (w - 1) / 2], dtype=np.float32)
        self.target_sz = np.array([h, w], dtype=np.float32)
        self.upscale_sz = c["response_up"] * c["response_sz"]
        self.hann = np.outer(np.hanning(self.upscale_sz), np.hanning(self.upscale_sz))
        self.hann /= self.hann.sum()
        self.scales = c["scale_step"] ** np.linspace(-(c["scale_num"] // 2), c["scale_num"] // 2, c["scale_num"])
        context = c["context"] * np.sum(self.target_sz)
        self.z_sz = float(np.sqrt(np.prod(self.target_sz + context)))
        self.x_sz = self.z_sz * c["instance_sz"] / c["exemplar_sz"]
        self.avg_color = np.mean(img, axis=(0, 1))
        z = ops.crop_and_resize(img, self.center, self.z_sz, out_size=c["exemplar_sz"], border_value=self.avg_color)
        zt = torch.from_numpy(z).to(self.device).permute(2, 0, 1).unsqueeze(0).float()
        self.kernel = self.net.backbone(zt)
        self._apce_ref: list[float] = []
        return {"exemplar": z, "exemplar_side_in_frame": self.z_sz, "kernel_shape": list(self.kernel.shape),
                "kernel_mean": self.kernel[0].mean(0).cpu().numpy()}

    def move_to(self, box_ltwh) -> None:
        """The fusion's choice becomes the tracker's state (our stated assumption; the paper does not say)."""
        x, y, w, h = box_ltwh
        new_sz = np.array([h, w], dtype=np.float32)
        ratio = float(np.sqrt(np.prod(new_sz) / max(np.prod(self.target_sz), 1e-6)))
        self.center = np.array([y - 1 + (h - 1) / 2, x - 1 + (w - 1) / 2], dtype=np.float32)
        self.target_sz = new_sz
        self.z_sz *= ratio
        self.x_sz *= ratio

    @torch.no_grad()
    def update(self, img: np.ndarray, record: bool = False) -> tuple[np.ndarray, float, dict]:
        c = self.cfg
        center_before, x_sz_before = self.center.copy(), self.x_sz
        crops = [ops.crop_and_resize(img, self.center, self.x_sz * f, out_size=c["instance_sz"],
                                     border_value=self.avg_color) for f in self.scales]
        xt = torch.from_numpy(np.stack(crops)).to(self.device).permute(0, 3, 1, 2).float()
        feats = self.net.backbone(xt)
        raw = self.net.head(self.kernel, feats).squeeze(1).cpu().numpy()  # (3, 17, 17), logits
        ups = np.stack([cv2.resize(u, (self.upscale_sz, self.upscale_sz), interpolation=cv2.INTER_CUBIC)
                        for u in raw])
        ups[: c["scale_num"] // 2] *= c["scale_penalty"]
        ups[c["scale_num"] // 2 + 1:] *= c["scale_penalty"]
        scale_id = int(np.argmax(np.amax(ups, axis=(1, 2))))
        peak_logit = float(np.amax(ups[scale_id]))
        peak_prob = float(1.0 / (1.0 + np.exp(-peak_logit)))
        a = apce(ups[scale_id])
        if len(self._apce_ref) < REF_FRAMES:
            self._apce_ref.append(a)
        ref = float(np.mean(self._apce_ref))
        score = float(min(1.0, a / ref))
        resp = ups[scale_id].copy()
        resp -= resp.min()
        resp /= resp.sum() + 1e-16
        resp = (1 - c["window_influence"]) * resp + c["window_influence"] * self.hann
        loc = np.unravel_index(resp.argmax(), resp.shape)
        disp = (np.array(loc) - (self.upscale_sz - 1) / 2) * c["total_stride"] / c["response_up"]
        self.center += disp * self.x_sz * self.scales[scale_id] / c["instance_sz"]
        s = (1 - c["scale_lr"]) + c["scale_lr"] * self.scales[scale_id]
        self.target_sz *= s
        self.z_sz *= s
        self.x_sz *= s
        box = np.array([self.center[1] + 1 - (self.target_sz[1] - 1) / 2,
                        self.center[0] + 1 - (self.target_sz[0] - 1) / 2, self.target_sz[1], self.target_sz[0]])
        rec = {}
        if record:
            rec = {"search": crops[scale_id], "search_center": center_before.tolist(),
                   "search_side_in_frame": float(x_sz_before * self.scales[scale_id]), "scale_id": scale_id,
                   "features_shape": list(feats.shape), "features_mean": feats[scale_id].mean(0).cpu().numpy(),
                   "response_raw": raw[scale_id], "response_up": ups[scale_id], "response_final": resp,
                   "peak": [int(loc[0]), int(loc[1])], "peak_logit": peak_logit, "peak_prob": peak_prob,
                   "apce": a, "apce_ref": ref}
        return box, score, rec


class FasterRCNN:
    """torchvision's Faster R-CNN with hooks on every stage the film shows."""

    def __init__(self, device: str):
        from torchvision.models.detection import FasterRCNN_ResNet50_FPN_Weights, fasterrcnn_resnet50_fpn

        self.weights = FasterRCNN_ResNet50_FPN_Weights.COCO_V1
        self.model = fasterrcnn_resnet50_fpn(weights=self.weights).to(device).eval()
        self.device = device
        self._rec: dict = {}
        self._recording = False
        m = self.model
        for name in ("layer1", "layer2", "layer3", "layer4"):
            getattr(m.backbone.body, name).register_forward_hook(self._keep(f"backbone.{name}"))
        m.backbone.fpn.register_forward_hook(self._keep("fpn"))
        m.rpn.head.register_forward_hook(self._keep("rpn.head"))
        m.rpn.register_forward_hook(self._keep("rpn"))
        m.roi_heads.box_roi_pool.register_forward_hook(self._keep("roi_pool"))
        m.roi_heads.box_predictor.register_forward_hook(self._keep("box_predictor"))

    def _keep(self, name):
        def hook(_m, _i, out):
            if self._recording:
                self._rec[name] = out
        return hook

    @torch.no_grad()
    def detect(self, img: np.ndarray, record: bool = False) -> tuple[list, dict]:
        """`img` is RGB uint8. Returns UAV-like detections [(ltwh box, score, label)], best first."""
        self._recording, self._rec = record, {}
        t = torch.from_numpy(img).permute(2, 0, 1).float().div(255).to(self.device)
        images, _ = self.model.transform([t])  # the resize and normalise, recorded below
        out = self.model([t])[0]
        dets = []
        for b, s, l in zip(out["boxes"].cpu().numpy(), out["scores"].cpu().numpy(), out["labels"].cpu().numpy()):
            if int(l) in COCO_UAV_LIKE:
                x0, y0, x1, y1 = b
                dets.append(([float(x0) + 1, float(y0) + 1, float(x1 - x0), float(y1 - y0)], float(s),
                             COCO_UAV_LIKE[int(l)]))
        dets.sort(key=lambda d: -d[1])
        rec = {}
        if record:
            rec = {"input_size": list(images.image_sizes[0]), "batched_shape": list(images.tensors.shape),
                   "rec": self._rec, "all_boxes": out["boxes"].cpu().numpy(), "all_scores": out["scores"].cpu().numpy(),
                   "all_labels": out["labels"].cpu().numpy()}
        self._recording = False
        return dets, rec
