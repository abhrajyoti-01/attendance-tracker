from collections.abc import Callable

import albumentations as A
import cv2
import numpy as np
import torch


def get_training_augmentation(
    image_size: int = 160,
    p: float = 0.8,
) -> A.Compose:
    return A.Compose(
        [
            A.RandomBrightnessContrast(
                brightness_limit=0.2,
                contrast_limit=0.2,
                p=0.5,
            ),
            A.HueSaturationValue(
                hue_shift_limit=10,
                sat_shift_limit=20,
                val_shift_limit=20,
                p=0.5,
            ),
            A.GaussNoise(
                var_limit=(5.0, 30.0),
                p=0.3,
            ),
            A.ISONoise(
                color_shift=(0.01, 0.05),
                intensity=(0.1, 0.3),
                p=0.3,
            ),
            A.MotionBlur(
                blur_limit=3,
                p=0.2,
            ),
            A.ImageCompression(
                quality_lower=60,
                quality_upper=100,
                p=0.3,
            ),
            A.Rotate(
                limit=15,
                border_mode=cv2.BORDER_CONSTANT,
                p=0.5,
            ),
            A.RandomScale(
                scale_limit=0.1,
                p=0.5,
            ),
            A.HorizontalFlip(
                p=0.5,
            ),
            A.CoarseDropout(
                max_holes=8,
                max_height=16,
                max_width=16,
                fill_value=128,
                p=0.2,
            ),
            A.Resize(image_size, image_size),
            A.Normalize(
                mean=[0.5, 0.5, 0.5],
                std=[0.5, 0.5, 0.5],
            ),
        ],
        p=p,
    )


def get_validation_augmentation(
    image_size: int = 160,
) -> A.Compose:
    return A.Compose(
        [
            A.Resize(image_size, image_size),
            A.Normalize(
                mean=[0.5, 0.5, 0.5],
                std=[0.5, 0.5, 0.5],
            ),
        ]
    )


def get_test_augmentation(
    image_size: int = 160,
) -> A.Compose:
    return A.Compose(
        [
            A.Resize(image_size, image_size),
            A.Normalize(
                mean=[0.5, 0.5, 0.5],
                std=[0.5, 0.5, 0.5],
            ),
        ]
    )


def get_identity_preserving_augmentation(
    image_size: int = 160,
) -> A.Compose:
    return A.Compose(
        [
            A.RandomBrightnessContrast(
                brightness_limit=0.15,
                contrast_limit=0.15,
                p=0.5,
            ),
            A.HueSaturationValue(
                hue_shift_limit=5,
                sat_shift_limit=10,
                val_shift_limit=10,
                p=0.4,
            ),
            A.GaussNoise(
                var_limit=(5.0, 15.0),
                p=0.2,
            ),
            A.ImageCompression(
                quality_lower=70,
                quality_upper=100,
                p=0.2,
            ),
            A.Resize(image_size, image_size),
            A.Normalize(
                mean=[0.5, 0.5, 0.5],
                std=[0.5, 0.5, 0.5],
            ),
        ]
    )


def get_aggressive_augmentation(
    image_size: int = 160,
) -> A.Compose:
    return A.Compose(
        [
            A.RandomBrightnessContrast(
                brightness_limit=0.3,
                contrast_limit=0.3,
                p=0.7,
            ),
            A.HueSaturationValue(
                hue_shift_limit=15,
                sat_shift_limit=25,
                val_shift_limit=25,
                p=0.7,
            ),
            A.GaussNoise(
                var_limit=(10.0, 50.0),
                p=0.5,
            ),
            A.ISONoise(
                color_shift=(0.02, 0.08),
                intensity=(0.15, 0.4),
                p=0.5,
            ),
            A.MotionBlur(
                blur_limit=5,
                p=0.4,
            ),
            A.GaussianBlur(
                blur_limit=3,
                p=0.3,
            ),
            A.ImageCompression(
                quality_lower=40,
                p=0.4,
            ),
            A.Rotate(
                limit=20,
                border_mode=cv2.BORDER_CONSTANT,
                p=0.6,
            ),
            A.RandomScale(
                scale_limit=0.15,
                p=0.6,
            ),
            A.HorizontalFlip(
                p=0.5,
            ),
            A.CoarseDropout(
                max_holes=12,
                max_height=20,
                max_width=20,
                min_holes=4,
                fill_value=128,
                p=0.3,
            ),
            A.Resize(image_size, image_size),
            A.Normalize(
                mean=[0.5, 0.5, 0.5],
                std=[0.5, 0.5, 0.5],
            ),
        ]
    )


class FaceAugmentationPipeline:
    def __init__(
        self,
        image_size: int = 160,
        mode: str = "training",
        p: float = 0.8,
    ):
        self.image_size = image_size
        self.mode = mode

        if mode == "training":
            self.transform = get_training_augmentation(image_size, p)
        elif mode == "validation":
            self.transform = get_validation_augmentation(image_size)
        elif mode == "test":
            self.transform = get_test_augmentation(image_size)
        elif mode == "identity":
            self.transform = get_identity_preserving_augmentation(image_size)
        elif mode == "aggressive":
            self.transform = get_aggressive_augmentation(image_size)
        else:
            raise ValueError(f"Unknown augmentation mode: {mode}")

    def __call__(self, image: np.ndarray) -> torch.Tensor:
        augmented = self.transform(image=image)
        tensor = torch.from_numpy(augmented["image"]).float()
        return tensor

    def augment_batch(self, images: list[np.ndarray], n_augment: int = 1) -> list[torch.Tensor]:
        augmented_images = []
        for _ in range(n_augment):
            for image in images:
                augmented_images.append(self(image))
        return augmented_images


class Mixup:
    def __init__(self, alpha: float = 0.2):
        self.alpha = alpha

    def __call__(
        self,
        images: torch.Tensor,
        labels: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, float]:
        if self.alpha > 0:
            lam = np.random.beta(self.alpha, self.alpha)
        else:
            lam = 1.0

        batch_size = images.size(0)
        index = torch.randperm(batch_size, device=images.device)

        mixed_images = lam * images + (1 - lam) * images[index, :]
        labels_a, labels_b = labels, labels[index]

        return mixed_images, labels_a, labels_b, lam


class CutMix:
    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha

    def __call__(
        self,
        images: torch.Tensor,
        labels: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        if self.alpha > 0:
            lam = np.random.beta(self.alpha, self.alpha)
        else:
            lam = 1.0

        batch_size, _, h, w = images.shape
        index = torch.randperm(batch_size, device=images.device)

        cut_rat = np.sqrt(1.0 - lam)
        cut_w = int(w * cut_rat)
        cut_h = int(h * cut_rat)

        cx = np.random.randint(w)
        cy = np.random.randint(h)

        bbx1 = np.clip(cx - cut_w // 2, 0, w)
        bby1 = np.clip(cy - cut_h // 2, 0, h)
        bbx2 = np.clip(cx + cut_w // 2, 0, w)
        bby2 = np.clip(cy + cut_h // 2, 0, h)

        mixed_images = images.clone()
        mixed_images[:, :, bby1:bby2, bbx1:bbx2] = images[index, :, bby1:bby2, bbx1:bbx2]

        lam = 1.0 - ((bbx2 - bbx1) * (bby2 - bby1) / (w * h))
        labels_a, labels_b = labels, labels[index]

        return mixed_images, labels_a, labels_b, lam


def mixup_criterion(
    criterion: Callable,
    pred: torch.Tensor,
    labels_a: torch.Tensor,
    labels_b: torch.Tensor,
    lam: float,
) -> torch.Tensor:
    return lam * criterion(pred, labels_a) + (1 - lam) * criterion(pred, labels_b)


def cutmix_criterion(
    criterion: Callable,
    pred: torch.Tensor,
    labels_a: torch.Tensor,
    labels_b: torch.Tensor,
    lam: float,
) -> torch.Tensor:
    return lam * criterion(pred, labels_a) + (1 - lam) * criterion(pred, labels_b)
