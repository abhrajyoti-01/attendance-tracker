from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

celery_app = Celery(
    "attendance_tracker",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
    include=[
        "src.workers.tasks.embedding_tasks",
        "src.workers.tasks.training_tasks",
        "src.workers.tasks.export_tasks",
        "src.workers.tasks.maintenance_tasks",
    ],
)


@worker_ready.connect
def _ensure_object_storage_buckets(sender=None, **kwargs):  # pragma: no cover - startup hook
    """Create the export/image buckets once per worker pool.

    Exports write to a bucket that was previously never provisioned, so a fresh
    MinIO deployment failed every async export with NoSuchBucket.
    """
    from src.services.storage import ensure_buckets, is_available

    if not is_available():
        return
    try:
        ensure_buckets()
    except Exception as exc:
        logger.warning("Could not provision object storage buckets", error=str(exc))


celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes=settings.celery.task_routes,
    task_track_started=True,
    task_send_sent_event=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    beat_schedule={
        "cleanup-expired-auth-tokens": {
            "task": "src.workers.tasks.maintenance_tasks.cleanup_expired_tokens_task",
            "schedule": crontab(hour=3, minute=0),
        },
        "purge-old-spoof-snapshots": {
            "task": "src.workers.tasks.maintenance_tasks.purge_old_spoof_attempts_task",
            "schedule": crontab(hour=3, minute=30),
        },
        "refresh-matcher-indexes": {
            "task": "src.workers.tasks.maintenance_tasks.refresh_matcher_indexes_task",
            "schedule": crontab(minute="*/30"),
        },
    },
)
