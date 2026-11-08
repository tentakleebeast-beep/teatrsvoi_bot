"""Карточка участника в админ-панели (Ш-50, обращение №25).

Право `participants.view` открывает карточку в чате и в Mini App, без права — 403.
В карточке: имя и короткие имена, роли и возможности, время в пути, телефон
и день рождения, тариф, участие в показах, последние обращения. Только чтение.
"""

from __future__ import annotations

import psycopg

from tests.conftest import ADMIN_CHAT, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _seed_card_person() -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        pid = conn.execute(
            """insert into participant
               (tg_chat_id, display_name, person_key, role, status, phone, birth_date,
                travel_minutes, plays, roles_note)
               values (780001, 'Карточка Тестова', 'тестова', 'troupe', 'active',
                       '+79990000001', '1990-05-17', 35, 'Чайка: Нина', 'героиня')
               returning id""").fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) "
                     "values (%s, 'troupe')", (pid,))
        conn.execute("insert into person_alias (participant_id, alias, alias_kind) "
                     "values (%s, 'Тестова', 'short')", (pid,))
        conn.execute(
            """insert into event_plan (title, kind, starts_at, ends_at, participant_ids, status)
               values ('Чайка', 'show', now() - interval '3 days',
                       now() - interval '3 days' + interval '2 hours', array[%s], 'done')""",
            (pid,))
        conn.execute("insert into feedback (participant_id, kind, text, status) "
                     "values (%s, 'wish', 'Хочу больше репетиций', 'new')", (pid,))
    return pid


def test_карточка_отдаёт_профиль_тариф_показы_и_обращения(client, auth):
    pid = _seed_card_person()
    r = client.get(f"/tools/participants/{pid}/card", headers=auth)
    assert r.status_code == 200, r.text
    card = r.json()["data"]
    assert card["display_name"] == "Карточка Тестова"
    assert [a["alias"] for a in card["aliases"]] == ["Тестова"]
    assert "troupe" in card["roles"]
    assert "capabilities" in card
    assert card["travel_minutes"] == 35
    assert card["phone"] == "+79990000001"
    assert card["birthday"] == "1990-05-17"
    assert card["tariff"]["tariff"] is None
    assert [s["title"] for s in card["shows"]] == ["Чайка"]
    assert [f["id"] for f in card["feedback"]]
    assert card["plays"] == "Чайка: Нина"


def test_карточка_требует_права_participants_view(client):
    pid = _seed_card_person()
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (780002, 'Без Прав', 'troupe', 'active')""")
        conn.execute("insert into participant_role (participant_id, role) "
                     "select id, 'troupe' from participant where tg_chat_id = 780002")
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "780002"}
    r = client.get(f"/tools/participants/{pid}/card", headers=headers)
    assert r.status_code == 403
    assert "participants.view" in r.json()["detail"]


def test_карточка_неизвестного_участника_404(client, auth):
    r = client.get("/tools/participants/999999/card", headers=auth)
    assert r.status_code == 404
    assert r.json()["detail"] == "participant_not_found"


def test_miniapp_карточка_тем_же_правом(client):
    _api()
    pid = _seed_card_person()
    token = client.post("/webapp/auth",
                        json={"init_data": init_data(ADMIN_CHAT)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = client.post("/webapp/admin/person", json={"participant_id": pid}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["display_name"] == "Карточка Тестова"


def test_miniapp_карточка_без_права_закрыта(client):
    _api()
    pid = _seed_card_person()
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (780003, 'Mini Без Прав', 'troupe', 'active')""")
        conn.execute("insert into participant_role (participant_id, role) "
                     "select id, 'troupe' from participant where tg_chat_id = 780003")
    token = client.post("/webapp/auth",
                        json={"init_data": init_data(780003)}).json()["token"]
    r = client.post("/webapp/admin/person", json={"participant_id": pid},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert "participants.view" in r.json()["detail"]
