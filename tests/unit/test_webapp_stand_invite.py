"""Стенд Mini App отвечает на выпуск инвайт-кода так же, как сервер.

Обоснование: `docs/development/176` §9.9 и AC-11 (карта `t_ff6144fc`), контракт —
`services/tool-api/app/api/admin.py:29-58`: роль `Literal["admin", "org", "troupe", "master"]`,
отказ `400` на `admin`, `400` на дисциплину у не-педагога, право
`registration.code.issue` для выпуска.

До этой карты стенд на `POST /webapp/admin/invite` не отвечал вовсе, поэтому
браузерный сценарий выпуска получал 404 и лист не открывался. Здесь проверяется
не только «200 с кодом», но и каждый отказ: стенд, который их не повторяет,
даёт зелёный браузерный тест на поведении, которого на сервере нет.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.webapp import server as stand

TOKEN = {"Authorization": f"Bearer {stand.VALID_TOKEN}"}
SUCCESS_BODY = {"role": "master", "ttl_days": 7, "max_uses": 1}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(stand.app)


def test_стенд_выпускает_код_и_повторяет_отказы(client) -> None:
    """AC-11: успех в боевой форме и все отказы пути выпуска.

    Форма ответа — `{ok, code, role, expires_at}`: экран читает только `code`, но
    в бою в ответе есть и роль, и срок, и стенд, который их не отдаёт, скрыл бы
    расхождение контракта.
    """
    ok = client.post("/webapp/admin/invite", json=SUCCESS_BODY, headers=TOKEN)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert set(body) == {"ok", "code", "role", "expires_at"}, body
    assert body["ok"] is True
    assert body["code"] == "PED7AGK2"
    assert body["role"] == "master"
    assert body["expires_at"] == "2026-11-26T12:00:00+04:00"
    # Код в алфавите `core.CODE_ALPHABET`: без похожих знаков (0/O, 1/I).
    assert len(body["code"]) == 8
    assert set(body["code"]) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")

    # Роль по умолчанию — труппа: экран отправляет её, пока сегмент не нажали.
    troupe = client.post("/webapp/admin/invite", headers=TOKEN,
                         json={"role": "troupe", "ttl_days": 7, "max_uses": 1})
    assert troupe.status_code == 200
    assert troupe.json()["role"] == "troupe"

    admin = client.post("/webapp/admin/invite", json={"role": "admin"}, headers=TOKEN)
    assert admin.status_code == 400
    assert "ADMIN_CHAT_ID" in admin.json()["detail"]

    unknown = client.post("/webapp/admin/invite", json={"role": "owner"}, headers=TOKEN)
    assert unknown.status_code == 422, unknown.text

    discipline = client.post("/webapp/admin/invite", headers=TOKEN,
                             json={"role": "troupe", "discipline_code": "acting"})
    assert discipline.status_code == 400
    assert "discipline_code" in discipline.json()["detail"]

    # У организатора нет `registration.code.issue` (`db/init/008`) — 403 словами права.
    org = client.post("/webapp/admin/invite", json=SUCCESS_BODY,
                      headers={**TOKEN, "X-SVOI-Role": "org"})
    assert org.status_code == 403
    assert org.json()["detail"] == "capability required: registration.code.issue"

    # Без токена — тот же отказ, что у остальных маршрутов стенда.
    anonymous = client.post("/webapp/admin/invite", json=SUCCESS_BODY)
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "token_expired"
