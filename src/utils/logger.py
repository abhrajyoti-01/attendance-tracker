import logging
import sys

import structlog

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def configure_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    level = _LEVEL_MAP.get(log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.EventRenamer("message"),
    ]

    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(
                colors=sys.stdout.isatty(),
            )
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def log_exception(logger: structlog.BoundLogger, exc: Exception, **kwargs) -> None:
    logger.exception(
        "Exception occurred",
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        **kwargs,
    )


def set_correlation_id(correlation_id: str) -> None:
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def get_correlation_id() -> str:
    import structlog.contextvars

    return structlog.contextvars.get_contextvars().get("correlation_id", "")


class RequestLogger:
    def __init__(self, logger: structlog.BoundLogger):
        self.logger = logger

    def log_request(
        self,
        method: str,
        path: str,
        client_ip: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.logger.info(
            "Incoming request",
            method=method,
            path=path,
            client_ip=client_ip,
            user_id=user_id,
        )

    def log_response(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.logger.info(
            "Request completed",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
            user_id=user_id,
        )

    def log_error(
        self,
        method: str,
        path: str,
        status_code: int,
        error: str,
        duration_ms: float,
        client_ip: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.logger.error(
            "Request error",
            method=method,
            path=path,
            status_code=status_code,
            error=error,
            duration_ms=duration_ms,
            client_ip=client_ip,
            user_id=user_id,
        )
