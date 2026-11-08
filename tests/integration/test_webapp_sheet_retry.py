"""Ш-37: повтор записи в лист из Mini App (действие панели).

Право — `budget.write`, как у кнопки бота и эндпоинта `/tools/budget/sheet-retry`.
Ответ тот же по ключам: `attempted`, `written`, `already`, `failed`, `remaining`.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str = "troupe") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,'active') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_без_права_повтор_закрыт(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 784001, "Повтор Без Прав")
    headers = _token(client, 784001)

    r = client.post("/webapp/admin/sheet-retry", json={}, headers=headers)
    assert r.status_code == 403
    assert "budget.write" in r.json()["detail"]

    with psycopg.connect(_test_dsn()) as conn:
        denied = conn.execute(
            "select count(*) from audit_event where action = 'access.denied' "
            "and after->>'via' = 'webapp' and after->>'required' = 'budget.write'"
        ).fetchone()[0]
    assert denied == 1


def test_админ_получает_контракт_ответа(client, auth, monkeypatch):
    """Пустая очередь: ключи ответа те же, что у эндпоинта tool-api."""
    import importlib

    main = importlib.import_module("app.core")
    monkeypatch.setattr(main, "BUDGET_SPREADSHEET_ID", "")

    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 784010, "Повтор Админ", role="admin")
    headers = _token(client, 784010)

    body = client.post("/webapp/admin/sheet-retry", json={}, headers=headers).json()
    assert set(body) >= {"ok", "attempted", "written", "already", "failed", "remaining"}
    assert body["ok"] is False and body["error"] == "no_spreadsheet"


def test_действие_объявлено_для_страницы():
    from app.webapp import ADMIN_ACTIONS, allowed_admin_actions

    assert ADMIN_ACTIONS["retry_sheet"] == ("budget.write",)
    assert allowed_admin_actions(["budget.write"])["retry_sheet"] is True
    assert allowed_admin_actions(["budget.read"])["retry_sheet"] is False
