"""Режим приложения: `transitional`/`target`, выгрузка как условие перехода (186 §2.3).

Обоснование: docs/development/186 §2.3, §5.1; дизайн 185 D7.
Переход в `target` без выгрузки запрещён: выгрузку делает человек, и единственное, что
бот может проверить, — наличие ссылки. Успех пишет аудит `google.mode.set`; отсутствие
строки в `app_setting` читается как `transitional`, а не как падение.
"""

from __future__ import annotations

import pytest
from app import google_targets
from app.errors import ApiError


class FakeConn:
    """Соединение без БД: помнит выполненные запросы и текущую строку режима."""

    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or ()))
        if sql.strip().lower().startswith("select value"):
            return self
        if sql.strip().lower().startswith("insert into app_setting"):
            self.value = (params or (None,))[0]
        return self

    def fetchone(self):
        sql = self.calls[-1][0].lower() if self.calls else ""
        if sql.startswith("select hash"):
            return None
        return {"value": self.value} if self.value is not None else None

    def audit_actions(self) -> list[str]:
        return [params[3] for sql, params in self.calls
                if sql.startswith("insert into audit_event") and len(params) > 3]


def test_по_умолчанию_переходный_режим():
    assert google_targets.google_mode(FakeConn()) == google_targets.MODE_TRANSITIONAL


def test_режим_читается_из_настроек():
    assert google_targets.google_mode(FakeConn("target")) == google_targets.MODE_TARGET


def test_мусор_в_настройке_не_включает_target():
    """Неизвестное значение — переходный режим: расширять набор молча нельзя."""
    assert google_targets.google_mode(FakeConn("target!")) == google_targets.MODE_TRANSITIONAL


def test_переход_в_target_требует_выгрузку():
    with pytest.raises(ApiError) as caught:
        google_targets.set_google_mode(FakeConn(), "target", 1, backup="  ")
    assert caught.value.status_code == 400
    assert caught.value.code == "mode_backup_required"


def test_переход_в_target_с_выгрузкой_пишет_аудит():
    conn = FakeConn()
    result = google_targets.set_google_mode(
        conn, "target", 1, backup="https://drive.example/export.zip")
    assert result == {"mode": "target", "backup": "https://drive.example/export.zip"}
    assert conn.value == "target"
    assert "google.mode.set" in conn.audit_actions()


def test_неизвестный_режим_это_400():
    with pytest.raises(ApiError) as caught:
        google_targets.set_google_mode(FakeConn(), "прод", 1)
    assert caught.value.status_code == 400
    assert caught.value.code == "mode_unknown"


def test_возврат_в_переходный_не_требует_выгрузки():
    """Выгрузка нужна только для `target`: обратный переход — просто переключение."""
    conn = FakeConn("target")
    assert google_targets.set_google_mode(conn, "transitional", 1)["mode"] == "transitional"
    assert "google.mode.set" in conn.audit_actions()
