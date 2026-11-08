"""Служебные файловые маршруты: вход по сервисному токену, а не по сессии (AUD-8).

Обоснование: docs/reports/130-orphan-review.md, docs/architecture/25 §9.2 п.7 и §9.4 Х3.
`GET /admin/files/storage` и `POST /admin/files/cold-rotate` зовёт таймер и мониторинг,
а не человек; до карты AUD-8 снимок хранилища из-за приоритета `&` в проверке ролей
(`set(roles) | {role} & {"admin","org"}`) открывался любому активному участнику.
Здесь проверяется, что роль больше не заменяет токен: ни участник, ни админ с сессией
не проходят, а без заголовка приходит 401 — то есть переведённый маршрут действительно
закрыт, а не просто «тест на 401» рядом со сломанной проверкой.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn

TOKEN = {"Authorization": "Bearer test-token"}


def _session(chat_id: int, role: str) -> dict:
    """Сессионные заголовки участника ровно той роли, что проверяет сценарий."""
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,%s,'active') on conflict (tg_chat_id) do nothing",
            (chat_id, f"Человек {chat_id}", f"человек-{chat_id}", role))
        conn.execute(
            "insert into participant_role (participant_id, role)"
            " select id, %s from participant where tg_chat_id = %s on conflict do nothing",
            (role, chat_id))
        conn.commit()
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": str(chat_id)}


def test_снимок_хранилища_без_токена_отказывает(client):
    """Без сервисного заголовка маршрут недоступен: 401, а не данные хранилища."""
    response = client.get("/admin/files/storage")
    assert response.status_code == 401
    assert response.json()["code"] == "bad_service_token"


def test_ротация_без_токена_отказывает(client):
    """Холодная ротация без токена не запускается — иначе её дёрнет кто угодно."""
    response = client.post("/admin/files/cold-rotate")
    assert response.status_code == 401
    assert response.json()["code"] == "bad_service_token"


def test_сессия_участника_не_открывает_снимок_хранилища(client):
    """Регресс на сломанную проверку ролей: вызов из-под человека — отказ.

    До конвертации `set(roles) | {role} & {"admin","org"}` пропускал любого активного
    участника; теперь маршрут служебный и актор в нём не принимается вовсе — ни роль
    `troupe`, ни роль `admin` доступа не даёт.
    """
    for chat_id, role in ((250364740, "troupe"), (250364741, "admin")):
        response = client.get("/admin/files/storage", headers=_session(chat_id, role))
        assert response.status_code in (401, 403), (role, response.status_code)
        assert response.json()["code"] == "actor_not_allowed", role
        assert "cold_files" not in response.text


def test_сессия_участника_не_запускает_ротацию(client):
    """Ротация — тоже только сервисный вход; роль `admin` в заголовке не считается."""
    response = client.post("/admin/files/cold-rotate", headers=_session(250364742, "admin"))
    assert response.status_code in (401, 403)
    assert response.json()["code"] == "actor_not_allowed"


def test_с_токеном_маршрут_проходит_проверку_входа(client, monkeypatch):
    """С токеном запрос доходит до дела: проверка входа не блокирует служебный вызов.

    Тело ответа здесь зависит от состояния хранилища, а проверяется ровно одно —
    что с токеном пришёл не 401, то есть маршрут жив для того, кто им пользуется.
    """
    from app.api import admin as admin_mod

    def fake_status(_conn):
        return {"cold_files": 0, "cold_bytes": 0, "hot_files": 0, "hot_bytes": 0}

    monkeypatch.setattr(admin_mod.cold, "status", fake_status)
    monkeypatch.setattr(admin_mod.cold, "free_space", lambda: {"free": 1, "total": 1})
    response = client.get("/admin/files/storage", headers=TOKEN)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
