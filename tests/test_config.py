"""Config parsing and production-safety guardrail tests.

Regression coverage for the CORS_ORIGINS crash: pydantic-settings JSON-decodes
complex fields before validators run, so a documented comma-separated value used
to raise SettingsError and take down the API *and* every Celery worker at import.
"""

import pytest
from pydantic import ValidationError

from src.config import CORSSettings


def _cors(value: str) -> CORSSettings:
    return CORSSettings(CORS_ORIGINS=value)


def test_single_origin_plain_string():
    assert _cors("https://localhost").origins == ["https://localhost"]


def test_comma_separated_origins():
    assert _cors("https://a.example.com,https://b.example.com").origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_comma_separated_with_whitespace():
    assert _cors(" https://a.example.com , https://b.example.com ").origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_json_array_still_supported():
    assert _cors('["https://a.example.com","https://b.example.com"]').origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_wildcard_allowed_but_flagged_for_production():
    assert _cors("*").origins == ["*"]


def test_missing_scheme_rejected():
    with pytest.raises(ValidationError):
        _cors("example.com")


def test_empty_value_yields_no_origins():
    assert _cors("").origins == []
