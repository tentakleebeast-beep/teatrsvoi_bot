"""dev-контур не пишет в человеческий лист (docs/reports/84, t_a422101b).

Строка остаётся в БД с пометкой `sheet_error`, `sheet_written_at` не заполняется —
поэтому сверка видит её как «лист не подтвердил», а не как «человек удалил строку».

Запись в лист живёт в домене бюджета (`app/api/budget.py`), а настройки и код
ошибки dev-контура — в ядре (`app/core.py`), поэтому подменяем их по месту.
"""

from __future__ import annotations

from types import SimpleNamespace

from app import core
from app.api import budget


def _body() -> SimpleNamespace:
    return SimpleNamespace(tx_date="2026-10-30", tx_type="expense", amount=1500,
                           category="Реквизит и костюмы", project="", counterparty="",
                           purpose="тест", source_text="", chat_id=1)


def test_dev_env_disables_sheet_write(monkeypatch) -> None:
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "sheet-test")
    monkeypatch.setattr(core, "SHEET_WRITE_ENABLED", False)
    marked: list[tuple[int, str | None]] = []
    monkeypatch.setattr(budget, "_mark_sheet_state", lambda tid, error=None: marked.append((tid, error)))
    called: list[list] = []
    monkeypatch.setattr(budget, "_append_sheet_row", lambda row: called.append(row))

    error = budget._append_tx_sheet(_body(), {"id": 900})

    assert error == core.DEV_SHEET_ERROR
    assert called == []                       # в лист не пошли
    assert marked == [(900, core.DEV_SHEET_ERROR)]  # но пометка в БД осталась


def test_enabled_sheet_write_still_works(monkeypatch) -> None:
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "sheet-test")
    monkeypatch.setattr(core, "SHEET_WRITE_ENABLED", True)
    marked: list[tuple[int, str | None]] = []
    monkeypatch.setattr(budget, "_mark_sheet_state", lambda tid, error=None: marked.append((tid, error)))
    called: list[list] = []
    monkeypatch.setattr(budget, "_append_sheet_row", lambda row: called.append(row))

    error = budget._append_tx_sheet(_body(), {"id": 901})

    assert error is None
    assert len(called) == 1 and called[0][0] == "901"
    assert marked == [(901, None)]


def test_retry_endpoint_refuses_in_dev(monkeypatch) -> None:
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "sheet-test")
    monkeypatch.setattr(core, "SHEET_WRITE_ENABLED", False)
    result = budget.budget_sheet_retry(None, who={"id": 1})
    assert result["ok"] is False
    assert result["error"] == core.DEV_SHEET_ERROR
