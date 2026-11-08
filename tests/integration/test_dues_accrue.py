"""FR-B7, часть 1: начисление взносов по тарифу — POST /tools/dues/accrue.

Проверяем правило заказчика от 28.10.2026: тариф хранится в профиле участника,
начисление ежемесячное, действует тариф на начало месяца, перерасчётов нет,
повторное начисление за тот же месяц ничего не меняет.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from tests.conftest import _test_dsn


def _add_person(chat_id: int, name: str, tariff: float | None,
                tariff_from: str | None = None, status: str = "active") -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status,
                                        dues_tariff, dues_tariff_from)
               values (%s, %s, 'troupe', %s, %s, %s) returning id""",
            (chat_id, name, status, tariff, tariff_from)).fetchone()
    return row[0]


def _charges(participant_id: int) -> list[dict]:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(
            """select period, amount_due, amount_paid, status from dues_charge
               where participant_id = %s order by period""",
            (participant_id,)).fetchall()


def test_начисление_по_тарифу_идемпотентно(client, auth):
    pid = _add_person(880001, "Студиец Тариф", 1500, "2026-09-01")

    first = client.post("/tools/dues/accrue", json={"period": "2026-11"}, headers=auth)
    assert first.status_code == 200
    body = first.json()
    assert body["ok"] is True
    assert body["period"] == "2026-11"
    assert body["created"] == 1
    assert body["skipped"] == 0
    assert body["amount_total"] == 1500
    assert body["rows"] == [{"participant_id": pid, "amount_due": 1500}]

    again = client.post("/tools/dues/accrue", json={"period": "2026-11"}, headers=auth).json()
    assert again["created"] == 0
    assert again["skipped"] == 1
    assert again["amount_total"] == 0

    assert _charges(pid) == [
        {"period": "2026-11", "amount_due": 1500, "amount_paid": 0, "status": "due"}]


def test_тариф_действует_с_начала_следующего_месяца(client, auth):
    pid = _add_person(880002, "Новый Тариф", 2000, "2026-12-01")

    october = client.post("/tools/dues/accrue", json={"period": "2026-11"},
                          headers=auth).json()
    assert october["created"] == 0, "тариф с 01.12 не должен начисляться за ноябрь"
    assert _charges(pid) == []

    december = client.post("/tools/dues/accrue", json={"period": "2026-12"},
                           headers=auth).json()
    assert december["created"] == 1
    assert december["amount_total"] == 2000


def test_без_тарифа_и_неактивные_не_начисляются(client, auth):
    _add_person(880003, "Без Тарифа", None)
    _add_person(880004, "Нулевой Тариф", 0)
    _add_person(880005, "Архив", 900, None, status="archived")
    _add_person(880006, "Заблокирован", 900, None, status="blocked")

    body = client.post("/tools/dues/accrue", json={"period": "2026-11"},
                       headers=auth).json()
    assert body["created"] == 0
    assert body["skipped"] == 0
    assert body["amount_total"] == 0


def test_повтор_не_затирает_оплату_и_сумму(client, auth):
    pid = _add_person(880007, "Уже Платил", 1200)
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into dues_charge (participant_id, period, amount_due,
                                        amount_paid, status)
               values (%s, '2026-11', 1000, 1000, 'paid')""", (pid,))

    body = client.post("/tools/dues/accrue", json={"period": "2026-11"},
                       headers=auth).json()
    assert body["created"] == 0
    assert body["skipped"] == 1
    assert _charges(pid) == [
        {"period": "2026-11", "amount_due": 1000, "amount_paid": 1000, "status": "paid"}]


def test_кривой_период_и_нет_права(client, auth):
    bad = client.post("/tools/dues/accrue", json={"period": "2026-13"}, headers=auth)
    assert bad.status_code == 422

    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into participant (tg_chat_id, display_name, role, status)
                        values (880008, 'Просто Студиец', 'troupe', 'active')""")
        conn.execute("""insert into participant_role (participant_id, role)
                        select id, 'troupe' from participant where tg_chat_id = 880008""")
    troupe = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "880008"}
    denied = client.post("/tools/dues/accrue", json={"period": "2026-11"}, headers=troupe)
    assert denied.status_code == 403


def test_в_аудите_остаётся_след(client, auth):
    _add_person(880009, "Для Аудита", 700)
    client.post("/tools/dues/accrue", json={"period": "2026-11"}, headers=auth)

    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute("""select entity_id, after from audit_event
                              where action = 'dues.accrue' order by id desc limit 1""").fetchone()
    assert row is not None
    assert row[0] == "2026-11"
    assert row[1]["created"] == 1
    assert row[1]["amount_total"] == 700
