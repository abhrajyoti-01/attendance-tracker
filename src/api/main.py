import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from src.api.metrics import http_request_duration, http_requests_total
from src.config import settings
from src.utils.logger import configure_logging, get_logger, log_exception

configure_logging(settings.logging.level, settings.logging.format)
logger = get_logger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
}


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid4()))
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        structlog.contextvars.unbind_contextvars("correlation_id")
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Starting Attendance Tracker API",
        version=app.version,
        env=settings.env,
    )

    try:
        from src.database.session import check_schema_current, close_db, engine

        async with engine.connect() as conn:
            if not await check_schema_current(conn):
                raise RuntimeError(
                    "Database schema is out of date. Run 'alembic upgrade head' before starting."
                )
        logger.info("Database schema verified")
    except Exception as e:
        logger.error("Database initialization failed", error=str(e))
        raise

    try:
        from src.inference.embedding_engine import EmbeddingEngineLazy

        engine_obj = EmbeddingEngineLazy.get_instance()
        logger.info("Embedding model loaded", model_info=engine_obj.get_model_info())
    except Exception as e:
        logger.warning("Embedding model not available", error=str(e))

    app.state.redis = None
    try:
        import redis.asyncio as aioredis

        app.state.redis = await aioredis.from_url(
            settings.redis.url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.redis.max_connections,
            socket_timeout=settings.redis.socket_timeout,
            socket_connect_timeout=settings.redis.socket_connect_timeout,
        )
        await app.state.redis.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning("Redis not available - token revocation degraded", error=str(e))
        if settings.is_production and settings.security.require_redis:
            raise

    try:
        from src.inference.index_manager import matcher_registry

        await matcher_registry.initialize()
        logger.info("Matcher registry initialized")
    except Exception as e:
        logger.warning("Matcher warm-up deferred to first use", error=str(e))

    yield

    logger.info("Shutting down Attendance Tracker API")

    try:
        from src.inference.index_manager import matcher_registry

        await matcher_registry.shutdown()
    except Exception:
        pass

    redis = getattr(app.state, "redis", None)
    if redis is not None:
        await redis.close()

    from src.database.session import close_db

    await close_db()


app = FastAPI(
    title="Attendance Tracker API",
    description="Multi-tenant face recognition attendance system",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else settings.security.docs_url_in_production,
    redoc_url=None,
    openapi_url="/openapi.json"
    if not settings.is_production
    else settings.security.docs_url_in_production,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)

# Registered unconditionally; the middleware itself no-ops when disabled
# (settings.rate_limit.enabled), allowing runtime toggling without redeploy.
from src.api.middleware.rate_limit import RateLimitMiddleware  # noqa: E402

app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors.origins,
    allow_credentials=settings.cors.allow_credentials,
    allow_methods=settings.cors.allow_methods,
    allow_headers=settings.cors.allow_headers,
)


@app.middleware("http")
async def observe_requests(request: Request, call_next: RequestResponseEndpoint) -> Response:
    start_time = time.perf_counter()
    method = request.method
    path = request.url.path
    client_ip = request.client.host if request.client else None
    # Use the route template, not the raw URL, so /api/users/{id} does not
    # create one Prometheus time series per user ID.
    endpoint = path

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        http_requests_total.labels(method=method, endpoint=endpoint, status_code=500).inc()
        log_exception(
            logger, exc, method=method, path=path, duration_ms=duration_ms, client_ip=client_ip
        )
        raise

    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        endpoint = route.path

    duration_ms = (time.perf_counter() - start_time) * 1000
    http_requests_total.labels(
        method=method, endpoint=endpoint, status_code=response.status_code
    ).inc()
    http_request_duration.labels(method=method, endpoint=endpoint).observe(duration_ms / 1000)

    if path != "/health":
        logger.info(
            "Request completed",
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            client_ip=client_ip,
        )

    response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors()[:10]:
        # pydantic v2 embeds non-serializable exception objects in 'ctx'.
        cleaned = {k: v for k, v in err.items() if k not in ("ctx", "url")}
        errors.append(cleaned)
    logger.warning("Validation error", path=request.url.path, count=len(exc.errors()))
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError):
    log_exception(logger, exc, path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "A database error occurred"},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    log_exception(logger, exc, path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.get("/", tags=["Health"], include_in_schema=False)
async def root():
    return {
        "name": "Attendance Tracker API",
        "version": app.version,
        "status": "running",
        "environment": settings.env,
    }


@app.get("/health", tags=["Health"])
async def health_check(request: Request):
    checks = {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

    try:
        from sqlalchemy import text

        from src.database.session import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"

    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {type(e).__name__}"
    else:
        checks["redis"] = "not_configured"

    try:
        from src.inference.index_manager import matcher_registry

        checks["model_loaded"] = matcher_registry.model_available
        checks["indexed_users"] = matcher_registry.total_indexed_users
    except Exception as e:
        checks["model_loaded"] = False
        checks["model_error"] = type(e).__name__

    critical_ok = checks.get("database") == "ok"
    if not critical_ok:
        checks["status"] = "unhealthy"
    elif checks.get("redis") != "ok" or not checks.get("model_loaded"):
        checks["status"] = "degraded"

    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if checks["status"] == "unhealthy"
        else status.HTTP_200_OK
    )
    return JSONResponse(status_code=status_code, content=checks)


if settings.monitoring.prometheus_enabled:
    app.mount("/metrics", make_asgi_app())
    logger.info("Prometheus metrics enabled at /metrics")

from src.api.routes.admin import router as admin_router  # noqa: E402
from src.api.routes.attendance import router as attendance_router  # noqa: E402
from src.api.routes.auth import router as auth_router  # noqa: E402
from src.api.routes.dashboard import router as dashboard_router  # noqa: E402
from src.api.routes.departments import router as departments_router  # noqa: E402
from src.api.routes.recognition import router as recognition_router  # noqa: E402
from src.api.routes.users import router as users_router  # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(departments_router, prefix="/api/departments", tags=["Departments"])
app.include_router(recognition_router, prefix="/api/recognize", tags=["Recognition"])
app.include_router(attendance_router, prefix="/api/attendance", tags=["Attendance"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])
