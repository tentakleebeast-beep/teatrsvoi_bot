"""Стенд Mini App отвечает на выпуск персонального кода так же, как сервер.

Обоснование: docs/development/182 §4.3.2 и §4.3.4 (V-116 б, V-117 а) — код
выписывается на выбранного человека: `POST /webapp/admin/invite {participant_id}`,
кандидаты — `GET /webapp/admin/invite-candidates`, новый человек —
`POST /webapp/admin/person-new`. Право — `registration.code.issue`.

Стенд повторяет не только «200 с кодом», но и каждый отказ: стенд, который их не
повторяет, даёт зелёный браузерный тест на поведении, которого на сервере нет.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.webapp import server as stand

TOKEN = {"Authorization": f"Bearer {stand.VALID_TOKEN}"}
TROUPE_ID = 6
SUCCESS_BODY = {"participant_id": TROUPE_ID, "ttl_days": 7}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(stand.app)


@pytest.fixture(autouse=True)
def fresh_invites() -> Iterator[None]:
    """Активные коды и кандидаты стенда — к фикстуре перед каждым тестом.

    Выпуск и отзыв меняют `ADMIN_OVERVIEW["blocks"]["invites"]`, как боевая таблица
    `invite_code`; созданный человек дописывается к кандидатам (карта t_806a633e,
    docs/development/182 §4.3.2).
    """
    stand.reset_invites()
    yield
    stand.reset_invites()


def test_стенд_выпускает_код_на_выбранного_человека(client) -> None:
    """Успех в боевой форме и отказы пути выпуска (`{participant_id}`)."""
    ok = client.post("/webapp/admin/invite", json=SUCCESS_BODY, headers=TOKEN)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert set(body) == {"ok", "code", "role", "participant_id", "display_name",
                         "expires_at"}, body
    assert body["ok"] is True
    assert body["code"] == "PED7AGK2"
    assert body["participant_id"] == TROUPE_ID
    assert body["display_name"] == "Сидоров Пётр"
    assert body["role"] == "troupe"
    assert body["expires_at"] == "2026-11-26T12:00:00+04:00"
    # Код в алфавите `core.CODE_ALPHABET`: без похожих знаков (0/O, 1/I).
    assert len(body["code"]) == 8
    assert set(body["code"]) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")

    # Роль берётся из записи, а не из тела: `role` в запросе ничего не меняет.
    master = client.post("/webapp/admin/invite", headers=TOKEN,
                         json={"participant_id": 5, "role": "admin"})
    assert master.status_code == 200
    assert master.json()["role"] == "master"

    # Неизвестный человек — боевой 404.
    missing = client.post("/webapp/admin/invite", json={"participant_id": 999}, headers=TOKEN)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "participant_not_found"

    # У организатора нет `registration.code.issue` (`db/init/008`) — 403 словами права.
    org = client.post("/webapp/admin/invite", json=SUCCESS_BODY,
                      headers={**TOKEN, "X-SVOI-Role": "org"})
    assert org.status_code == 403
    assert org.json()["detail"] == "capability required: registration.code.issue"

    # Без токена — тот же отказ, что у остальных маршрутов стенда.
    anonymous = client.post("/webapp/admin/invite", json=SUCCESS_BODY)
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "token_expired"


def test_стенд_заводит_человека_и_отдаёт_кандидатов(client) -> None:
    """`person-new` и `invite-candidates` повторяют правила `api/admin.py`."""
    found = client.get("/webapp/admin/invite-candidates", headers=TOKEN)
    assert found.status_code == 200, found.text
    names = [row["display_name"] for row in found.json()["data"]]
    assert "Сидоров Пётр" in names
    assert set(found.json()["data"][0]) == {"id", "display_name", "role"}

    search = client.get("/webapp/admin/invite-candidates?q=кузнец", headers=TOKEN)
    assert [row["display_name"] for row in search.json()["data"]] == ["Кузнецова Мария"]

    created = client.post("/webapp/admin/person-new", headers=TOKEN,
                          json={"display_name": "Петров Иван", "role": "troupe"})
    assert created.status_code == 200, created.text
    person = created.json()["participant"]
    assert person["display_name"] == "Петров Иван"
    assert person["role"] == "troupe"
    after = client.get("/webapp/admin/invite-candidates?q=петров", headers=TOKEN)
    assert [row["id"] for row in after.json()["data"]] == [person["id"]]

    one_word = client.post("/webapp/admin/person-new", headers=TOKEN,
                           json={"display_name": "Иван"})
    assert one_word.status_code == 400
    assert one_word.json()["code"] == "need_full_name"

    duplicate = client.post("/webapp/admin/person-new", headers=TOKEN,
                            json={"display_name": "Петров Иван"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "person_key_taken"

    admin = client.post("/webapp/admin/person-new", headers=TOKEN,
                        json={"display_name": "Главный Админ", "role": "admin"})
    assert admin.status_code == 422, "роль admin кодом и экраном не создаётся"


def test_блок_приглашений_панели_отдаётся_списком(client) -> None:
    """Форма блока как в бою: `blocks.invites` — список кодов, не `{"data": [...]}`.

    Бой отдаёт `blocks["invites"] = list_invites(who=viewer)["data"]`; экран читает
    `(BLOCKS.invites || []).length` и `forEach`. Пока стенд отдавал обёртку, длина
    была `undefined`, ветка `!invites.length` всегда истинна, и «Активные коды»
    пустели даже с кодами в ответе (карта t_806a633e). Строка списка теперь несёт
    имя человека: экран печатает, на кого выписан код (§4.3.2).
    """
    overview = client.get("/webapp/admin/overview", headers=TOKEN)
    assert overview.status_code == 200, overview.text
    invites = overview.json()["blocks"]["invites"]
    assert isinstance(invites, list), f"стенд отдал {type(invites).__name__}: {invites!r}"
    assert invites, "список кодов фикстуры пуст — строку рисовать не на чем"
    assert set(invites[0]) == {"code", "role", "participant_id", "display_name",
                              "created_at", "expires_at", "max_uses", "uses"}, invites[0]
    assert invites[0]["role"] == "master", invites[0]
    assert invites[0]["display_name"] == "Кузнецова Мария"


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
