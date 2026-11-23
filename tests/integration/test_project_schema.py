"""Схема миграции 075: допустимые значения, ограничения, уникальные индексы.

Проверяется сама база, а не код: тип проекта — список значений, а не подстрока;
сумма события неотрицательна; у события ровно одна операция; `origin='event'`
разрешён; уникальность плана сохраняется для студии и допускает два проекта.

Обоснование: docs/development/196 §3, AC-01…AC-08.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import _test_dsn


@pytest.fixture()
def conn(test_db):
    """Транзакция на тестовой базе: всё откатывается, соседние тесты не задеваются."""
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as connection:
        yield connection
        connection.rollback()


def test_голова_учёта_это_075(test_db):
    """AC-01: цепочка alembic доходит до головы (075 — проекты, 076 — V-132, 077 — V-134)."""
    with psycopg.connect(test_db, row_factory=dict_row) as connection:
        head = connection.execute("select version_num from alembic_version").fetchone()
    assert head["version_num"] == "077_event_participant"


def test_типы_проекта_список_а_не_подстрока(conn):
    """AC-03: `event` и `rental` приняты, `rental ` (с пробелом) — ошибка check."""
    conn.execute("insert into project (name, kind) values ('x','rental')")
    conn.execute("insert into project (name, kind) values ('y','event')")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("insert into project (name, kind) values ('z','rental ')")


def test_сумма_события_неотрицательна(conn):
    """AC-04: `revenue_amount = -1` отвергается; `null` и `0` проходят."""
    project_id = conn.execute("insert into project (name, kind) values ('p','rental') "
                              "returning id").fetchone()["id"]
    for revenue in (None, 0):
        conn.execute(
            "insert into event_plan (title, kind, project_id, starts_at, ends_at,"
            " revenue_amount) values ('e','rental',%s,now(),now() + interval '1 hour',%s)",
            (project_id, revenue))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into event_plan (title, kind, project_id, starts_at, ends_at,"
            " revenue_amount) values ('e','rental',%s,now(),now() + interval '1 hour',-1)",
            (project_id,))


def test_одна_операция_на_событие(conn):
    """AC-05: второй `budget_tx` с тем же `event_id` — уникальный индекс; без него — можно."""
    project_id = conn.execute("insert into project (name, kind) values ('p','rental') "
                              "returning id").fetchone()["id"]
    event_id = conn.execute(
        "insert into event_plan (title, kind, project_id, starts_at, ends_at,"
        " revenue_amount) values ('e','rental',%s,now(),now() + interval '1 hour',1000)"
        " returning id", (project_id,)).fetchone()["id"]
    category = conn.execute("select id from category where direction = 'income' "
                            "order by id limit 1").fetchone()["id"]
    insert = ("insert into budget_tx (period, block, tx_date, tx_type, amount,"
              " category_id, project_id, purpose, origin, event_id)"
              " values ('2026-11','sublease',current_date,'income',1000,%s,%s,'e','event',%s)")
    conn.execute(insert, (category, project_id, event_id))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():            # savepoint: откат не рушит транзакцию теста
            conn.execute(insert, (category, project_id, event_id))
    # без event_id — сколько угодно раз: частичный индекс не мешает
    free = ("insert into budget_tx (period, block, tx_date, tx_type, amount,"
            " category_id, project_id, purpose, origin, event_id)"
            " values ('2026-11','sublease',current_date,'income',1000,%s,%s,'e','event',null)")
    conn.execute(free, (category, project_id))
    conn.execute(free, (category, project_id))


def test_origin_event_разрешён_а_чужой_нет(conn):
    """AC-06: `origin='event'` принимается, `origin='calendar'` — ошибка check."""
    category = conn.execute("select id from category where direction = 'expense' "
                            "order by id limit 1").fetchone()["id"]
    conn.execute(
        "insert into budget_tx (period, block, tx_date, tx_type, amount, category_id,"
        " purpose, origin) values ('2026-11','expense',current_date,'expense',10,%s,'x','event')",
        (category,))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "insert into budget_tx (period, block, tx_date, tx_type, amount, category_id,"
            " purpose, origin)"
            " values ('2026-11','expense',current_date,'expense',10,%s,'x','calendar')",
            (category,))


def test_уникальность_плана_сохранилась_и_даёт_место_проектам(conn):
    """AC-07: две строки студии с одной статьёй невозможны, два проекта — можно."""
    category = conn.execute("select id from category where direction = 'expense' "
                            "order by id limit 1").fetchone()["id"]
    one = conn.execute("insert into project (name, kind) values ('a','show') returning id"
                       ).fetchone()["id"]
    two = conn.execute("insert into project (name, kind) values ('b','show') returning id"
                       ).fetchone()["id"]
    studio = ("insert into budget_plan (period, block, category_id, amount_planned)"
              " values ('2026-11','expense',%s,100)")
    conn.execute(studio, (category,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(studio, (category,))
    project_plan = ("insert into budget_plan (period, block, category_id, project_id,"
                    " amount_planned) values ('2026-11','expense',%s,%s,100)")
    conn.execute(project_plan, (category, one))
    conn.execute(project_plan, (category, two))


def test_план_и_факт_разные_наборы_блоков(conn):
    """AC-08: `show_invest` — блок операции, а не плана; наборы не унифицируются."""
    category = conn.execute("select id from category where direction = 'expense' "
                            "order by id limit 1").fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("insert into budget_plan (period, block, category_id, amount_planned)"
                     " values ('2026-11','show_invest',%s,100)", (category,))
