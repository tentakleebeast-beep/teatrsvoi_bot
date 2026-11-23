"""Проекты: данные списка и карточки, права, пустые значения, план и форма.

Карточку видят только `admin` и `org` по `budget.read` (V-127); числа считаются
из `budget_plan` и `budget_tx`, а «план не задан» — `null`, не ноль. Форма проекта
меняет тип только у пустого проекта, план не пишется в закрытый месяц.

Обоснование: FR-D17, docs/development/196 §4, §5, AC-33…AC-41.
"""

from __future__ import annotations

import psycopg
import pytest
from app import core
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import BOT_TOKEN, init_data


@pytest.fixture()
def clean(client):
    def _wipe():
        with psycopg.connect(_test_dsn()) as conn:
            conn.execute("delete from budget_tx where true")
            conn.execute("delete from event_plan where true")
            conn.execute("delete from budget_plan where true")
            conn.execute("delete from budget_period where true")
            conn.execute("delete from project where true")
            conn.commit()

    _wipe()
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


def _project(name="Жизнь насекомых", kind="show", status="active") -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("insert into project (name, kind, status) values (%s,%s,%s) "
                           "returning id", (name, kind, status)).fetchone()
        conn.commit()
        return row[0]


def _tx(project_id: int, amount: float, tx_type="expense", block="expense",
        event_id=None) -> None:
    with psycopg.connect(_test_dsn()) as conn:
        category = conn.execute(
            "select id from category where direction = %s order by id limit 1",
            (("income" if tx_type == "income" else "expense"),)).fetchone()[0]
        conn.execute(
            """insert into budget_tx (period, block, tx_date, tx_type, amount,
                   category_id, project_id, purpose, status, origin, event_id, is_plan)
               values ('2026-11', %s, '2026-11-10', %s, %s, %s, %s, 'проверка',
                       'confirmed', 'manual', %s, false)""",
            (block, tx_type, amount, category, project_id, event_id))
        conn.commit()


def test_master_не_видит_список_и_карточку(clean, client):
    """AC-33: роль без `budget.read` получает 403 `no_access`, данных нет в ответе."""
    _person(792001, "master")
    project_id = _project()
    headers = _webapp_headers(client, 792001)

    listing = client.get("/webapp/projects/data", headers=headers)
    card = client.get(f"/webapp/project/data?id={project_id}", headers=headers)

    assert listing.status_code == 403 and listing.json()["code"] == "no_access"
    assert card.status_code == 403 and card.json()["code"] == "no_access"
    assert "items" not in listing.json() and "money" not in card.json()


def test_org_видит_список_с_планом_и_фактом(clean, client):
    """AC-34/38: организатор видит проекты всех типов; плохой `kind` — 400."""
    _person(792002, "org")
    project_id = _project()
    _project(name="Валерий", kind="rental")
    with psycopg.connect(_test_dsn()) as conn:
        category = conn.execute("select id from category where direction = 'expense' "
                                "order by id limit 1").fetchone()[0]
        conn.execute("insert into budget_plan (period, block, category_id, project_id, "
                     "amount_planned) values ('2026-11','expense',%s,%s,120000)",
                     (category, project_id))
        conn.commit()
    _tx(project_id, 86400)

    headers = _webapp_headers(client, 792002)
    body = client.get("/webapp/projects/data", headers=headers).json()
    item = next(row for row in body["items"] if row["id"] == project_id)
    assert item["plan"] == 120000.0 and item["fact"] == 86400.0
    assert item["kind_title"] == "Спектакль"

    bad = client.get("/webapp/projects/data?kind=archive", headers=headers)
    assert bad.status_code == 400 and bad.json()["code"] == "bad_kind"


def test_карточка_считает_остаток_и_честно_показывает_пустое(clean, client):
    """AC-39: план − факт; у проекта без плана `plan` и `rest` — `null`, не ноль."""
    _person(792003, "org")
    filled = _project()
    empty = _project(name="Пурга")
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        category = conn.execute("select id from category where direction = 'expense' "
                                "order by id limit 1").fetchone()["id"]
        conn.execute("insert into budget_plan (period, block, category_id, project_id, "
                     "amount_planned) values ('2026-11','expense',%s,%s,120000)",
                     (category, filled))
        conn.commit()
    _tx(filled, 86400)

    headers = _webapp_headers(client, 792003)
    money = client.get(f"/webapp/project/data?id={filled}", headers=headers).json()["money"]
    assert money["plan"] == 120000.0 and money["fact"] == 86400.0
    assert money["rest"] == 33600.0

    empty_money = client.get(f"/webapp/project/data?id={empty}",
                             headers=headers).json()["money"]
    assert empty_money["plan"] is None and empty_money["rest"] is None
    assert empty_money["fact"] is None


def test_доход_вне_событий_только_у_арендатора(clean, client):
    """AC-40: `unlinked_income` — сумма аренды без события; у спектакля поле `null`."""
    _person(792004, "org")
    rental = _project(name="Валерий", kind="rental")
    show = _project(name="Жизнь насекомых", kind="show")
    _tx(rental, 5000, tx_type="income", block="sublease")
    _tx(show, 7000, tx_type="income", block="sublease")

    headers = _webapp_headers(client, 792004)
    rental_money = client.get(f"/webapp/project/data?id={rental}",
                              headers=headers).json()["money"]
    show_money = client.get(f"/webapp/project/data?id={show}",
                            headers=headers).json()["money"]
    assert rental_money["unlinked_income"] == 5000.0
    assert show_money["unlinked_income"] is None


def test_форма_создаёт_проект_и_не_допускает_дубль(clean, client):
    """AC-37: имя занято без учёта регистра — 409; создание — 200."""
    _person(792005, "org")
    headers = _webapp_headers(client, 792005)
    created = client.post("/webapp/project/save", headers=headers, json={
        "name": "Пурга", "kind": "show"})
    assert created.status_code == 200, created.text
    assert created.json()["project"]["kind_title"] == "Спектакль"

    duplicate = client.post("/webapp/project/save", headers=headers, json={
        "name": "пурга", "kind": "event"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "project_name_taken"


def test_тип_меняется_только_у_пустого_проекта(clean, client):
    """AC-35/36: пустой проект меняет тип, с операцией — `409 project_kind_locked`."""
    _person(792006, "org")
    empty = _project(name="Пустой")
    busy = _project(name="С операцией")
    _tx(busy, 1000)
    headers = _webapp_headers(client, 792006)

    ok = client.post("/webapp/project/save", headers=headers, json={
        "id": empty, "name": "Пустой", "kind": "rental", "status": "active"})
    assert ok.status_code == 200, ok.text

    locked = client.post("/webapp/project/save", headers=headers, json={
        "id": busy, "name": "С операцией", "kind": "rental", "status": "active"})
    assert locked.status_code == 409
    assert locked.json()["code"] == "project_kind_locked"


def test_план_проекта_и_отказ_в_закрытый_месяц(clean, client):
    """AC-41: план пишется в строку проекта; закрытый месяц — 409 без строки."""
    _person(792007, "org")
    project_id = _project(name="Валерий", kind="rental")
    headers = _webapp_headers(client, 792007)

    saved = client.post("/webapp/project/plan", headers=headers, json={
        "project_id": project_id, "period": "2026-11", "block": "expense",
        "category": "Гонорары и доли", "amount": 120000})
    assert saved.status_code == 200, saved.text
    assert saved.json()["plan"]["amount"] == 120000.0

    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("insert into budget_period (period, status) values ('2026-12','closed')")
        conn.commit()
    closed = client.post("/webapp/project/plan", headers=headers, json={
        "project_id": project_id, "period": "2026-12", "block": "expense",
        "category": "Гонорары и доли", "amount": 50000})
    assert closed.status_code == 409 and closed.json()["code"] == "period_closed"
    with psycopg.connect(_test_dsn()) as conn:
        count = conn.execute("select count(*) from budget_plan where period = '2026-12'"
                             ).fetchone()[0]
    assert count == 0
