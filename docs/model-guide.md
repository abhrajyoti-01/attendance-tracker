# Model Guide

## Architecture

### Embedding Network

The face embedding model uses EfficientNet-B0 as the backbone, followed by a neck and embedding head that outputs 128-dimensional L2-normalized embeddings:

```
Input: (3, 160, 160) RGB, normalized mean=0.5, std=0.5

EfficientNet-B0 (pretrained on ImageNet)
  Stem   → MBConv1, k3x3        (32, 80, 80)
  Block1 → MBConv6, k3x3  ×1    (16, 80, 80)
  Block2 → MBConv6, k3x3  ×2    (24, 40, 40)
  Block3 → MBConv6, k5x5  ×2    (40, 20, 20)
  Block4 → MBConv6, k3x3  ×3    (80, 10, 10)
  Block5 → MBConv6, k5x5  ×3    (112, 10, 10)
  Block6 → MBConv6, k5x5  ×4    (192, 5, 5)
  Block7 → MBConv6, k3x3  ×1    (320, 5, 5)
  Output: (1280, 5, 5)

Neck:
  Conv2d(1280→512, k=1) → BatchNorm → PReLU
  AdaptiveAvgPool2d(1) → Flatten → (512)

Embedding Head:
  Linear(512→256) → BatchNorm → PReLU → Dropout(0.1)
  Linear(256→128) → BatchNorm

L2 Normalize (‖x‖₂ = 1) → 128-d unit hypersphere embedding
```

**Backbone details:**
- EfficientNet-B0: 5.3M params, ~390M FLOPs
- Fallback: MobileNetV3-Small (2.5M params) for low-end devices

### Loss Functions

The model is trained with a combination of complementary losses. Implemented in `src/model/losses.py`:

| Loss | Type | Purpose |
|------|------|---------|
| **ArcFace** | Additive angular margin | Tight intra-class clustering, essential at scale |
| **CosFace** | Additive cosine margin | Alternative angular margin approach |
| **TripletLoss** | Distance-based | Explicit inter-class separation |
| **BatchHardTripletLoss** | Online hard mining | Most informative triplets per batch |
| **CombinedLoss** | Ensemble (ArcFace + Triplet) | Best of both worlds, configurable weights |
| **CircleLoss** | Unified similarity optimization | Handles both intra and inter-class similarity |
| **CenterLoss** | Center-based | Reduces intra-class variance |
| **AdaptiveTripletLoss** | Dynamic margin | Adjusts margin based on training progress |
| **OnlineTripletLoss** | Semi-hard mining | Online triplet selection with semi-hard negatives |

**Recommended:** `CombinedLoss` with ArcFace (weight 0.7) + BatchHardTripletLoss (weight 0.3).

---

## Training Pipeline

### Two-Phase Strategy

| Phase | Dataset | Epochs | Purpose |
|-------|---------|--------|---------|
| **Pretraining** | VGGFace2 / CASIA-WebFace / MS1M | ~50 | Learn general face representations |
| **Fine-tuning** | Organization-specific data | 20-30 | Adapt to specific demographic distributions |

Skip Phase 1 if using publicly available pretrained ArcFace weights.

### Training Configuration

| Parameter | Value |
|-----------|-------|
| Backbone | EfficientNet-B0 (ImageNet pretrained) |
| Optimizer | AdamW (lr=5e-5 backbone, lr=5e-4 head) |
| Scheduler | CosineAnnealingWarmRestarts (T_0=10, T_mult=2) |
| Batch size | 64 (P=16 identities, K=4 images per identity) |
| ArcFace margin | 0.5 |
| ArcFace scale | 64.0 |
| Embedding dim | 128 |
| Mixed precision | AMP (torch.cuda.amp) when GPU available |
| Checkpointing | Save top-3 by combined metric |

### Data Augmentation

Applied via Albumentations (`src/preprocessing/augment.py`):
- Random brightness/contrast
- Hue/saturation/value shifts
- Gaussian and ISO noise
- Motion blur
- Rotation (±15°)
- Random scaling (±10%)
- Horizontal flip
- Coarse dropout
- MixUp and CutMix support

---

## ONNX Export

Export the trained PyTorch model to ONNX for production inference:

```python
from src.model.embedding_net import export_to_onnx

export_to_onnx(model, "models/exported/embedding_net.onnx")
```

The exported model:
- Accepts `(B, 3, 160, 160)` float32 input normalized to [0, 1]
- Outputs `(B, 128)` L2-normalized embeddings
- Supports dynamic batch size
- opset_version = 14

ONNX Runtime provides 2-3x faster CPU inference compared to PyTorch eager mode.

---

## Threshold Calibration

Calibrate the match threshold per organization using validation data:

```python
def calibrate_threshold(embeddings, labels, target_frr=0.001):
    """
    Find cosine threshold with FRR ≤ target_frr.
    Prioritize low false reject rate for attendance use case.
    """
    # Compute positive (same-identity) and negative (different-identity) scores
    # Pick threshold at the target_frr percentile of positive score distribution
    # Report corresponding FAR
```

**Recommended starting threshold:** 0.50. Typical production range: 0.45-0.60. Tune per organization for optimal balance.

---

## Face Detection

Uses MTCNN via `facenet-pytorch` (`src/preprocessing/face_detector.py`):
- Detects faces in images
- Returns bounding boxes and confidence scores
- Aligns using eye landmarks
- Crops to 160x160 for embedding network input
- Supports single-image and batch directory processing

---

## Quality Checking

Before computing embeddings, faces pass quality checks (`src/preprocessing/quality_checker.py`):

| Check | Metric | Threshold |
|-------|--------|-----------|
| Face size | Minimum face dimension | 40 px |
| Blur | Laplacian variance | ≥ 100.0 |
| Brightness | Mean pixel value | 50-200 |
| Contrast | Standard deviation | ≥ 30 |

Returns per-criterion pass/fail and overall score (0.0-1.0). Includes CLAHE auto-enhancement.
