"""Форма поиска окон в Mini App: справочники, поиск, назначение (docs/development/53)."""

import psycopg
from app.person_key import make_key

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,%s,%s,'troupe','active') returning id",
        (chat_id, name, make_key(name))).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


class FakeEvents:
    def insert(self, calendarId, body):
        return self

    def execute(self):
        return {"id": "gcal-form"}


class FakeService:
    def events(self):
        return FakeEvents()


def test_справочники_для_формы(client):
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 787001, "Форма Первый")
    headers = _token(client, 787001)

    body = client.get("/webapp/form-data", headers=headers).json()
    assert body["ok"] is True
    assert any(item["code"] == "acting" for item in body["disciplines"])
    assert any(item["code"] == "stage" for item in body["venues"])
    assert body["people"], "список людей нужен для чекбоксов"


def test_поиск_окон_из_формы(client, monkeypatch):
    from app import core as api

    _api()
    monkeypatch.setattr(api, "fetch_events", lambda *a, **k: [])
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 787002, "Форма Второй")
        for hour in range(17, 23):
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s,'2026-12-02',%s,true,'busy','bot')""", (pid, hour))
        conn.commit()
    headers = _token(client, 787002)

    body = client.post("/webapp/slots", headers=headers, json={
        "participants": [pid], "date_from": "2026-12-02", "date_to": "2026-12-02",
        "min_hours": 2}).json()
    # windows может отсутствовать, если эндпоинт вернул ошибку — показываем её
    assert "detail" not in body, body
    assert body.get("windows"), body


def test_назначение_из_формы(client, monkeypatch):
    from app import core as api

    _api()
    monkeypatch.setattr(api, "_calendar", lambda: FakeService())
    monkeypatch.setattr(api, "CALENDAR_ID_DRAFT", "draft")
    monkeypatch.setattr(api, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    with psycopg.connect(_test_dsn()) as conn:
        pid = _person(conn, 787003, "Форма Третий")
    headers = _token(client, 787003)

    preview = client.post("/webapp/preview", headers=headers, json={
        "persist": True,
        "kind": "rehearsal", "date": "2026-12-03", "from_hour": 18, "to_hour": 20,
        "participants": [pid]}).json()
    assert preview["ok"] is True, preview

    assigned = client.post("/webapp/assign", headers=headers,
                           json={"proposal_id": preview["proposal_id"]}).json()
    assert assigned["ok"] is True
    assert assigned["in_calendar"] is True


def test_без_токена_форма_не_работает(client):
    _api()
    assert client.get("/webapp/form-data").status_code == 401


def test_старый_адрес_поиска_редиректит_в_люди(client):
    """`/webapp/search` — бывший поиск людей; экран переехал в «Управление → Люди»."""
    _api()
    page = client.get("/webapp/search", follow_redirects=False)
    assert page.status_code == 301
    assert page.headers["location"] == "/webapp/admin/t/people"


def test_страница_поиска_окон_отдаётся(client):
    """Поиск окон живёт на /webapp/slots (план 94, Ш-6)."""
    _api()
    page = client.get("/webapp/slots")
    assert page.status_code == 200
    assert "Поиск окон" in page.text


def test_вход_в_поиск_окон_принимает_критерии_из_адреса(client):
    """Ссылка со сводки и из карточки события несёт критерии в адресе (Ш-05).

    Подставляет их сам экран, поэтому сервер обязан отдать ту же страницу
    с любыми параметрами, а не 404: вход не должен ломаться от длинного адреса.
    """
    _api()
    page = client.get("/webapp/slots?from=2026-11-23&to=2026-11-29&kind=rental"
                      "&participants=1,2")
    assert page.status_code == 200
    assert "Поиск окон" in page.text
    assert 'data-kind="rental"' in page.text
