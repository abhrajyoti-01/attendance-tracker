import time

import structlog
from fastapi import FastAPI, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.config import settings

logger = structlog.get_logger(__name__)

WINDOW_SECONDS = 60


def _resolve_scope(path: str) -> str | None:
    if not path.startswith("/api/"):
        return None
    if path.startswith("/api/auth"):
        return "auth"
    if path.startswith("/api/recognize"):
        return "recognition"
    return "general"


def _limit_for(scope: str) -> int:
    return {
        "auth": settings.rate_limit.auth_requests_per_minute,
        "recognition": settings.rate_limit.recognition_requests_per_minute,
        "general": settings.rate_limit.requests_per_minute,
    }[scope]


class _MemoryCounter:
    """Process-local fallback when Redis is unavailable."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._window_started_at = time.time()

    def incr(self, key: str) -> int:
        now = time.time()
        if now - self._window_started_at >= WINDOW_SECONDS:
            self._counters.clear()
            self._window_started_at = now
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self._memory = _MemoryCounter()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        scope = _resolve_scope(request.url.path)
        if scope is None or not settings.rate_limit.enabled:
            return await call_next(request)
        if settings.rate_limit.enabled_in_production_only and not settings.is_production:
            return await call_next(request)

        limit = _limit_for(scope)
        client_ip = client_ip_from_request(request)
        window_epoch = int(time.time() // WINDOW_SECONDS)
        counter_key = f"ratelimit:{scope}:{client_ip}:{window_epoch}"

        count = await self._incr(request, counter_key)
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, limit - count)),
        }
        if count > limit:
            headers["Retry-After"] = str(WINDOW_SECONDS - int(time.time()) % WINDOW_SECONDS)
            logger.warning(
                "Rate limit exceeded",
                scope=scope,
                client_ip=client_ip,
                path=request.url.path,
            )
            return Response(
                content=('{"detail": "Too many requests. Please retry later."}'),
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
                headers=headers,
            )

        response = await call_next(request)
        for header, value in headers.items():
            response.headers.setdefault(header, value)
        return response

    async def _incr(self, request: Request, key: str) -> int:
        # scope["app"] is always the FastAPI instance. `self.app` is the *inner*
        # middleware (add_middleware composes in reverse) and has no .state.
        app = request.scope.get("app")
        redis = getattr(getattr(app, "state", None), "redis", None)
        if redis is not None:
            try:
                pipe = redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, WINDOW_SECONDS + 5)
                count = (await pipe.execute())[0]
                return int(count)
            except Exception as exc:
                logger.debug("Redis rate limit unavailable; using memory", error=str(exc))
        return self._memory.incr(key)


def client_ip_from_request(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"
