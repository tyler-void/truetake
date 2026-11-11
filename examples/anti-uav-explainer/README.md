# The anti-UAV explainer

A 2 minute 45 second film that explains *Vision-Based Anti-UAV Detection and Tracking* (Zhao, Zhang, Li, Wang,
IEEE T-ITS 2022, arXiv 2205.10851) module by module, on real data. It follows one frame of a real video through the
method: the tracker loses the drone on frame 42; the paper's rule (Algorithm 1) asks the detector; the frame is
resized, shrinks through ResNet-50, grows back through the feature pyramid, gets proposals, and the drone's box is
drawn on it. Every picture is an intermediate of the models on that frame; every number is read from the run or
from the paper, and says which.

Nothing is trained. The paper released its data but not its weights or its fusion code, so the example uses:

| The paper's | Here | Said on screen |
|---|---|---|
| Faster R-CNN ResNet-50, retrained on DUT Anti-UAV | torchvision's, COCO weights; COCO has no drone class, so airplane, bird and kite count | yes |
| SiamFC | SiamFC with public weights (huanglianghua/siamfc-pytorch, MIT) | yes |
| Algorithm 1 (τt = τd = 0.9) | re-implemented from the text | yes |
| the tracker's score, left undefined | APCE (peak sharpness) divided by its mean over the first 5 frames, capped at 1 | yes |

The sigmoid of SiamFC's peak was tried first as the score: it stays above 0.9 even when the tracker has lost the
drone, so the paper's rule could never fire. APCE falls when the response flattens.

## Make it

```bash
examples/anti-uav-explainer/setup.sh ./anti-uav-demo   # scripts, 3.7 GB of data, SiamFC weights, fonts
cd anti-uav-demo
tools/.venv/bin/python tools/scout.py data/images data/gt scout video05
tools/.venv/bin/python tools/extract.py data/images data/gt film video05 41 41
python film/film.py --cues
truetake-score render film/cues.json -o film/score.wav --style arranged
python film/film.py --score film/score.wav
python -m truetake.flash film/anti-uav-explainer.mp4
```

`film/film.py` needs truetake with the `film` extra; `truetake-score` is
[its sister repository](https://github.com/tyler-void/truetake-score). The scout runs the tracker alone and the
tracker with Algorithm 1 on a sequence and logs every frame; pick the handover frame (`"source": "detector"`) from
its log. `paper/paper.json` and `paper/method.md` are the paper read with [the paper-reading skill](../../skills/paper-reading/SKILL.md).

## Our run on all 20 sequences

Success (area under the overlap curve) of OUR run, COCO detector and public SiamFC weights. The film quotes the
paper's Table III instead; this table only shows that the rule helps here too.

| Sequence | Frames | Tracker alone | With Algorithm 1 | Gate fired | Detector won |
|---|---|---|---|---|---|
| video01 | 1050 | 0.137 | 0.701 | 828 | 640 |
| video02 | 83 | 0.612 | 0.735 | 56 | 21 |
| video03 | 100 | 0.654 | 0.746 | 73 | 17 |
| video04 | 341 | 0.502 | 0.442 | 296 | 231 |
| video05 | 450 | 0.011 | 0.505 | 38 | 1 |
| video06 | 200 | 0.779 | 0.781 | 110 | 96 |
| video07 | 2480 | 0.135 | 0.457 | 1490 | 89 |
| video08 | 2305 | 0.342 | 0.690 | 1772 | 689 |
| video09 | 2500 | 0.432 | 0.531 | 1442 | 487 |
| video10 | 2635 | 0.394 | 0.491 | 2548 | 400 |
| video11 | 1000 | 0.598 | 0.628 | 793 | 42 |
| video12 | 1485 | 0.299 | 0.359 | 1374 | 168 |
| video13 | 1915 | 0.115 | 0.115 | 1905 | 0 |
| video14 | 590 | 0.311 | 0.311 | 534 | 0 |
| video15 | 1350 | 0.054 | 0.332 | 761 | 50 |
| video16 | 1285 | 0.076 | 0.168 | 1163 | 104 |
| video17 | 780 | 0.033 | 0.032 | 774 | 7 |
| video18 | 1320 | 0.294 | 0.609 | 296 | 17 |
| video19 | 1300 | 0.500 | 0.644 | 446 | 61 |
| video20 | 1635 | 0.165 | 0.472 | 1387 | 121 |

Mean 0.322 → 0.487; better on 16 sequences, worse on 2 (where the COCO detector picks the wrong object). The paper
reports SiamFC 0.381 → 0.616 with its retrained Faster R-CNN ResNet-50 (Table III).

## Notes

- The dataset is for academic research. Keep the project folder (data, frames, renders) out of public repositories.
- The paper's PDF is the publisher's copy; do not commit it.
- The film passes `truetake.flash` (at most 3 flashes in any second); the first version did not, and stations now
  dim when the camera leaves them, so pans never sweep bright pictures across the screen.
