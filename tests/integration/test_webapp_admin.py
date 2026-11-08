"""Админ-панель в Mini App только для чтения (обращение №14, docs/development/73)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str = "troupe", status: str = "active") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,%s,%s) returning id",
        (chat_id, name, make_key(name), role, status)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_без_права_панель_закрыта(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782001, "Панель Без Прав")
    body = client.get("/webapp/admin/overview", headers=_token(client, 782001))
    assert body.status_code == 403
    assert body.json()["detail"] == "admin_panel_forbidden"


def test_админ_видит_участников_заявки_и_права(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782002, "Панель Админ", role="admin")
        _person(conn, 782003, "Панель Заявка", status="pending")
    headers = _token(client, 782002)

    result = client.get("/webapp/admin/overview", headers=headers)
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True
    assert set(body["allowed"]) >= {"people", "pending", "rights", "budget", "audit"}
    assert body["blocks"]["people"]["counts"]["active"] >= 1
    assert body["blocks"]["people"]["counts"]["pending"] >= 1
    names = [p["display_name"] for p in body["blocks"]["pending"]]
    assert "Панель Заявка" in names
    roles = [m["role"] for m in body["blocks"]["rights"]["matrix"]]
    assert "admin" in roles
    assert "period_title" in body["blocks"]["budget"]


def test_организатор_видит_людей_но_не_права(client):
    """FR-A6, Ш-02: «Права» на экране — по `participants.manage`, а не по просмотру.

    У роли `org` есть `participants.view`, поэтому «Люди» приходят; матрицы прав
    в ответе нет — иначе экран показывал бы вход, а выдача прав отвечала 403 (Л4).
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782004, "Панель Орг", role="org")

    result = client.get("/webapp/admin/overview", headers=_token(client, 782004))
    assert result.status_code == 200, result.text
    body = result.json()
    assert "rights" not in body["allowed"]
    assert "rights" not in body["blocks"]
    assert "people" in body["allowed"]
    assert body["actions"]["manage_rights"] is False


def test_архив_не_приходит_без_права_управления_людьми(client):
    """FR-A7, отчёт 142 §3 строка 26, карта t_c6a13266: архив — не просмотр.

    Блок `archive` строился под `"people" in allowed`, то есть отдавался по
    `participants.view`. У org и master право есть на просмотр и нет на возврат:
    данные ушедших людей уходили в браузер, хотя кнопка «Вернуть в активные»
    ответила бы 403.
    """
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782007, "Архив Незримый", role="troupe", status="archived")
        _person(conn, 782008, "Архив Панель Орг", role="org")
        _person(conn, 782011, "Архив Панель Мастер", role="master")

    for chat_id in (782008, 782011):
        headers = _token(client, chat_id)
        body = client.get("/webapp/admin/overview", headers=headers).json()
        assert "archive" not in body["blocks"], "архив ушёл в браузер без права"
        assert body["actions"]["manage_rights"] is False
        assert all(person["status"] != "archived"
                   for person in body["blocks"]["people"]["data"])


def test_архив_приходит_с_правом_управления_людьми(client):
    """Право `participants.manage` у администратора: блок и возврат на месте."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782009, "Архив Видимый Админ", role="troupe", status="archived")
        _person(conn, 782010, "Архив Панель Админ", role="admin")

    body = client.get("/webapp/admin/overview", headers=_token(client, 782010)).json()
    names = [person["display_name"] for person in body["blocks"]["archive"]["data"]]
    assert "Архив Видимый Админ" in names
    assert body["actions"]["manage_rights"] is True


def test_старый_адрес_панели_редиректит(client):
    """`/webapp/admin` — бывший общий вход: разделы теперь на своих адресах (план 94, Ш-5)."""
    _api()
    page = client.get("/webapp/admin", follow_redirects=False)
    assert page.status_code == 301
    assert page.headers["location"] == "/webapp/admin/t/people"


def test_страница_открывается_без_данных(client):
    """Раздел «Люди» отдаётся пустой базе: оболочка, подпись и загрузка данных."""
    _api()
    page = client.get("/webapp/admin/t/people")
    assert page.status_code == 200
    assert "Люди" in page.text
    assert "/webapp/admin/overview" in page.text


def test_без_права_на_бюджет_раздел_не_отдаётся(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 782004, "Панель Аудитор")
        conn.execute(
            "insert into participant_capability_override "
            "(participant_id, capability, allowed) values (%s, %s, true)",
            (pid, "audit.view"))
        conn.commit()
    headers = _token(client, 782004)
    body = client.get("/webapp/admin/overview", headers=headers).json()
    assert body["allowed"] == ["audit"]
    assert list(body["blocks"]) == ["audit"]


def test_панель_не_показывает_закрытые_обращения(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 782005, "Панель Автор Обращения")
        _person(conn, 782006, "Панель Обращения Админ", role="admin")
    author = _token(client, 782005)
    created = client.post("/webapp/feedback", headers=author,
                          json={"kind": "wish", "text": "Закрытое в панели"}).json()
    assert created.get("ok") is True, created
    admin = _token(client, 782006)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("update feedback set status = %s, resolved_at = now() where id = %s",
                     ("done", created["id"]))
        conn.commit()

    body = client.get("/webapp/admin/overview", headers=admin).json()
    data = body["blocks"]["feedback"]["data"]
    assert all(item["id"] != created["id"] for item in data)
    assert all(item["status"] in ("new", "seen", "planned") for item in data)
    assert body["blocks"]["feedback"]["counts"].get("done", 0) >= 1
