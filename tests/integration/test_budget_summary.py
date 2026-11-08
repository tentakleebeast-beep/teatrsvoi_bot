"""UC-03: баланс и итоги — /tools/budget/summary (docs/development/28).

Проверяем контракт ответа, агрегацию по периодам, взносы, фильтр по проекту
и отказ без права budget.read. Внешние сервисы не трогаем.
"""

from __future__ import annotations

import psycopg

from tests.conftest import _test_dsn


def _seed_budget() -> None:
    """Два периода, два проекта, взносы и черновик (не должен попасть в итоги)."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        # project не входит в общий список очистки conftest — подчищаем сами
        conn.execute("delete from project where name in ('Бунин', 'Пурга')")
        actor = conn.execute(
            "select id from participant where tg_chat_id = 123456789").fetchone()[0]
        bunin = conn.execute(
            "insert into project (name) values ('Бунин') returning id").fetchone()[0]
        purga = conn.execute(
            "insert into project (name) values ('Пурга') returning id").fetchone()[0]
        rows = [
            ("2026-09", "show_income", "2026-09-10", "income", 10000, purga, "confirmed"),
            ("2026-09", "expense", "2026-09-12", "expense", 4000, purga, "confirmed"),
            ("2026-10", "show_income", "2026-10-05", "income", 5000, bunin, "confirmed"),
            ("2026-10", "expense", "2026-10-06", "expense", 1000, bunin, "confirmed"),
            # черновик не участвует в итогах
            ("2026-10", "expense", "2026-10-07", "expense", 9999, bunin, "draft"),
        ]
        for period, block, tx_date, tx_type, amount, project_id, status in rows:
            conn.execute(
                """insert into budget_tx
                   (period, block, tx_date, tx_type, amount, project_id,
                    purpose, status, origin)
                   values (%s,%s,%s,%s,%s,%s,'тест',%s,'import')""",
                (period, block, tx_date, tx_type, amount, project_id, status))
        conn.execute(
            """insert into dues_charge (participant_id, period, amount_due, amount_paid)
               values (%s, '2026-09', 500, 500)""", (actor,))


def _troupe_headers() -> dict:
    """Участник без права budget.read."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status)
               values (777001, 'Студиец', 'troupe', 'active')""")
        conn.execute(
            """insert into participant_role (participant_id, role)
               select id, 'troupe' from participant where tg_chat_id = 777001""")
    return {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "777001"}


def test_пустая_база_даёт_нулевой_баланс(client, auth):
    r = client.get("/tools/budget/summary", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["periods"] == []
    assert body["balance"] == 0
    assert body["income_total"] == 0
    assert body["expense_total"] == 0
    assert body["project"] is None


def test_итоги_по_нескольким_периодам_и_взносам(client, auth):
    _seed_budget()
    body = client.get("/tools/budget/summary", headers=auth).json()

    assert body["ok"] is True
    assert [p["period"] for p in body["periods"]] == ["2026-09", "2026-10"]

    sep, oct_ = body["periods"]
    # взносы (dues_charge) прибавляются к доходу периода
    assert sep["income"] == 10500
    assert sep["expense"] == 4000
    assert sep["result"] == 6500
    assert sep["ops"] == 2

    assert oct_["income"] == 5000
    assert oct_["expense"] == 1000
    assert oct_["result"] == 4000
    assert oct_["ops"] == 2  # черновик не считается

    assert body["income_total"] == 15500
    assert body["expense_total"] == 5000
    assert body["balance"] == 10500


def test_фильтр_по_проекту(client, auth):
    _seed_budget()
    body = client.get("/tools/budget/summary?project=Бунин", headers=auth).json()

    assert body["ok"] is True
    assert body["project"] == "Бунин"
    assert [p["period"] for p in body["periods"]] == ["2026-10"]
    assert body["periods"][0]["income"] == 5000
    assert body["balance"] == 4000


def test_фильтр_по_несуществующему_проекту_пуст(client, auth):
    _seed_budget()
    body = client.get("/tools/budget/summary?project=НетТакого", headers=auth).json()
    assert body["ok"] is True
    assert body["periods"] == []
    assert body["balance"] == 0


def test_нет_права_budget_read(client, auth):
    headers = _troupe_headers()
    r = client.get("/tools/budget/summary", headers=headers)
    assert r.status_code == 403
    assert "budget.read" in r.json()["detail"]
