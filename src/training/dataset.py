"""Face recognition training dataset and batch sampler.

Expected layout (one directory per identity):

    data_root/
        person_a/
            img001.jpg
            img002.jpg
        person_b/
            ...

Images are detected/cropped to 160x160 RGB by MTCNN at load time. Crops are
cached under ``cache_dir`` keyed by file path so subsequent epochs skip
detection entirely.
"""

import hashlib
from collections import defaultdict
from collections.abc import Iterator, Sequence
from pathlib import Path

import cv2
import numpy as np
import structlog
import torch
from torch.utils.data import DataLoader, Dataset, Sampler

from src.config import settings

logger = structlog.get_logger(__name__)

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_IMAGES_PER_IDENTITY = 4


class IdentityDataset(Dataset):
    """Maps identity directories to integer labels and yields face crops."""

    def __init__(
        self,
        samples: Sequence[tuple[Path, int]],
        image_size: int | None = None,
        cache_dir: Path | None = None,
    ):
        self.samples = list(samples)
        self.image_size = image_size or settings.model.face_image_size
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def num_identities(self) -> int:
        return len({label for _, label in self.samples})

    def _crop_cache_path(self, source: Path) -> Path:
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.npy"  # type: ignore[union-attr]

    def _detect_crop(self, image_path: Path) -> np.ndarray:
        """Detect one face and return an RGB uint8 crop of image_size."""
        if self.cache_dir is not None:
            cached = self._crop_cache_path(image_path)
            if cached.exists():
                return np.load(cached)

        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError(f"Unreadable image: {image_path}")

        from src.preprocessing.face_detector import FaceDetector

        detector = FaceDetector(
            image_size=self.image_size,
            margin=20,
            device="cuda" if settings.model.device == "cuda" else "cpu",
        )
        face = detector.detect(image)
        if face is None:
            raise ValueError(f"No face found in {image_path}")

        # detect() returns float [0..1] HWC RGB
        crop_uint8 = np.clip(face * 255.0, 0, 255).astype(np.uint8)

        if self.cache_dir is not None:
            np.save(self._crop_cache_path(image_path), crop_uint8)
        return crop_uint8

    def __getitem__(self, index: int):
        path, label = self.samples[index]
        try:
            crop = self._detect_crop(path)
        except Exception as exc:
            logger.warning("Sample skipped during load", path=str(path), error=str(exc))
            crop = np.zeros((self.image_size, self.image_size, 3), dtype=np.uint8)

        tensor = torch.from_numpy(crop).permute(2, 0, 1).float().div_(255.0)
        tensor = (tensor - 0.5) / 0.5
        return tensor, label


class PKBatchSampler(Sampler[list[int]]):
    """Yields batches with P identities x K images each (required for batch-hard mining)."""

    def __init__(
        self,
        labels: Sequence[int],
        p: int = 8,
        k: int = 4,
        iterations_per_epoch: int | None = None,
        seed: int = 42,
    ):
        self.labels = list(labels)
        self.p = p
        self.k = k
        self.seed = seed

        by_label: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(self.labels):
            by_label[label].append(idx)
        self.by_label = by_label

        # Identities need at least 2 images so batch-hard mining has a positive pair.
        self.usable_labels = sorted(
            label for label, indices in by_label.items() if len(indices) >= 2
        )

        self.iterations = iterations_per_epoch or max(1, len(self.usable_labels) // max(1, p))
        self._rng = np.random.default_rng(seed)

    def __iter__(self) -> Iterator[list[int]]:
        rng = np.random.default_rng(self._rng.integers(0, 2**32 - 1))
        usable = self.usable_labels
        if not usable:
            return iter([])

        for _ in range(self.iterations):
            chosen_p = min(self.p, len(usable))
            chosen_labels = rng.choice(usable, size=chosen_p, replace=False)

            batch: list[int] = []
            for label in chosen_labels:
                pool = self.by_label[label]
                take_k = min(self.k, len(pool))
                picked = rng.choice(pool, size=take_k, replace=False)
                batch.extend(int(i) for i in picked)

            yield batch

    def __len__(self) -> int:
        return self.iterations


def build_train_val_datasets(
    data_root: str | Path,
    val_ratio: float = 0.2,
    seed: int = 42,
    cache_dir: str | Path | None = None,
) -> tuple["IdentityDataset", "IdentityDataset", dict]:
    """Split per-identity image lists into disjoint train/val sample lists."""
    root = Path(data_root)
    if not root.is_dir():
        raise NotADirectoryError(f"Training data root not found: {root}")

    rng = np.random.default_rng(seed)

    identities: dict[str, list[Path]] = {}
    for identity_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        images = sorted(p for p in identity_dir.rglob("*") if p.suffix.lower() in VALID_EXTENSIONS)
        if len(images) >= MIN_IMAGES_PER_IDENTITY:
            identities[identity_dir.name] = images

    if len(identities) < 2:
        raise ValueError(
            f"Need at least 2 identities with >= {MIN_IMAGES_PER_IDENTITY} images; "
            f"found {len(identities)} at {root}"
        )

    train_samples: list[tuple[Path, int]] = []
    val_samples: list[tuple[Path, int]] = []

    for label, (_name, images) in enumerate(sorted(identities.items())):
        images = list(images)
        rng.shuffle(images)
        n_val = max(1, int(len(images) * val_ratio)) if len(images) > 4 else 1
        val_files = images[:n_val]
        train_files = images[n_val:]
        train_samples.extend((p, label) for p in train_files)
        val_samples.extend((p, label) for p in val_files)

    stats = {
        "identities": len(identities),
        "train_images": len(train_samples),
        "val_images": len(val_samples),
    }

    shared_cache = Path(cache_dir) if cache_dir else root / "_cache"
    train_set = IdentityDataset(train_samples, cache_dir=shared_cache / "train")
    val_set = IdentityDataset(val_samples, cache_dir=shared_cache / "val")
    return train_set, val_set, stats


def make_pk_loader(
    dataset: IdentityDataset,
    p: int,
    k: int,
    num_workers: int = 2,
    seed: int = 42,
) -> tuple[DataLoader, PKBatchSampler]:
    labels = [label for _, label in dataset.samples]
    sampler = PKBatchSampler(labels, p=p, k=k, seed=seed)
    loader = DataLoader(
        dataset,
        batch_sampler=sampler,
        num_workers=num_workers,
        pin_memory=torch.cuda.is_available(),
        persistent_workers=num_workers > 0,
    )
    return loader, sampler
