"""Shared pytest fixtures.

Tests run against SQLite (aiosqlite) with FastAPI's dependency overrides, so no
Postgres/Redis/model files are required. ML-dependent tests are marked and skip
when the ONNX model or torch is unavailable.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-value-that-is-at-least-32-characters")
# The rate limiter is keyed by client IP; every test shares 127.0.0.1 and would
# exhaust the 10/min auth budget partway through the session.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def onnx_model_path() -> Path:
    return BASE_DIR / "models" / "exported" / "embedding_net.onnx"


@pytest.fixture(scope="session")
def has_onnx_model(onnx_model_path: Path) -> bool:
    return onnx_model_path.exists() and onnx_model_path.stat().st_size > 0
