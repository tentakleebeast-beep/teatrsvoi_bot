"""Свежесть файла: правка человека после чтения останавливает запись.

Обоснование: docs/development/186 §4.3 шаг 7, §9 (`sheet_changed_since_read`).
Правило простое и потому проверяемое: файл новее последнего чтения — пишем после
перечитывания, а не поверх правки человека. Неизвестное `modifiedTime` свежесть
не ломает: сравнивать нечего, а отказ «на всякий случай» остановил бы dev.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app import sheet_binding
from app.errors import ApiError

BINDING = sheet_binding.Binding(key="budget.transactions", spreadsheet_id="s",
                                sheet_title="Транзакции", layout="flat_table",
                                last_read_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
                                last_edit_time=datetime(2026, 11, 20, 9, 0, tzinfo=UTC))


def test_файл_старше_чтения_свежий():
    assert sheet_binding.stale_reason(datetime(2026, 11, 20, 8, 0, tzinfo=UTC),
                                      BINDING.last_edit_time) is False


def test_файл_новее_чтения_несвежий():
    assert sheet_binding.stale_reason(BINDING.last_edit_time + timedelta(minutes=1),
                                      BINDING.last_edit_time) is True


def test_равные_времена_считаются_свежими():
    """`modifiedTime` равен отметке чтения — файл не менялся после неё."""
    assert sheet_binding.stale_reason(BINDING.last_edit_time, BINDING.last_edit_time) is False


def test_неизвестное_время_не_блокирует_запись():
    assert sheet_binding.stale_reason(None, BINDING.last_edit_time) is False
    assert sheet_binding.stale_reason(datetime(2026, 11, 20, tzinfo=UTC), None) is False


def test_отказ_называется_sheet_changed_since_read():
    with pytest.raises(ApiError) as caught:
        sheet_binding.ensure_fresh(BINDING.last_edit_time + timedelta(minutes=5), BINDING)
    assert caught.value.status_code == 409
    assert caught.value.code == "sheet_changed_since_read"


def test_разбор_времени_google_понимает_z_и_пустое():
    assert sheet_binding.parse_time("2026-11-20T09:00:00.000Z").tzinfo is not None
    assert sheet_binding.parse_time("") is None
    assert sheet_binding.parse_time("не дата") is None


class _Request:
    def __init__(self, answer):
        self.answer = answer

    def execute(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Drive:
    """Drive-клиент: только `files().get(fields="modifiedTime")`."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    def files(self):
        return self

    def get(self, **params):
        self.calls.append(params)
        return _Request(self.answer)


def test_маска_полей_листа_не_просит_modified_time():
    """`properties.modifiedTime` Sheets API не знает: запрос с ним — 400 invalid argument."""
    assert "modifiedTime" not in sheet_binding.SHEET_FIELDS
    assert "properties.title" in sheet_binding.SHEET_FIELDS


def test_время_правки_файла_берётся_у_drive():
    """Свежесть сравнивает `modifiedTime` файла — его отдаёт Drive, а не Sheets."""
    drive = _Drive({"modifiedTime": "2026-11-20T09:00:00.000Z"})
    moment = sheet_binding.file_modified_time("file-id", drive)
    assert moment is not None and moment.tzinfo is not None
    assert drive.calls[0]["fields"] == sheet_binding.DRIVE_FIELDS


def test_сбой_времени_правки_не_ломает_запись():
    """Нет времени — свежесть не блокирует: иначе одна ошибка Drive останавливала бы всё."""
    assert sheet_binding.file_modified_time("file-id", _Drive(OSError("нет"))) is None
