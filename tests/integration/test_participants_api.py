"""UC-06: участники и заявки — список и права (docs/development/28)."""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn


def _seed_people() -> None:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (700001, 'Активный Студиец', 'troupe', 'active')""")
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (700002, 'Ушедший Студиец', 'troupe', 'archived')""")
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (700003, 'Ожидающий', 'troupe', 'pending')""")


def test_список_без_архивных(client, auth):
    _seed_people()
    r = client.get("/tools/participants", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    names = [p["display_name"] for p in body["data"]]
    assert "Активный Студиец" in names
    assert "Ушедший Студиец" not in names
    # pending не архивный — виден в списке
    assert "Ожидающий" in names


def test_include_archived_показывает_всех(client, auth):
    _seed_people()
    body = client.get("/tools/participants?include_archived=true", headers=auth).json()
    names = [p["display_name"] for p in body["data"]]
    assert "Ушедший Студиец" in names
    assert "Активный Студиец" in names


def test_карточка_участника_содержит_роли_и_telegram(client, auth):
    _seed_people()
    body = client.get("/tools/participants", headers=auth).json()
    card = next(p for p in body["data"] if p["display_name"] == "Активный Студиец")
    assert card["chat_id"] == 700001
    assert card["role"] == "troupe"
    assert "troupe" in card["roles"]
    assert "capabilities" in card


def test_нет_права_participants_view(client, auth):
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (700004, 'Без Прав', 'troupe', 'active')""")
        conn.execute(
            """insert into participant_role (participant_id, role)
               select id, 'troupe' from participant where tg_chat_id = 700004""")
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "700004"}
    r = client.get("/tools/participants", headers=headers)
    assert r.status_code == 403
    assert "participants.view" in r.json()["detail"]


def test_чужой_участник_без_записи_отклоняется(client, auth):
    headers = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123123123"}
    r = client.get("/tools/participants", headers=headers)
    assert r.status_code == 403
