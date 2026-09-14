"""Embedding-related background tasks."""

from uuid import UUID

import numpy as np
import structlog

from src.workers.async_runner import run_async
from src.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True)
def compute_embedding_task(self, user_id: str, image_paths: list[str]):
    """Compute and persist a mean face embedding for a user from image files on disk.

    Quality gates apply: images failing detection or quality checks are rejected,
    and the task fails when fewer than three usable samples remain.
    """
    try:
        import cv2
        from sqlalchemy import select

        from src.database.models import Embedding, User
        from src.database.session import get_db_context
        from src.inference.embedding_engine import EmbeddingEngineLazy
        from src.preprocessing.face_detector import FaceDetectorLazy
        from src.preprocessing.quality_checker import QualityChecker

        detector = FaceDetectorLazy.get_instance()
        engine = EmbeddingEngineLazy.get_instance()
        quality_checker = QualityChecker()

        embeddings: list[np.ndarray] = []
        quality_scores: list[float] = []
        rejected = {"unreadable": 0, "no_face": 0, "low_quality": 0}

        for path in image_paths:
            image = cv2.imread(path)
            if image is None:
                rejected["unreadable"] += 1
                continue

            face = detector.detect(image)
            if face is None:
                rejected["no_face"] += 1
                continue

            face_uint8 = face
            quality = quality_checker.check(face_uint8)
            if not quality["valid"]:
                rejected["low_quality"] += 1
                continue

            embeddings.append(engine.compute_single(face))
            quality_scores.append(float(quality["overall_score"]))

        if len(embeddings) < 3:
            return {
                "success": False,
                "user_id": user_id,
                "error": (
                    f"Insufficient valid samples ({len(embeddings)}/3); " f"rejected={rejected}"
                ),
            }

        stacked = np.stack(embeddings)
        mean_vector = stacked.mean(axis=0)
        mean_vector /= float(np.linalg.norm(mean_vector) + 1e-10)

        async def _persist():
            async with get_db_context() as db:
                result = await db.execute(
                    select(Embedding).where(Embedding.user_id == UUID(user_id))
                )
                record = result.scalar_one_or_none()
                vector_bytes = mean_vector.astype("<f4").tobytes()
                avg_quality = sum(quality_scores) / len(quality_scores)

                if record is not None:
                    record.vector = vector_bytes
                    record.num_samples = len(embeddings)
                    record.quality_score = avg_quality
                    record.version += 1
                else:
                    user_exists = await db.execute(select(User.id).where(User.id == UUID(user_id)))
                    if user_exists.scalar_one_or_none() is None:
                        raise ValueError(f"User {user_id} does not exist")
                    db.add(
                        Embedding(
                            user_id=UUID(user_id),
                            vector=vector_bytes,
                            num_samples=len(embeddings),
                            quality_score=avg_quality,
                            version=1,
                        )
                    )
                await db.commit()
                return avg_quality

        avg_quality = run_async(_persist())

        logger.info(
            "Embedding computed",
            user_id=user_id,
            samples=len(embeddings),
            rejected=rejected,
        )
        return {
            "success": True,
            "user_id": user_id,
            "num_samples": len(embeddings),
            "quality_score": avg_quality,
            "embedding_dim": int(mean_vector.shape[0]),
            "rejected": rejected,
        }

    except Exception as exc:
        logger.exception("Embedding computation failed", user_id=user_id)
        return {"success": False, "user_id": user_id, "error": str(exc)}
