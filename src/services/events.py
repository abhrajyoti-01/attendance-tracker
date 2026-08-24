"""In-process + Redis pub/sub event bus for live attendance feeds.

Events published by the attendance service are fanned out to all SSE
subscribers of the same organization. When Redis is reachable, pub/sub is
used so events cross process boundaries (the API runs multiple uvicorn
workers). Without Redis, a process-local asyncio queue fan-out keeps the
feature functional in development and tests.
"""

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncGenerator

import structlog

logger = structlog.get_logger(__name__)

CHANNEL_PREFIX = "attendance-events"
LOCAL_QUEUE_LIMIT = 200

# org_id -> subscriber queues
_local_subscribers: defaultdict[str, set[asyncio.Queue]] = defaultdict(set)

_redis_client = None
_redis_failed = False


async def _get_redis():
    """Lazily create a dedicated Redis client for pub/sub; None when unavailable."""
    global _redis_client, _redis_failed
    if _redis_failed:
        return None
    if _redis_client is None:
        try:
            import redis.asyncio as aioredis

            from src.config import settings

            _redis_client = aioredis.from_url(
                settings.redis.url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=settings.redis.socket_timeout,
                socket_connect_timeout=settings.redis.socket_connect_timeout,
            )
            await _redis_client.ping()
        except Exception as exc:
            logger.debug("Redis pub/sub unavailable; using local event fan-out", error=str(exc))
            _redis_client = None
            _redis_failed = True
            return None
    return _redis_client


def reset_event_bus_for_tests() -> None:
    """Clear local subscribers and force Redis re-detection. Test helper only."""
    global _redis_client, _redis_failed
    for queues in _local_subscribers.values():
        queues.clear()
    _local_subscribers.clear()
    if _redis_client is not None:
        try:
            _redis_client.aclose()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
    _redis_client = None
    _redis_failed = False


async def publish_attendance_event(organization_id: str, payload: dict) -> None:
    """Publish an attendance event to every subscriber of the organization.

    Never raises: a broken feed must not fail attendance marking.
    """
    try:
        data = json.dumps(payload, default=str)
        redis = await _get_redis()
        if redis is not None:
            await redis.publish(f"{CHANNEL_PREFIX}:{organization_id}", data)
            return

        queue_full = 0
        for queue in list(_local_subscribers.get(organization_id, ())):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                queue_full += 1
        if queue_full:
            logger.warning(
                "Dropped live-feed events for slow subscribers",
                organization_id=organization_id,
                dropped=queue_full,
            )
    except Exception:
        logger.exception("Failed to publish attendance event", organization_id=organization_id)


async def subscribe_attendance_events(organization_id: str) -> AsyncGenerator[str, None]:
    """Yield raw JSON payloads published for the given organization."""
    redis = await _get_redis()
    if redis is not None:
        channel = f"{CHANNEL_PREFIX}:{organization_id}"
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    yield message["data"]
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=LOCAL_QUEUE_LIMIT)
    _local_subscribers[organization_id].add(queue)
    try:
        while True:
            yield await queue.get()
    finally:
        _local_subscribers[organization_id].discard(queue)
        if not _local_subscribers[organization_id]:
            del _local_subscribers[organization_id]
