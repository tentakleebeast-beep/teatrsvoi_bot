"""Ш-49 (V-010): раздел «Справка» — тексты по ролям и право howto.read."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}


def _grant_howto(chat_id: int = 123456789) -> None:
    """В тестовой базе админ-чат — организатор; howto.read выдаём точечно, как в бою админу."""
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            """insert into participant_capability_override
                   (participant_id, capability, allowed, reason)
               select id, 'howto.read', true, 'тест Ш-49'
                 from participant where tg_chat_id = %s
               on conflict (participant_id, capability) do update set allowed = true""",
            (chat_id,))
        conn.commit()


def _as(chat_id: int) -> dict:
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def test_Ш49_админ_видит_справку_всех_ролей(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364913, "Админ Справка", "admin")
    body = client.get("/tools/howto", headers=_as(250364913)).json()
    assert body["ok"] is True
    roles = [section["role"] for section in body["sections"]]
    assert roles == ["admin", "org", "troupe"]
    for section in body["sections"]:
        assert section["title"]
        assert section["text"].startswith("# Справка:")
        # Справка не учит печатать команды: действия — в меню и админ-панели
        assert "/grant" not in section["text"] and "/invite" not in section["text"]


def test_Ш49_чужой_роли_текст_не_отдаётся(client):
    """Класс: право есть, но чужие роли не показываем — только своя инструкция."""
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364911, "Орг Справка", "org")
    _grant_howto(250364911)
    body = client.get("/tools/howto", headers=_as(250364911)).json()
    assert [section["role"] for section in body["sections"]] == ["org"]


def test_Ш49_без_права_отказ(client):
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 250364912, "Труппа Справка")
    response = client.get("/tools/howto", headers=_as(250364912))
    assert response.status_code == 403
    assert "howto.read" in response.text


def test_Ш49_пустой_файл_не_даёт_пустой_раздел(client, tmp_path, monkeypatch):
    """Класс дефекта: отсутствующий файл не должен превращаться в раздел без текста."""
    from app import core as api_main

    _grant_howto()
    monkeypatch.setattr(api_main, "HOWTO_DIR", tmp_path)
    body = client.get("/tools/howto", headers=HEADERS).json()
    assert body["ok"] is True
    assert body["sections"] == []
