"""Области доступа Google: чтение — read-only, запись — пишущая (docs/development/186 §2.2).

Обоснование: docs/development/186 §2.2 п.4, дефект R-5 (пишущие области на чтении).
Проверяется не список строк, а то, какие области запросит каждый клиент: забыть
`write=True` на пишущем пути безопасно, а пишущая область на чтении — нет.
"""

from __future__ import annotations

import pytest
from app import core


@pytest.fixture()
def scopes(monkeypatch):
    """Перехватывает области, с которыми собирается клиент Google."""
    seen: list[list[str]] = []

    def _credentials(items):
        seen.append(list(items))
        return object()

    monkeypatch.setattr(core, "_credentials", _credentials)
    monkeypatch.setattr("googleapiclient.discovery.build",
                        lambda *_args, **_kwargs: object())
    return seen


def test_sheets_read_клиент_просит_только_чтение(scopes):
    core._sheets_read()
    assert scopes == [[core.READ_SCOPES["sheets"]]]
    assert "spreadsheets.readonly" in scopes[0][0]


def test_calendar_read_клиент_просит_только_чтение(scopes):
    core._calendar()
    assert scopes == [[core.READ_SCOPES["calendar"]]]


def test_drive_клиент_не_качает_содержимое(scopes):
    """Drive читается метаданными: область чтения, не запись."""
    core._drive_read()
    assert scopes == [[core.READ_SCOPES["drive"]]]


def test_пишущие_клиенты_просят_свою_область(scopes):
    core._sheets(write=True)
    core._calendar_write()
    assert scopes == [[core.WRITE_SCOPES["sheets"]], [core.WRITE_SCOPES["calendar"]]]


def test_области_чтения_не_дают_права_записи():
    """Ни одна область чтения не совпадает с пишущей: граница видна по строкам."""
    assert core.READ_SCOPES["sheets"] != core.WRITE_SCOPES["sheets"]
    assert core.READ_SCOPES["calendar"] != core.WRITE_SCOPES["calendar"]
    assert all(value.endswith(".readonly") for value in core.READ_SCOPES.values())
