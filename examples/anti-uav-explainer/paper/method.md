# Vision-Based Anti-UAV Detection and Tracking: the method, in execution order

Zhao, Zhang, Li, Wang. IEEE T-ITS 23(12), 2022. arXiv 2205.10851.

The paper's own method is the fusion of a tracker with a detector (Sec. IV-C, Fig. 7, Algorithm 1). The tracker and
the detector are existing methods; the paper retrains 14 detectors on its dataset and evaluates 8 trackers.

1. **Initialise the tracker** on the first frame with the ground-truth box GT0. (Sec. IV-C)
2. **Track.** On each later frame t, the tracker returns a box bbox_t and a confidence score_t. (Algorithm 1)
3. **Gate.** If score_t is at least τt, bbox_t is the result for the frame. If score_t < τt, the result is treated as
   unreliable and the detector is called. τt = 0.9. (Sec. IV-C)
4. **Detect.** The detector returns boxes bboxes_d with scores scores_d on frame t. (Algorithm 1)
5. **Choose.** If the highest detector score is above τd and above score_t, its box is the result; otherwise bbox_t is.
   τd = 0.9. (Sec. IV-C)

Changing τt and τd moves Success by less than 1% (Fig. 8). With the fusion, every tracker improves; SiamFC goes from
Success 0.381 to 0.616 with Faster-RCNN ResNet50 (Table III).

**Left open by the paper**, and what our proof of concept assumes (stated on screen):

- How score_t is computed. We use the sigmoid of the peak of SiamFC's response map.
- Whether the tracker continues from the chosen box. We move the tracker's state to it.

**Our substitutes**, since no weights or code were released: torchvision Faster R-CNN ResNet-50 FPN with COCO weights
(same architecture as one of the paper's detectors, not retrained), public SiamFC weights, and Algorithm 1
re-implemented from the text.
