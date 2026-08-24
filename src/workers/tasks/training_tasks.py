"""Training job orchestration.

``start_training_task`` drives the full lifecycle: pending -> running ->
completed/failed, persisting metrics, checkpoint path, and ONNX export path to
the ``training_jobs`` table so the API can poll real status.
"""

import asyncio
import traceback
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select

from src.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

JOB_STATUSES = ("pending", "running", "completed", "failed")


async def _load_job(job_id: UUID):
    from src.database.models import TrainingJob
    from src.database.session import get_db_context

    async with get_db_context() as db:
        result = await db.execute(select(TrainingJob).where(TrainingJob.id == job_id))
        return result.scalar_one_or_none()


@celery_app.task(bind=True)
def start_training_task(self, job_id: str):
    """Execute a previously-created TrainingJob. The API creates the row first."""

    async def _run():
        from src.database.models import TrainingJob
        from src.database.session import get_db_context

        async with get_db_context() as db:
            result = await db.execute(select(TrainingJob).where(TrainingJob.id == UUID(job_id)))
            job = result.scalar_one_or_none()
            if job is None:
                raise ValueError(f"TrainingJob {job_id} does not exist")
            if job.status == "running":
                raise ValueError(f"TrainingJob {job_id} already running")

            job.status = "running"
            job.started_at = datetime.now(UTC)
            job.celery_task_id = self.request.id
            await db.commit()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.exception("Unable to start training job", job_id=job_id)
        return {"success": False, "job_id": job_id, "error": str(exc)}

    def progress(event: dict) -> None:
        logger.info(
            "Training progress", job_id=job_id, **{k: v for k, v in event.items() if k != "history"}
        )

    def update_job(
        status_value: str,
        *,
        metrics: dict | None = None,
        error: str | None = None,
        checkpoint: str | None = None,
        onnx: str | None = None,
    ) -> None:
        async def _update():
            from src.database.models import TrainingJob
            from src.database.session import get_db_context

            async with get_db_context() as db:
                result = await db.execute(select(TrainingJob).where(TrainingJob.id == UUID(job_id)))
                job = result.scalar_one_or_none()
                if job is None:
                    return
                job.status = status_value
                if status_value in ("completed", "failed"):
                    job.completed_at = datetime.now(UTC)
                if metrics:
                    job.metrics = {**(job.metrics or {}), **metrics}
                if error:
                    job.error_message = error[:4000]
                if checkpoint:
                    job.checkpoint_path = checkpoint
                if onnx:
                    job.onnx_path = onnx
                await db.commit()

        asyncio.run(_update())

    try:
        from src.training.trainer import TrainingConfig, run_training

        job_row = asyncio.run(_load_job(UUID(job_id)))
        config_dict = dict(job_row.config or {}) if job_row else {}
        config = TrainingConfig(config_dict)

        result = run_training(config, progress_cb=progress)

        history_trimmed = result.get("history", [])
        final_eval = result.get("final_eval", {})
        update_job(
            "completed",
            metrics={
                **({"eval": final_eval} if final_eval else {}),
                "best_tpr_at_target_far": result.get("best_tpr_at_target_far"),
                "data_stats": result.get("data_stats"),
                "epochs_run": len(history_trimmed),
            },
            checkpoint=result.get("checkpoint_path"),
            onnx=result.get("onnx_path"),
        )
        return {"success": True, "job_id": job_id, "checkpoint": result.get("checkpoint_path")}

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.error("Training failed", job_id=job_id, error=detail)
        logger.debug("Traceback", tb=traceback.format_exc())
        update_job("failed", error=detail)
        return {"success": False, "job_id": job_id, "error": detail}


@celery_app.task
def check_training_status_task(job_id: str):
    """Read current persisted status for a training job."""
    try:
        row = asyncio.run(_load_job(UUID(job_id)))
        if row is None:
            return {"success": False, "job_id": job_id, "error": "job not found"}
        return {
            "success": True,
            "job_id": job_id,
            "status": row.status,
            "metrics": row.metrics or {},
            "error_message": row.error_message,
            "onnx_path": row.onnx_path,
        }
    except Exception as exc:
        return {"success": False, "job_id": job_id, "error": str(exc)}
