"""Хелперы сверки: разбор сумм и дат из человеческого листа (docs/development/16 §4.8)."""

import pathlib
import sys
from datetime import date, datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rec():
    sys.path.insert(0, str(ROOT / "scripts"))
    import reconcile as mod
    yield mod


@pytest.mark.parametrize("raw,expected", [
    ("3 000 ₽", 3000.0),
    (12000, 12000.0),
    ("45 000", 45000.0),
    ("−7 803", -7803.0),
    ("", None),
    (None, None),
])
def test_разбор_сумм(rec, raw, expected):
    assert rec.num(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("26.10.2026", date(2026, 10, 26)),
    ("26.10.26", date(2026, 10, 26)),
    ("2026-10-26", date(2026, 10, 26)),
    (datetime(2026, 10, 26, 12, 0), date(2026, 10, 26)),
])
def test_разбор_дат(rec, raw, expected):
    assert rec.parse_date(raw) == expected


def test_пустая_дата(rec):
    assert rec.parse_date("") is None
    assert rec.parse_date("не дата") is None


def test_лист_не_подтвердил_не_отклоняется(rec):
    """C-3: сбой записи в лист — не повод помечать строку rejected."""
    failed = {"id": 10, "origin": "bot", "sheet_written_at": None, "sheet_error": "quota"}
    deleted = {"id": 11, "origin": "bot", "sheet_written_at": None, "sheet_error": None}
    rejected, unconfirmed = rec.triage_missing([failed, deleted], set())
    assert [r["id"] for r in rejected] == [11]
    assert [r["id"] for r in unconfirmed] == [10]


def test_строка_в_листе_не_трогается(rec):
    """Если строка есть в файле, признак сбоя уже неактуален."""
    row = {"id": 12, "origin": "bot", "sheet_written_at": None, "sheet_error": "quota"}
    rejected, unconfirmed = rec.triage_missing([row], {12})
    assert rejected == [] and unconfirmed == []
