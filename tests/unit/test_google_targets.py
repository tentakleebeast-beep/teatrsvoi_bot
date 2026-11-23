"""Реестр объектов Google: что можно писать, что только читать (docs/development/186 §2.2).

Обоснование: docs/development/186 §2.2 (реестр объектов), §9 (коды отказа).
Реестр — не список пожеланий, а граница: пишущий вызов возможен только с ключом из
`WRITE_TARGETS`, а боевой календарь и живые листы в нём отсутствуют физически. Тест
проверяет обе стороны: ключ вне реестра отказывает, живой объект не получает ключа
записи даже при желании.
"""

from __future__ import annotations

import pytest
from app import core, google_targets
from app.errors import ApiError


def _code(exc: ApiError) -> str:
    return exc.code


def test_пишущих_объектов_ровно_четыре():
    """В реестре записи — только свои файлы: бюджет, занятость, тестовый лист, черновик."""
    assert sorted(google_targets.WRITE_TARGETS) == [
        "budget.transactions", "calendar.draft", "schedule.test", "schedule.workload"]


def test_живой_календарь_и_живые_листы_не_пишутся():
    """Боевой календарь и живой лист есть только среди источников чтения."""
    for key in ("calendar.read", "schedule.human", "budget.live", "drive.root"):
        assert key not in google_targets.WRITE_TARGETS
        assert key in google_targets.READ_SOURCES


def test_ключ_вне_реестра_отказывает_403(monkeypatch):
    """Писать в объект, которого нет в реестре, нельзя даже с настроенным id."""
    monkeypatch.setattr(core, "CALENDAR_ID_READ", "live-calendar")
    with pytest.raises(ApiError) as caught:
        google_targets.resolve("calendar.read")
    assert caught.value.status_code == 403
    assert _code(caught.value) == "write_target_refused"


def test_пустой_id_отказывает_503(monkeypatch):
    """Объект объявлен, но не настроен: `write_target_unset`, а не «записали молча»."""
    monkeypatch.setattr(core, "BUDGET_SPREADSHEET_ID", "")
    with pytest.raises(ApiError) as caught:
        google_targets.resolve("budget.transactions")
    assert caught.value.status_code == 503
    assert _code(caught.value) == "write_target_unset"


def test_чужой_источник_чтения_неизвестен():
    """Читать «что-нибудь» нельзя: ключ вне `READ_SOURCES` — `unknown_source`."""
    with pytest.raises(ApiError) as caught:
        google_targets.read_id("calendar.live")
    assert caught.value.status_code == 400
    assert _code(caught.value) == "unknown_source"


def test_описание_объекта_одно_на_чтение_и_запись():
    """`schedule.workload` — объект записи и источник чтения одновременно, но роль одна."""
    assert google_targets.target("schedule.workload").layout == "monthly_blocks"
    assert google_targets.target("schedule.test").layout == "hour_matrix"
    assert google_targets.target("budget.transactions").layout == "flat_table"


def test_режим_без_строки_читается_как_переходный():
    """Отсутствие `app_setting.google.mode` — `transitional`, а не падение (186 §2.3)."""

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return None

    assert google_targets.google_mode(_Conn()) == google_targets.MODE_TRANSITIONAL
