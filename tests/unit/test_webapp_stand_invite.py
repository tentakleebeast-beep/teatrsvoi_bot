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

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.webapp import server as stand

TOKEN = {"Authorization": f"Bearer {stand.VALID_TOKEN}"}
SUCCESS_BODY = {"role": "master", "ttl_days": 7, "max_uses": 1}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(stand.app)


@pytest.fixture(autouse=True)
def fresh_invites() -> Iterator[None]:
    """Активные коды стенда — к фикстуре перед каждым тестом.

    Выпуск и отзыв меняют `ADMIN_OVERVIEW["blocks"]["invites"]`, как боевая таблица
    `invite_code`; без возврата код одного теста менял бы список в другом (карта
    t_806a633e).
    """
    stand.reset_invites()
    yield
    stand.reset_invites()


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


def test_блок_приглашений_панели_отдаётся_списком(client) -> None:
    """Форма блока как в бою: `blocks.invites` — список кодов, не `{"data": [...]}`.

    Бой отдаёт `blocks["invites"] = list_invites(who=viewer)["data"]`
    (`api/admin.py:1019-1020`) — голый список, а экран читает
    `(BLOCKS.invites || []).length` и `forEach` (`admin_invites.py:122`). Пока
    стенд отдавал обёртку, длина была `undefined`, ветка `!invites.length` всегда
    истинна, и «Активные коды» пустели даже с кодами в ответе (карта t_806a633e).
    Фикстура не пуста: строка списка и слово «педагог» проверяются и без выпуска.
    """
    overview = client.get("/webapp/admin/overview", headers=TOKEN)
    assert overview.status_code == 200, overview.text
    invites = overview.json()["blocks"]["invites"]
    assert isinstance(invites, list), f"стенд отдал {type(invites).__name__}: {invites!r}"
    assert invites, "список кодов фикстуры пуст — строку рисовать не на чем"
    assert set(invites[0]) == {"code", "role", "created_at", "expires_at",
                              "max_uses", "uses"}, invites[0]
    assert invites[0]["role"] == "master", invites[0]


def test_выпуск_и_отзыв_меняют_список_кодов(client) -> None:
    """Выпуск дописывает код в блок, отзыв убирает — как боевая таблица `invite_code`.

    Без этого экран после выпуска звал `refresh()`, получал прежний список и
    строки кода не появлялось вовсе; кнопка «Отозвать» получала 404 (карта
    t_806a633e).
    """
    def codes() -> list[str]:
        overview = client.get("/webapp/admin/overview", headers=TOKEN)
        return [item["code"] for item in overview.json()["blocks"]["invites"]]

    seed = stand.INVITE_SAMPLES[0]["code"]
    assert codes() == [seed]

    issued = client.post("/webapp/admin/invite", json=SUCCESS_BODY, headers=TOKEN)
    assert issued.status_code == 200, issued.text
    assert codes() == [stand.INVITE_CODE, seed]

    # Второй выпуск не дублирует строку: код на стенде один и тот же.
    client.post("/webapp/admin/invite", json=SUCCESS_BODY, headers=TOKEN)
    assert codes() == [stand.INVITE_CODE, seed]

    revoked = client.post("/webapp/admin/invite-revoke", headers=TOKEN,
                          json={"code": stand.INVITE_CODE})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["code"] == stand.INVITE_CODE
    assert codes() == [seed]

    # Повторный отзыв — боевой 404 `invite_not_found_or_used`.
    again = client.post("/webapp/admin/invite-revoke", headers=TOKEN,
                        json={"code": stand.INVITE_CODE})
    assert again.status_code == 404
    assert again.json()["detail"] == "invite_not_found_or_used"

    # У организатора нет `registration.code.issue` — отказ словами права.
    org = client.post("/webapp/admin/invite-revoke", headers={**TOKEN, "X-SVOI-Role": "org"},
                      json={"code": seed})
    assert org.status_code == 403
    assert org.json()["detail"] == "capability required: registration.code.issue"
