"""Single-GPU fine-tuning of the FaceNet embedding model.

Trains InceptionResnetV1 (VGGFace2 init) with batch-hard triplet mining on a
PK-sampled loader, evaluates on an identity-disjoint holdout each epoch, and
exports the best checkpoint to ONNX for inference.
"""

import math
import time
from collections.abc import Callable
from pathlib import Path

import structlog
import torch
from torch import optim

from src.config import settings
from src.model.losses import BatchHardTripletLoss
from src.training.dataset import build_train_val_datasets, make_pk_loader

logger = structlog.get_logger(__name__)


class TrainingConfig:
    def __init__(self, config: dict):
        self.data_root: str = str(config["data_root"])
        self.output_dir: str = str(config.get("output_dir", "models/checkpoints"))
        self.epochs: int = int(config.get("epochs", 10))
        self.p: int = int(config.get("p", 8))
        self.k: int = int(config.get("k", 4))
        self.learning_rate: float = float(config.get("learning_rate", 1e-4))
        self.weight_decay: float = float(config.get("weight_decay", 1e-5))
        self.margin: float = float(config.get("margin", 0.3))
        self.val_ratio: float = float(config.get("val_ratio", 0.2))
        self.num_workers: int = int(config.get("num_workers", settings.model.num_workers))
        self.seed: int = int(config.get("seed", 42))
        self.target_far: float = float(config.get("target_far", 1e-3))
        self.export_onnx: bool = bool(config.get("export_onnx", True))

    def as_dict(self) -> dict:
        return {
            "data_root": self.data_root,
            "epochs": self.epochs,
            "p": self.p,
            "k": self.k,
            "learning_rate": self.learning_rate,
            "weight_decay": self.weight_decay,
            "margin": self.margin,
            "val_ratio": self.val_ratio,
            "seed": self.seed,
            "target_far": self.target_far,
        }


def _build_model(device: str) -> torch.nn.Module:
    from facenet_pytorch import InceptionResnetV1

    model = InceptionResnetV1(pretrained="vggface2", classify=False, num_classes=None)
    return model.to(device).train()


class Trainer:
    def __init__(
        self,
        config: TrainingConfig,
        *,
        progress_cb: Callable[[dict], None] | None = None,
    ):
        self.config = config
        self.progress_cb = progress_cb or (lambda event: None)
        self.device = (
            "cuda" if torch.cuda.is_available() and settings.model.device == "cuda" else "cpu"
        )

        torch.manual_seed(config.seed)

        self.train_set, self.val_set, self.data_stats = build_train_val_datasets(
            config.data_root,
            val_ratio=config.val_ratio,
            seed=config.seed,
        )
        self.train_loader, self.sampler = make_pk_loader(
            self.train_set,
            p=config.p,
            k=config.k,
            num_workers=config.num_workers,
            seed=config.seed,
        )
        # Validation uses ordinary batching; identity coverage is what matters.
        from torch.utils.data import DataLoader

        self.val_loader = DataLoader(
            self.val_set,
            batch_size=32,
            shuffle=False,
            num_workers=config.num_workers,
            pin_memory=self.device == "cuda",
        )

        self.model = _build_model(self.device)
        self.criterion = BatchHardTripletLoss(margin=config.margin)
        self.optimizer = optim.AdamW(
            self.model.parameters(),
            lr=config.learning_rate,
            weight_decay=config.weight_decay,
        )
        self.scheduler = optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer, T_max=max(1, config.epochs)
        )
        self.use_amp = self.device == "cuda"
        self.scaler = torch.amp.GradScaler("cuda", enabled=self.use_amp)

        output = Path(config.output_dir) / f"run_{int(time.time())}"
        output.mkdir(parents=True, exist_ok=True)
        self.output_dir = output

    def _report(self, event: dict) -> None:
        self.progress_cb(event)

    def train_epoch(self, epoch: int) -> float:
        running_loss, batches = 0.0, 0
        for images, labels in self.train_loader:
            images = images.to(self.device, non_blocking=True)
            labels = labels.to(self.device, non_blocking=True)

            self.optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", enabled=self.use_amp):
                embeddings = self.model(images)
                loss = self.criterion(embeddings, labels)

            if not math.isfinite(float(loss.detach())):
                logger.warning("Non-finite loss skipped", epoch=epoch)
                continue

            self.scaler.scale(loss).backward()
            self.scaler.unscale_(self.optimizer)
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=5.0)
            self.scaler.step(self.optimizer)
            self.scaler.update()

            running_loss += float(loss.detach())
            batches += 1

        self.scheduler.step()
        return running_loss / max(1, batches)

    def run(self) -> dict:
        from src.training.evaluator import evaluate

        cfg = self.config
        best_metric = -1.0
        best_checkpoint: Path | None = None
        history: list[dict] = []

        self._report(
            {
                "event": "training_started",
                "device": self.device,
                **self.data_stats,
                "total_epochs": cfg.epochs,
            }
        )

        for epoch in range(1, cfg.epochs + 1):
            epoch_start = time.time()
            train_loss = self.train_epoch(epoch)

            metrics = evaluate(self.model, self.val_loader, self.device, target_far=cfg.target_far)
            elapsed = time.time() - epoch_start

            epoch_record = {
                "epoch": epoch,
                "train_loss": round(train_loss, 5),
                "elapsed_seconds": round(elapsed, 1),
                "eval": metrics,
            }
            history.append(epoch_record)
            self._report({"event": "epoch_complete", **epoch_record})
            logger.info("Epoch complete", **epoch_record)

            primary = (
                metrics.get(f"tpr_at_far_{cfg.target_far:g}", -1.0)
                if metrics.get("valid")
                else -1.0
            )
            if metrics.get("valid") and primary > best_metric:
                best_metric = primary
                best_checkpoint = self.output_dir / "best.pt"
                torch.save(
                    {
                        "model_state_dict": self.model.state_dict(),
                        "epoch": epoch,
                        "metrics": metrics,
                        "config": cfg.as_dict(),
                    },
                    best_checkpoint,
                )

        checkpoint_path: Path | None = best_checkpoint
        if checkpoint_path is None:
            checkpoint_path = self.output_dir / "last.pt"
            torch.save(
                {
                    "model_state_dict": self.model.state_dict(),
                    "epoch": cfg.epochs,
                    "metrics": {},
                    "config": cfg.as_dict(),
                },
                checkpoint_path,
            )

        final_eval = evaluate(self.model, self.val_loader, self.device, target_far=cfg.target_far)

        onnx_path: Path | None = None
        if cfg.export_onnx:
            try:
                onnx_path = self._export_onnx()
            except Exception as exc:
                logger.exception("ONNX export failed", error=str(exc))

        result = {
            "checkpoint_path": str(checkpoint_path),
            "onnx_path": str(onnx_path) if onnx_path else None,
            "best_tpr_at_target_far": round(best_metric, 4),
            "final_eval": final_eval,
            "history": history[-cfg.epochs :],
            "data_stats": self.data_stats,
        }
        self._report({"event": "training_complete", **result})
        return result

    def _export_onnx(self) -> Path:
        path = self.output_dir / "embedding_net.onnx"
        self.model.eval()

        dummy = torch.randn(1, 3, settings.model.face_image_size, settings.model.face_image_size)

        def normalized_forward(x):
            out = self.model(x)
            return torch.nn.functional.normalize(out, p=2, dim=1)

        with torch.no_grad():
            torch.onnx.export(
                normalized_forward,
                dummy.to(self.device),
                str(path),
                input_names=["input.1"],
                output_names=["embedding"],
                opset_version=14,
                do_constant_folding=True,
                dynamic_axes={"input.1": {0: "batch"}, "embedding": {0: "batch"}},
            )
        logger.info("Fine-tuned model exported", path=str(path))
        return path


def run_training(config: TrainingConfig, progress_cb=None) -> dict:
    trainer = Trainer(config, progress_cb=progress_cb)
    return trainer.run()
