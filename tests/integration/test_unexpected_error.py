"""Программная ошибка в адаптере — это 500 с `request_id`, а не `ok:false`.

R-5 (docs/reports/79): широкий `except` в обработчике скрывал поломку кода под
видом «внешний сервис недоступен». Здесь проверяем обратное: отказ Google
(`HttpError`) даёт понятный `ok:false`, а опечатка в имени поля — 500 из общего
конверта, по которому в логе находится трейсбек.
"""

from __future__ import annotations

from tests.conftest import google_error


def _calendar_ids(monkeypatch, read: str = "test-calendar-id") -> None:
    from app import core as api

    monkeypatch.setattr(api, "CALENDAR_ID_READ", read)
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "")


def test_отказ_google_даёт_ok_false(client, auth, monkeypatch):
    """Ожидаемый отказ внешнего сервиса остаётся частичным успехом, не 500."""
    from app import core as api

    _calendar_ids(monkeypatch)

    def broken(*args, **kwargs):
        raise google_error()

    monkeypatch.setattr(api, "_calendar", broken)
    response = client.get("/tools/calendar/events?days=7", headers=auth)
    assert response.status_code == 200
    assert response.json()["error"] == "calendar_unavailable"


def test_опечатка_в_имени_поля_даёт_500_с_request_id(client, auth, monkeypatch):
    """Опечатка в коде адаптера — поломка кода: 500, код internal_error, request_id.

    Клиент без `raise_server_exceptions`: общий обработчик формирует ответ, но
    TestClient по умолчанию пробрасывает исключение наружу — здесь нужен именно
    ответ, который увидит гейтвей.
    """
    from app import core as api
    from app.main import app
    from fastapi.testclient import TestClient

    _calendar_ids(monkeypatch)

    def broken(*args, **kwargs):
        # так выглядит опечатка: атрибута `eventz` у сервиса нет
        raise AttributeError("'Resource' object has no attribute 'eventz'")

    monkeypatch.setattr(api, "_calendar", broken)
    probe = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    response = probe.get("/tools/calendar/events?days=7", headers=auth)

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["status"] == 500
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert body["request_id"]
    # внутренний текст ошибки клиенту не показываем — он уходит в лог по request_id
    assert "eventz" not in response.text
