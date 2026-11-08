"""Анкета участника в Mini App: авторизация и сохранение (docs/development/43)."""

import json
import time
from urllib.parse import urlencode

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn

BOT_TOKEN = "123456:TEST"


def _api():
    """Модуль приложения импортируем ПОСЛЕ фикстуры: она выставляет DATABASE_URL."""
    import app.core as core
    import app.main as module

    # Настройки живут в app.core (этап B): подпись сервер проверяет тем же токеном
    core.BOT_TOKEN = BOT_TOKEN
    return module


def init_data(chat_id: int) -> str:
    """Подписанный initData так, как его пришлёт Telegram."""
    import hashlib
    import hmac as hmaclib

    data = {"auth_date": str(int(time.time())), "query_id": "q1",
            "user": json.dumps({"id": chat_id, "first_name": "Тест"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmaclib.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmaclib.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def test_авторизация_и_анкета(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 770100, "Анкета Тест")

    auth = client.post("/webapp/auth", json={"init_data": init_data(770100)}).json()
    assert auth["ok"] is True and auth["token"]
    headers = {"Authorization": f"Bearer {auth['token']}"}

    profile = client.get("/webapp/profile", headers=headers).json()
    assert profile["profile"]["id"] == pid
    assert profile["profile"]["travel_minutes"] == 60
    assert profile["projects"] is not None


def test_сохранение_времени_в_пути(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 770101, "Дорога Тест")
    token = client.post("/webapp/auth", json={"init_data": init_data(770101)}).json()["token"]

    saved = client.post("/webapp/profile", headers={"Authorization": f"Bearer {token}"},
                        json={"travel_minutes": 15}).json()
    assert saved["ok"] is True and saved["travel_minutes"] == 15

    with psycopg.connect(_test_dsn()) as conn:
        value = conn.execute("select travel_minutes from participant "
                             "where tg_chat_id = 770101").fetchone()[0]
    assert value == 15


def test_роли_сохраняются_и_заменяются(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 770102, "Роли Тест")
        project = conn.execute(
            "insert into project (name, kind, status) values ('Анкета Спектакль','show','active') "
            "returning id").fetchone()[0]
        conn.commit()
    token = client.post("/webapp/auth", json={"init_data": init_data(770102)}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/webapp/profile", headers=headers,
                        json={"roles": [{"project_id": project, "alias": "Наташа"},
                                        {"project_id": project, "alias": "Стражник"}]}).json()
    assert len(first["roles"]) == 2, "несколько ролей в одном спектакле допустимы"

    second = client.post("/webapp/profile", headers=headers,
                         json={"roles": [{"project_id": project, "alias": "Наташа"}]}).json()
    assert len(second["roles"]) == 1, "список ролей заменяется целиком"


def test_без_токена_нет_доступа(client):
    _api()
    assert client.get("/webapp/profile").status_code == 401
    assert client.get("/webapp/profile",
                      headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_незарегистрированный_не_получает_токен(client):
    _api()
    r = client.post("/webapp/auth", json={"init_data": init_data(777777)})
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "not_registered"
    assert body["detail"] and "зарегистрируйтесь" in body["detail"]


def test_плохая_подпись_не_даёт_токен(client):
    _api()
    bad = init_data(770103).replace("hash=", "hash=00")
    r = client.post("/webapp/auth", json={"init_data": bad})
    assert r.status_code == 401


def test_время_в_пути_проверяется(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 770104, "Предел Тест")
    token = client.post("/webapp/auth", json={"init_data": init_data(770104)}).json()["token"]
    r = client.post("/webapp/profile", headers={"Authorization": f"Bearer {token}"},
                    json={"travel_minutes": 500})
    assert r.status_code == 400
