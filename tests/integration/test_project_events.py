"""Деньги события через API: создание, перенос, отмена, отказы, ручная правка.

Проверяется поведение, а не форма ответа: событие аренды порождает ровно одну
операцию; перенос тянет за собой дату и период; закрытый месяц отклоняется с
аудитом; отмена переводит операцию в `reversed`; производную строку нельзя
править руками; неизвестный проект не заводится сам.

Обоснование: FR-D17, I11, I12, docs/development/196 §4.5, §7, AC-15…AC-28.
"""

from __future__ import annotations

import psycopg
import pytest
from app import core
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data

HEADERS = {"Authorization": "Bearer test-token"}


class _FakeEvents:
    def insert(self, calendarId, body):        # noqa: N803 — сигнатура Google API
        return self

    def update(self, calendarId, eventId, body):   # noqa: N803
        return self

    def delete(self, calendarId, eventId):     # noqa: N803
        return self

    def get(self, calendarId, eventId):        # noqa: N803
        return self

    def execute(self):
        # живое событие чернового календаря: `get` отдаёт подтверждённое
        return {"id": "gcal-1", "status": "confirmed"}


class _FakeService:
    def events(self):
        return _FakeEvents()


@pytest.fixture()
def clean(client, monkeypatch):
    """Чистые проекты, планы и периоды: conftest чистит только операции и события."""
    def _wipe():
        with psycopg.connect(_test_dsn()) as conn:
            conn.execute("delete from budget_tx where true")
            conn.execute("delete from event_plan where true")
            conn.execute("delete from budget_plan where true")
            conn.execute("delete from budget_period where true")
            conn.execute("delete from project where true")
            conn.commit()

    _wipe()
    monkeypatch.setattr(core, "venue_busy_for", lambda *a, **k: ([], {"source": "events"}))
    monkeypatch.setattr(core, "_calendar_write", lambda: _FakeService())
    monkeypatch.setattr(core, "CALENDAR_ID_DRAFT", "draft-calendar")
    yield
    _wipe()


def _person(chat_id: int, role: str) -> None:
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


def _webapp_headers(client, chat_id: int) -> dict:
    core.BOT_TOKEN = BOT_TOKEN
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _project(name="Валерий", kind="rental", status="active") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("insert into project (name, kind, status) values (%s,%s,%s) "
                           "returning id", (name, kind, status)).fetchone()
        conn.commit()
        return row[0]


def _event_row(event_id: int) -> dict:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return dict(conn.execute("select * from event_plan where id = %s",
                                 (event_id,)).fetchone())


def _op(event_id: int) -> dict | None:
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        row = conn.execute("select * from budget_tx where event_id = %s",
                           (event_id,)).fetchone()
        return dict(row) if row else None


def test_аренда_без_суммы_отклоняется_и_события_нет(clean, client):
    """AC-15: без `revenue_amount` — 422, ни события, ни операции."""
    _person(791001, "org")
    project_id = _project()
    headers = _webapp_headers(client, 791001)

    response = client.post("/webapp/project/event", headers=headers, json={
        "project_id": project_id, "kind": "rental", "title": "Валерий",
        "date": "2026-11-26", "from_hour": 18, "to_hour": 21, "venue": "stage"})

    assert response.status_code == 422
    assert response.json()["code"] == "revenue_amount_required"
    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select count(*) from event_plan").fetchone()[0] == 0


def test_аренда_с_суммой_создаёт_операцию_и_I11_зелёный(clean, client):
    """AC-16: событие аренды → одна операция `sublease` со статьёй «Аренда (доход)»."""
    _person(791002, "org")
    project_id = _project()
    headers = _webapp_headers(client, 791002)

    response = client.post("/webapp/project/event", headers=headers, json={
        "project_id": project_id, "kind": "rental", "title": "Валерий",
        "date": "2026-11-26", "from_hour": 18, "to_hour": 21, "venue": "stage",
        "revenue_amount": 15000})
    assert response.status_code == 200, response.text
    event_id = response.json()["event_id"]

    op = _op(event_id)
    assert op is not None
    assert op["block"] == "sublease" and op["tx_type"] == "income"
    assert float(op["amount"]) == 15000.0
    assert op["tx_date"].isoformat() == "2026-11-26"
    assert op["origin"] == "event"
    with psycopg.connect(_test_dsn()) as conn:
        ok = conn.execute("select ok from v_invariant_checks where code = 'I11'"
                          ).fetchone()[0]
    assert ok is True


def test_перенос_через_месяц_двигает_операцию(clean, client):
    """AC-20: перенос события на декабрь меняет период производной операции."""
    _person(791003, "org")
    project_id = _project()
    headers = _webapp_headers(client, 791003)
    event_id = client.post("/webapp/project/event", headers=headers, json={
        "project_id": project_id, "kind": "rental", "title": "Валерий",
        "date": "2026-11-26", "from_hour": 18, "to_hour": 21, "venue": "stage",
        "revenue_amount": 15000}).json()["event_id"]

    moved = client.post("/tools/events/move", headers={**HEADERS,
                                                       "X-Actor-Chat-Id": "791003"},
                        json={"event_id": event_id, "date": "2026-12-03"})
    assert moved.status_code == 200, moved.text

    with psycopg.connect(_test_dsn()) as conn:
        period, tx_date = conn.execute(
            "select period, tx_date from budget_tx where event_id = %s",
            (event_id,)).fetchone()
    assert period == "2026-12" and tx_date.isoformat() == "2026-12-03"


def test_перенос_в_закрытый_месяц_отказ_и_аудит(clean, client):
    """AC-21: закрытый месяц — 409, событие не сдвинуто, отказ в аудите."""
    _person(791004, "org")
    project_id = _project()
    headers = _webapp_headers(client, 791004)
    event_id = client.post("/webapp/project/event", headers=headers, json={
        "project_id": project_id, "kind": "rental", "title": "Валерий",
        "date": "2026-11-26", "from_hour": 18, "to_hour": 21, "venue": "stage",
        "revenue_amount": 15000}).json()["event_id"]
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into budget_period (period, status) values ('2026-12','closed')")
        conn.commit()

    moved = client.post("/tools/events/move", headers={**HEADERS,
                                                       "X-Actor-Chat-Id": "791004"},
                        json={"event_id": event_id, "date": "2026-12-03"})

    assert moved.status_code == 409
    assert moved.json()["code"] == "period_closed"
    assert _event_row(event_id)["starts_at"].date().isoformat() == "2026-11-26"
    with psycopg.connect(_test_dsn()) as conn:
        refused = conn.execute("select count(*) from audit_event "
                               "where action = 'budget.period.refuse'").fetchone()[0]
    assert refused >= 1


def test_отмена_переводит_операцию_в_reversed(clean, client):
    """AC-22: строка операции остаётся, статус `reversed`, I11 зелёный."""
    _person(791005, "org")
    project_id = _project()
    headers = _webapp_headers(client, 791005)
    event_id = client.post("/webapp/project/event", headers=headers, json={
        "project_id": project_id, "kind": "rental", "title": "Валерий",
        "date": "2026-11-26", "from_hour": 18, "to_hour": 21, "venue": "stage",
        "revenue_amount": 15000}).json()["event_id"]

    cancelled = client.post("/tools/events/cancel", headers={**HEADERS,
                                                             "X-Actor-Chat-Id": "791005"},
                            json={"event_id": event_id})
    assert cancelled.status_code == 200, cancelled.text
    with psycopg.connect(_test_dsn()) as conn:
        status, count = conn.execute(
            "select status, count(*) over () from budget_tx where event_id = %s",
            (event_id,)).fetchone()
        ok = conn.execute("select ok from v_invariant_checks where code = 'I11'"
                          ).fetchone()[0]
    assert status == "reversed" and count == 1 and ok is True


def test_ручная_правка_производной_операции_запрещена(clean, client):
    """AC-23: `409 event_operation` на попытку изменить производную строку."""
    _person(791006, "org")
    project_id = _project()
    headers = _webapp_headers(client, 791006)
    event_id = client.post("/webapp/project/event", headers=headers, json={
        "project_id": project_id, "kind": "rental", "title": "Валерий",
        "date": "2026-11-26", "from_hour": 18, "to_hour": 21, "venue": "stage",
        "revenue_amount": 15000}).json()["event_id"]
    tx_id = _op(event_id)["id"]

    response = client.post("/webapp/tx/save", headers=headers, json={
        "idempotency_key": "edit-derived", "tx_type": "income", "amount": 1000,
        "category": "Продажа билетов", "tx_id": tx_id})

    assert response.status_code == 409
    assert response.json()["code"] == "event_operation"
    assert float(_op(event_id)["amount"]) == 15000.0


def test_неизвестный_проект_не_заводится_сам(clean, client):
    """AC-27/28: имя проекта — 422 `unknown_project`; пустой проект пишется как раньше."""
    _person(791007, "org")
    headers = {**HEADERS, "X-Actor-Chat-Id": "791007"}
    unknown = client.post("/tools/budget/commit", headers=headers, json={
        "idempotency_key": "unknown-project", "tx_type": "expense", "amount": 500,
        "category": "Прочее", "project": "Несуществующий"})
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_project"
    with psycopg.connect(_test_dsn()) as conn:
        assert conn.execute("select count(*) from project").fetchone()[0] == 0

    no_project = client.post("/tools/budget/commit", headers=headers, json={
        "idempotency_key": "no-project", "tx_type": "expense", "amount": 500,
        "category": "Прочее", "project": None})
    assert no_project.status_code == 200, no_project.text


def test_ручной_доход_в_аренду_попадает_в_субаренду(clean, client):
    """AC-26: блок выводится из статьи, а не только из `tx_type`."""
    _person(791008, "org")
    saved = client.post("/webapp/tx/save", headers=_webapp_headers(client, 791008), json={
        "idempotency_key": "manual-lease", "tx_type": "income", "amount": 5000,
        "category": "Аренда (доход)", "tx_date": "2026-11-10"})
    assert saved.status_code == 200, saved.text
    with psycopg.connect(_test_dsn()) as conn:
        block = conn.execute("select block from budget_tx where idempotency_key = "
                             "'manual-lease'").fetchone()[0]
        # старая форма тоже узнаётся: второй статьи «Субаренда» не появляется
        old = client.post("/webapp/tx/save", headers=_webapp_headers(client, 791008), json={
            "idempotency_key": "manual-lease-old", "tx_type": "income", "amount": 3000,
            "category": "Субаренда", "tx_date": "2026-11-11"})
        assert old.status_code == 200, old.text
        categories = conn.execute("select count(*) from category where name = "
                                  "'Субаренда'").fetchone()[0]
        old_block = conn.execute("select block from budget_tx where idempotency_key = "
                                 "'manual-lease-old'").fetchone()[0]
    assert block == "sublease" and old_block == "sublease" and categories == 0
