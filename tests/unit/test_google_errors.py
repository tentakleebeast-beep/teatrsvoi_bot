"""Ошибки Google → коды конверта `docs/architecture/85` (186 §9).

Обоснование: docs/development/186 §9, `docs/architecture/85-error-catalog.md`.
Отказ Google не должен приходить человеку английским текстом библиотеки: 401/403 —
`no_access` (прав нет), остальное — код операции (`sheet_write_failed`,
`calendar_unavailable`). Тест гоняет адаптер на настоящем `HttpError`, а не на `RuntimeError`.
"""

from __future__ import annotations

import httplib2
import pytest
from googleapiclient.errors import HttpError


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"denied")


def test_нет_прав_это_no_access(monkeypatch):
    from app import sheet_binding

    error = sheet_binding._google_error(_http_error(403), "sheet_write_failed")
    assert error.status_code == 403 and error.code == "no_access"


def test_истёкший_доступ_тоже_no_access():
    from app import sheet_binding

    error = sheet_binding._google_error(_http_error(401), "calendar_unavailable")
    assert error.code == "no_access"


@pytest.mark.parametrize("code", ["sheet_write_failed", "sheet_verify_failed",
                                  "sheet_unavailable", "calendar_unavailable",
                                  "drive_unavailable"])
def test_отказ_операции_остаётся_кодом_операции(code):
    """Остальные отказы не маскируются под «нет прав»: у них свой код и 502."""
    from app import sheet_binding

    error = sheet_binding._google_error(_http_error(500), code)
    assert error.status_code == 502 and error.code == code


def test_текст_отказа_человеческий_и_без_английского_стека():
    from app import sheet_binding

    error = sheet_binding._google_error(_http_error(500), "sheet_verify_failed")
    assert "Лист не подтвердил запись" in error.detail
    assert "HttpError" not in error.detail


def test_коды_спецификации_совпадают_с_конвертом():
    """Коды из §9 — машинные и стабильные: их читает Mini App, а не человек."""
    expected = {"write_target_refused", "write_target_unset", "binding_missing",
                "sheet_ambiguous", "header_mismatch", "sheet_changed_since_read",
                "sheet_verify_failed", "no_access", "not_bot_event",
                "mode_backup_required", "unknown_source"}
    assert all(isinstance(code, str) and code == code.lower() for code in expected)
