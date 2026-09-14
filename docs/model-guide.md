# Model Guide

## Architecture

### Embedding Network

The shipped model is **FaceNet InceptionResnetV1 pretrained on VGGFace2**,
exported to ONNX and served by ONNX Runtime. It outputs **512-dimensional
L2-normalized embeddings**:

```
Input: (N, 3, 160, 160) float32 RGB
       standardized once: x/255 then (x - 0.5) / 0.5, i.e. range [-1, 1]

InceptionResnetV1 (VGGFace2 weights, classify=False)
  → 512-d output

L2 normalize (‖x‖₂ = 1) → 512-d unit hypersphere embedding
```

Standardization is **not** baked into the exported graph; `EmbeddingEngine`
applies it exactly once. `scripts/export_pretrained_onnx.py --validate` asserts
the output shape is `(N, 512)` and that every row is unit length.

| Property | Value |
|----------|-------|
| Input | 160×160 RGB, float32, range [-1, 1] |
| Output | 512-d, L2-normalized |
| Runtime | ONNX Runtime (CPU by default; CUDA when `DEVICE=cuda`) |
| Default match threshold | 0.55 cosine (`MATCH_THRESHOLD_DEFAULT`) |
| Threshold floor | 0.30 (`MATCH_THRESHOLD_FLOOR`, enforced server-side) |

`src/model/embedding_net.py` additionally offers EfficientNet/MobileNet
backbones and a training-time export helper. It is **not** used by the serving
path or by the fine-tuning pipeline — both use `facenet_pytorch` — and is kept
as a reference implementation.

### Loss Functions

The fine-tuning pipeline (`src/training/`) trains with a batch-hard triplet
loss (`src/training/trainer.py`). `src/model/losses.py` contains additional
margin-loss implementations used by the unused backbone experiments above.

| Loss | Type | Purpose |
|------|------|---------|
| **BatchHardTriplet** | Triplet margin | Used by the shipped trainer |
| **ArcFace** | Additive angular margin | Reference implementation in `src/model/losses.py` |
| **CosFace** | Additive cosine margin | Reference implementation in `src/model/losses.py` |
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
| Backbone | InceptionResnetV1 (VGGFace2 pretrained) |
| Optimizer | AdamW |
| Scheduler | CosineAnnealingLR |
| Sampling | P identities × K samples per batch (batch-hard triplet mining) |
| Embedding dim | 512 |
| Mixed precision | AMP when CUDA is available |
| Checkpointing | Best model by TPR@FAR=1e-3, exported to ONNX |

See `src/training/trainer.py` for the authoritative implementation.

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

> Note: this module exists but the shipped trainer
> (`src/training/dataset.py`) does not currently apply it; augmentation is a
> documented hook rather than an active part of the pipeline.

---

## ONNX Export

The production model is exported with:

```bash
python scripts/export_pretrained_onnx.py --validate
```

The script is the authoritative exporter. The exported model:
- Accepts `(B, 3, 160, 160)` float32 input with `(x/255 - 0.5) / 0.5` applied once
- Outputs `(B, 512)` L2-normalized embeddings
- Supports dynamic batch size
- opset_version = 14

`--validate` asserts the output shape is `(N, 512)` and that every row is unit length.

`src/model/embedding_net.export_to_onnx` is an alternative exporter for the
unused EfficientNet/MobileNet backbones and is not part of the serving path.

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
