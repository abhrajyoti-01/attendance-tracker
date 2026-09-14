"""A single, process-wide asyncio event loop for Celery workers.

The async SQLAlchemy engine and its asyncpg connection pool are created at
import time and are therefore bound to whichever event loop first uses them.
Celery prefork workers execute many tasks per process, so wrapping each task in
``asyncio.run()`` hands the pool a *different* loop every time: the second task
in a worker reuses connections tied to a closed loop and fails with
"attached to a different loop" / "Event loop is closed" errors.

Running every task on one long-lived loop in a dedicated daemon thread keeps
the pool valid for the lifetime of the worker process.
"""

import asyncio
import atexit
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _start_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_run, name="celery-async-loop", daemon=True)
    thread.start()

    global _loop, _thread
    _loop, _thread = loop, thread
    return loop


def get_loop() -> asyncio.AbstractEventLoop:
    with _lock:
        if _loop is None or _loop.is_closed():
            return _start_loop()
        return _loop


def run_async(coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
    """Run ``coro`` to completion on the worker's shared event loop.

    Safe to call from any Celery worker thread and from sequential tasks in the
    same process. ``timeout`` is in seconds; None waits indefinitely.
    """
    loop = get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except BaseException:
        future.cancel()
        raise


def _shutdown() -> None:
    with _lock:
        loop = _loop
    if loop is None or loop.is_closed():
        return

    async def _drain() -> None:
        from src.database.session import close_db

        await close_db()

    try:
        asyncio.run_coroutine_threadsafe(_drain(), loop).result(timeout=10)
    except Exception:  # pragma: no cover - best-effort teardown
        logger.warning("Async engine teardown failed during worker shutdown")
    finally:
        loop.call_soon_threadsafe(loop.stop)


atexit.register(_shutdown)
