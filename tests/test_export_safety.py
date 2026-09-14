"""Spreadsheet export safety tests (CSV/Excel formula injection)."""

from src.services.export_safety import sanitize_cell, sanitize_row


def test_formula_prefixes_are_neutralized():
    for payload in ("=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(A1)"):
        result = sanitize_cell(payload)
        assert result.startswith("'")
        assert result[1:] == payload


def test_plain_text_unchanged():
    assert sanitize_cell("Ada Lovelace") == "Ada Lovelace"
    assert sanitize_cell("user-123") == "user-123"


def test_none_becomes_empty_string():
    assert sanitize_cell(None) == ""


def test_control_characters_stripped():
    assert sanitize_cell("bad\x00name\x07") == "badname"


def test_tab_and_cr_prefix_neutralized():
    assert sanitize_cell("\tleading") == "'\tleading"
    assert sanitize_cell("\rleading") == "'\rleading"


def test_sanitize_row_applies_to_all_values():
    row = {"name": "=HACK()", "external_id": "ok", "count": 12}
    assert sanitize_row(row) == {
        "name": "'=HACK()",
        "external_id": "ok",
        "count": "12",
    }
