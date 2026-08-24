"""Holdout evaluation: pairwise ROC, TPR@FAR, and threshold calibration."""

import numpy as np
import structlog
import torch

logger = structlog.get_logger(__name__)


@torch.no_grad()
def compute_embeddings(
    model: torch.nn.Module,
    loader,
    device: str,
    max_batches: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Run the encoder over a dataloader; returns (embeddings [N,D], labels [N])."""
    model.eval()
    all_embeddings: list[np.ndarray] = []
    all_labels: list[np.ndarray] = []

    for index, (images, labels) in enumerate(loader):
        if max_batches is not None and index >= max_batches:
            break
        images = images.to(device, non_blocking=True)
        embeddings = model(images)
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
        all_embeddings.append(embeddings.cpu().numpy())
        all_labels.append(labels.numpy())

    if not all_embeddings:
        return np.empty((0, 0), dtype=np.float32), np.empty((0,), dtype=np.int64)

    return (
        np.concatenate(all_embeddings).astype(np.float32),
        np.concatenate(all_labels),
    )


def pairwise_scores(
    embeddings: np.ndarray, labels: np.ndarray, max_pairs: int = 200_000
) -> tuple[np.ndarray, np.ndarray]:
    """Cosine similarities and match/no-match ground truth for cross-identity pairs."""
    n = embeddings.shape[0]
    sim = embeddings @ embeddings.T
    same = labels[:, None] == labels[None, :]

    iu = np.triu_indices(n, k=1)
    sims = sim[iu]
    y_true = same[iu].astype(np.int8)

    if len(sims) > max_pairs:
        rng = np.random.default_rng(0)
        chosen = rng.choice(len(sims), size=max_pairs, replace=False)
        sims = sims[chosen]
        y_true = y_true[chosen]

    return sims.astype(np.float32), y_true


def evaluate(
    model: torch.nn.Module,
    loader,
    device: str,
    target_far: float = 1e-3,
    max_pairs: int = 200_000,
) -> dict:
    """Compute TPR at fixed FAR plus the similarity threshold that achieves it."""
    embeddings, labels = compute_embeddings(model, loader, device)
    if embeddings.shape[0] < 4:
        return {"valid": False, "reason": "insufficient_holdout_samples"}

    scores, y_true = pairwise_scores(embeddings, labels, max_pairs=max_pairs)
    positives = scores[y_true == 1]
    negatives = scores[y_true == 0]

    if positives.size == 0 or negatives.size == 0:
        return {"valid": False, "reason": "single_class_holdout"}

    # Threshold = lowest score keeping FAR <= target_far.
    sorted_neg = np.sort(negatives)
    far_position = int(np.floor(target_far * len(sorted_neg)))
    threshold = float(sorted_neg[-far_position - 1]) if far_position > 0 else -1.0

    tpr_at_target = float((positives >= threshold).mean())
    far_actual = float((negatives >= threshold).mean())

    # Accuracy-optimal operating point (balanced classes assumption noted in docs).
    best_acc, best_threshold_acc = 0.0, 0.0
    for candidate in np.quantile(scores, np.linspace(0.05, 0.95, 37)):
        acc = float(((positives >= candidate).mean() + (negatives < candidate).mean()) / 2)
        if acc > best_acc:
            best_acc, best_threshold_acc = acc, float(candidate)

    return {
        "valid": True,
        "num_pairs": int(len(y_true)),
        "positive_pairs": int(y_true.sum()),
        f"tpr_at_far_{target_far:g}": round(tpr_at_target, 4),
        "threshold_at_target_far": round(threshold, 4),
        "achieved_far": round(far_actual, 6),
        "best_accuracy": round(best_acc, 4),
        "accuracy_threshold": round(best_threshold_acc, 4),
        "mean_positive_similarity": round(float(positives.mean()), 4),
        "mean_negative_similarity": round(float(negatives.mean()), 4),
    }
