"""Профиль: имя, короткие имена, время в пути, манифест для AI.

Обоснование (Ш-51): файл создан в d1815e8 feat: обратная связь, полный профиль, каналы; fix: 14 дефектов второго смоука."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn


def _as(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def test_сохранение_всех_полей(client):
    chat = 250364950
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, chat, "Профиль Полный")

    saved = client.post("/tools/profile/me", headers=_as(chat), json={
        "display_name": "Новое Имя",
        "aliases": ["Короткое", "Второе"],
        "travel_minutes": 25,
        "ai_manifest": "Говори кратко, без уменьшительных имён",
    }).json()
    assert saved["ok"] is True
    assert saved["display_name"] == "Новое Имя"

    body = client.get("/tools/profile/me", headers=_as(chat)).json()
    assert body["name"] == "Новое Имя"
    assert sorted(body["aliases"]) == ["Второе", "Короткое"]
    assert body["travel_minutes"] == 25
    assert "кратко" in body["ai_manifest"]


def test_псевдонимы_заменяются_целиком(client):
    chat = 250364951
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, chat, "Профиль Замена")

    client.post("/tools/profile/me", headers=_as(chat), json={"aliases": ["Первый", "Второй"]})
    client.post("/tools/profile/me", headers=_as(chat), json={"aliases": ["Третий"]})

    with psycopg.connect(_test_dsn()) as conn:
        rows = [row[0] for row in conn.execute(
            "select alias from person_alias where participant_id = %s "
            "and alias_kind = 'short' order by alias", (pid,)).fetchall()]
    assert rows == ["Третий"]


def test_слишком_много_псевдонимов_отклоняется(client):
    chat = 250364952
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, chat, "Профиль Много")
    response = client.post("/tools/profile/me", headers=_as(chat),
                           json={"aliases": ["аа1", "аа2", "аа3", "аа4", "аа5", "аа6", "аа7"]})
    assert response.status_code == 400


def test_короткое_имя_отклоняется(client):
    chat = 250364953
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, chat, "Профиль Пусто")
    assert client.post("/tools/profile/me", headers=_as(chat),
                       json={"display_name": "Я"}).status_code == 422


def test_дорога_вне_диапазона_отклоняется(client):
    chat = 250364954
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, chat, "Профиль Дорога")
    assert client.post("/tools/profile/me", headers=_as(chat),
                       json={"travel_minutes": 999}).status_code == 422
