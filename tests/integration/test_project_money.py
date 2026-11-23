"""Деньги проекта: производная операция и инварианты I11/I12 на живой базе.

Проверяется то, что нельзя увидеть в unit-тесте: поля операции, которые выводятся
из типа события, идемпотентность по `unique (event_id)`, пересчёт даты и периода
при переносе, отказ на закрытый месяц и поведение `v_invariant_checks` при каждой
поломке.

Обоснование: FR-D17, I11, I12, docs/development/196 §7–8, AC-09…AC-13, AC-16…AC-22.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
import pytest
from app import core, project_money
from app.errors import ApiError
from psycopg.rows import dict_row

from tests.conftest import _test_dsn

MONTH = "2026-11"


@pytest.fixture()
def db(test_db):
    """Чистые строки денег и проектов: conftest чистит budget_tx, но не project."""
    with psycopg.connect(test_db, row_factory=dict_row) as conn:
        conn.execute("delete from budget_tx where true")
        conn.execute("delete from event_plan where true")
        conn.execute("delete from budget_plan where true")
        conn.execute("delete from budget_period where true")
        conn.execute("delete from project where true")
        conn.commit()
    yield test_db
    with psycopg.connect(test_db, row_factory=dict_row) as conn:
        conn.execute("delete from budget_tx where true")
        conn.execute("delete from event_plan where true")
        conn.execute("delete from budget_plan where true")
        conn.execute("delete from budget_period where true")
        conn.execute("delete from project where true")
        conn.commit()


def _project(conn, name="Валерий", kind="rental") -> int:
    return conn.execute("insert into project (name, kind) values (%s, %s) returning id",
                        (name, kind)).fetchone()["id"]


def _event(conn, project_id: int, when: datetime, kind="rental",
           revenue: float | None = 15000) -> int:
    return conn.execute(
        """insert into event_plan (title, kind, project_id, starts_at, ends_at,
               status, revenue_amount)
           values ('Событие', %s, %s, %s, %s, 'confirmed', %s) returning id""",
        (kind, project_id, when, when + timedelta(hours=2), revenue)).fetchone()["id"]


def _i11(conn) -> dict:
    return conn.execute("select ok, detail from v_invariant_checks where code = 'I11'"
                        ).fetchone()


def _i12(conn) -> dict:
    return conn.execute("select ok, detail from v_invariant_checks where code = 'I12'"
                        ).fetchone()


def test_создание_аренды_даёт_одну_операцию_и_зелёный_I11(db):
    """AC-16: тип события решает блок и статью; операция одна и сходится с событием."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)

        created = project_money.create_for_event(conn, event_id, {"id": None})
        conn.commit()

        tx = conn.execute("select * from budget_tx where event_id = %s",
                          (event_id,)).fetchone()
        category = conn.execute(
            "select c.name from category c join budget_tx t on t.category_id = c.id "
            "where t.id = %s", (tx["id"],)).fetchone()
        assert _i11(conn)["ok"] is True

    assert created["id"] == tx["id"]
    assert tx["block"] == "sublease" and tx["tx_type"] == "income"
    assert float(tx["amount"]) == 15000.0
    assert tx["tx_date"].isoformat() == "2026-11-26" and tx["period"] == MONTH
    assert tx["origin"] == "event" and tx["project_id"] == project_id
    assert tx["purpose"] == "Аренда — Валерий — 26.11.2026"
    assert category["name"] == "Аренда (доход)"


def test_показ_арендатора_это_показы_а_не_аренда(db):
    """AC-17: событие `show` даёт `show_income` и статью «Продажа билетов»."""
    when = datetime(2026, 11, 27, 19, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when, kind="show", revenue=8000)
        project_money.create_for_event(conn, event_id, {"id": None})
        conn.commit()
        tx = conn.execute("select block, category_id from budget_tx where event_id = %s",
                          (event_id,)).fetchone()
        name = conn.execute("select name from category where id = %s",
                            (tx["category_id"],)).fetchone()["name"]
    assert tx["block"] == "show_income"
    assert name == "Продажа билетов"


def test_повторный_вызов_не_плодит_вторую_операцию(db):
    """AC-18: повторная синхронизация возвращает существующую строку, `rev` прежний."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        first = project_money.create_for_event(conn, event_id, {"id": None})
        second = project_money.create_for_event(conn, event_id, {"id": None})
        conn.commit()
        count = conn.execute("select count(*) as n from budget_tx where event_id = %s",
                             (event_id,)).fetchone()["n"]
        rev = conn.execute("select rev from budget_tx where id = %s",
                           (first["id"],)).fetchone()["rev"]
    assert count == 1
    assert second["id"] == first["id"] and rev == 1


def test_правка_суммы_поднимает_rev(db):
    """AC-19: сумма операции следует за событием, `rev` растёт."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        tx = project_money.create_for_event(conn, event_id, {"id": None})
        project_money.update_amount(conn, event_id, 18000, {"id": None})
        conn.commit()
        row = conn.execute("select amount, rev from budget_tx where id = %s",
                           (tx["id"],)).fetchone()
    assert float(row["amount"]) == 18000.0 and row["rev"] == 2


def test_перенос_через_месяц_двигает_дату_и_период(db):
    """AC-20: дата и период операции следуют за событием, новый период создаётся."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    new_when = datetime(2026, 12, 3, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        tx = project_money.create_for_event(conn, event_id, {"id": None})
        # реальный путь: сначала двигается событие, затем его производная операция
        conn.execute("update event_plan set starts_at = %s, ends_at = %s where id = %s",
                     (new_when, new_when + timedelta(hours=2), event_id))
        project_money.move_date(conn, event_id, new_when, {"id": None})
        conn.commit()
        row = conn.execute("select tx_date, period from budget_tx where id = %s",
                           (tx["id"],)).fetchone()
        period = conn.execute("select status from budget_period where period = '2026-12'"
                              ).fetchone()
        assert _i11(conn)["ok"] is True
    assert row["tx_date"].isoformat() == "2026-12-03" and row["period"] == "2026-12"
    assert period["status"] == "open"


def test_перенос_в_закрытый_месяц_отказ(db):
    """AC-21: закрытый месяц — `409 period_closed`, строка операции не меняется."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        tx = project_money.create_for_event(conn, event_id, {"id": None})
        conn.execute("insert into budget_period (period, status) values ('2026-12','closed')")
        conn.commit()
        with pytest.raises(ApiError) as exc:
            project_money.move_date(conn, event_id, datetime(2026, 12, 3, 18,
                                                             tzinfo=core.TZ), {"id": None})
        conn.rollback()
        row = conn.execute("select period, tx_date from budget_tx where id = %s",
                           (tx["id"],)).fetchone()
        refused = conn.execute("select count(*) as n from audit_event "
                               "where action = 'budget.period.refuse'").fetchone()["n"]
    assert exc.value.code == "period_closed"
    assert row["period"] == MONTH and row["tx_date"].isoformat() == "2026-11-26"
    assert refused >= 1


def test_отмена_переводит_операцию_в_reversed(db):
    """AC-22: строка остаётся, но `confirmed` больше нет — I11 зелёный."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        tx = project_money.create_for_event(conn, event_id, {"id": None})
        conn.execute("update event_plan set status = 'cancelled' where id = %s", (event_id,))
        project_money.cancel(conn, event_id, {"id": None})
        conn.commit()
        row = conn.execute("select status from budget_tx where id = %s",
                           (tx["id"],)).fetchone()
        count = conn.execute("select count(*) as n from budget_tx where event_id = %s",
                             (event_id,)).fetchone()["n"]
        assert _i11(conn)["ok"] is True
    assert row["status"] == "reversed" and count == 1


def test_i11_ловит_расхождение_суммы(db):
    """AC-10: операция на 14000 против события 15000 — I11 краснеет."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        tx = project_money.create_for_event(conn, event_id, {"id": None})
        conn.execute("update budget_tx set amount = 14000 where id = %s", (tx["id"],))
        conn.commit()
        check = _i11(conn)
    assert check["ok"] is False
    assert "1 событий без верной операции" in check["detail"]


def test_i11_ловит_отменённое_с_подтверждённой_операцией(db):
    """AC-11: отменённое событие с `confirmed` операцией — нарушение."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when)
        project_money.create_for_event(conn, event_id, {"id": None})
        conn.execute("update event_plan set status = 'cancelled' where id = %s", (event_id,))
        conn.commit()
        assert _i11(conn)["ok"] is False


def test_i12_ловит_арендатора_в_бюджете_спектакля(db):
    """AC-12/13: производная операция у проекта `show` или без `event_id` — нарушение."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        rental = _project(conn, name="Валерий", kind="rental")
        event_id = _event(conn, rental, when)
        tx = project_money.create_for_event(conn, event_id, {"id": None})
        conn.commit()
        assert _i12(conn)["ok"] is True

        # операция «на арендатора», но у проекта-спектакля
        show = _project(conn, name="Пурга", kind="show")
        conn.execute("update budget_tx set project_id = %s where id = %s",
                     (show, tx["id"]))
        conn.commit()
        assert _i12(conn)["ok"] is False

        # и обратно: у арендатора, но без event_id
        conn.execute("update budget_tx set project_id = %s, event_id = null where id = %s",
                     (rental, tx["id"]))
        conn.commit()
        assert _i12(conn)["ok"] is False


def test_событие_без_суммы_не_создаёт_операцию(db):
    """Событие без `revenue_amount` доходным не считается: операции нет."""
    when = datetime(2026, 11, 26, 18, tzinfo=core.TZ)
    with psycopg.connect(db, row_factory=dict_row) as conn:
        project_id = _project(conn)
        event_id = _event(conn, project_id, when, revenue=None)
        assert project_money.create_for_event(conn, event_id, {"id": None}) is None
        count = conn.execute("select count(*) as n from budget_tx where event_id = %s",
                             (event_id,)).fetchone()["n"]
    assert count == 0
