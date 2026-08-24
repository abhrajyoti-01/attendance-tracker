from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID

import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=(
                f"{'https' if settings.storage.use_ssl else 'http'}://"
                f"{settings.storage.endpoint}"
            ),
            aws_access_key_id=settings.storage.access_key,
            aws_secret_access_key=settings.storage.secret_key,
            region_name=settings.storage.region,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )
    return _client


def ensure_buckets() -> None:
    """Idempotently create the configured buckets. Call from worker startup."""
    if not settings.storage.enabled:
        return
    client = _get_client()
    for bucket in {settings.storage.bucket_name, settings.storage.export_bucket_name}:
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            logger.info("Creating storage bucket", bucket=bucket)
            client.create_bucket(Bucket=bucket)


def build_object_key(*parts: str) -> str:
    now = datetime.now(UTC)
    return str(PurePosixPath(now.strftime("%Y/%m/%d"), *parts))


def upload_bytes(key: str, data: bytes, content_type: str) -> str:
    bucket = (
        settings.storage.export_bucket_name
        if key.startswith("exports/")
        else settings.storage.bucket_name
    )
    _get_client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    return f"{bucket}/{key}"


def presign_get_url(storage_ref: str, expires_seconds: int | None = None) -> str:
    bucket, _, key = storage_ref.partition("/")
    expiry = expires_seconds or settings.storage.presigned_url_expiry_seconds
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry,
    )


def export_object_key(organization_id: UUID, fmt: str, requested_at: datetime) -> str:
    stamp = requested_at.strftime("%Y%m%d_%H%M%S")
    return f"exports/{organization_id}/{stamp}.{fmt}"


def is_available() -> bool:
    return settings.storage.enabled and bool(
        settings.storage.access_key and settings.storage.secret_key
    )


def retention_cutoff(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)
