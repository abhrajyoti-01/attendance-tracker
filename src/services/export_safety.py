"""Spreadsheet export helpers.

User-controlled text (names, external IDs, device IDs) ends up in CSV/XLSX
files that are opened in Excel/Sheets. Leading ``=``, ``+``, ``-``, ``@``,
tab or CR makes the cell a formula, so a malicious display name can execute
a DDE/URL payload on the reviewer's machine. Neutralize them.
"""

import re

from src.services.audit import redact_pii  # noqa: F401  (re-export for convenience)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_cell(value: object) -> str:
    """Return a spreadsheet-safe string for a single cell value."""
    if value is None:
        return ""
    text = str(value)
    text = _CONTROL_CHARS.sub("", text)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def sanitize_row(row: dict) -> dict:
    return {key: sanitize_cell(value) for key, value in row.items()}
