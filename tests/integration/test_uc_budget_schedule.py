"""UC-03 (баланс и итоги) и UC-04 (расписание недели) — приёмка из docs/development/28."""

import datetime as dt

import psycopg

from tests.conftest import _test_dsn, service_today

HEADERS = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "123456789"}
TROUPE = {"Authorization": "Bearer test-token", "X-Actor-Chat-Id": "250364740"}


def _tx(conn, period: str, tx_type: str, amount: float, block: str = "expense"):
    block = block if block != "other" else ("show_income" if tx_type == "income" else "expense")
    conn.execute("""insert into budget_tx (period, tx_type, amount, block, status, tx_date, purpose)
                    values (%s, %s, %s, %s, 'confirmed', %s, 'тест сценария')""",
                 (period, tx_type, amount, block, f"{period}-15"))
    conn.commit()


def _troupe(conn, chat_id: int) -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s,'Труппа Тест','труппа-тест','troupe','active') returning id",
        (chat_id,)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
    conn.commit()
    return pid


def test_uc03_баланс_это_доходы_минус_расходы(client):
    """Приёмка: сумма по периодам равна балансу."""
    with psycopg.connect(_test_dsn()) as conn:
        _tx(conn, "2026-09", "income", 100000)
        _tx(conn, "2026-09", "expense", 40000)
        _tx(conn, "2026-10", "income", 50000)
        _tx(conn, "2026-10", "expense", 65000)

    body = client.get("/tools/budget/summary", headers=HEADERS).json()
    assert body["ok"] is True
    periods = {row["period"]: row for row in body["periods"]}
    assert periods["2026-09"]["income"] == 100000
    assert periods["2026-09"]["expense"] == 40000
    assert body["income_total"] - body["expense_total"] == body["balance"]
    assert body["balance"] == (100000 - 40000) + (50000 - 65000)


def test_uc03_проект_фильтрует_итоги(client):
    with psycopg.connect(_test_dsn()) as conn:
        project = conn.execute(
            "insert into project (name, kind, status) values ('Бунин Тест','show','active') "
            "returning id").fetchone()[0]
        conn.execute("""insert into budget_tx (period, tx_type, amount, block, status,
                                             project_id, tx_date, purpose)
                        values ('2026-11','expense',30000,'show_cost','confirmed',%s,
                                '2026-11-05','тест проекта')""", (project,))
        conn.execute("""insert into budget_tx (period, tx_type, amount, block, status,
                                             tx_date, purpose)
                        values ('2026-11','expense',7000,'expense','confirmed','2026-11-06',
                                'тест прочего')""")
        conn.commit()

    body = client.get("/tools/budget/summary?project=Бунин", headers=HEADERS).json()
    assert body["expense_total"] == 30000, "в итог проекта не попадает чужая операция"


def test_uc03_труппе_итоги_недоступны(client):
    with psycopg.connect(_test_dsn()) as conn:
        _troupe(conn, 250364740)
    assert client.get("/tools/budget/summary", headers=TROUPE).status_code == 403


def test_uc04_календарь_без_id_сообщает_об_этом(client, monkeypatch):
    from app import core as api

    monkeypatch.setattr(api, "CALENDAR_ID_READ", "")
    body = client.get("/tools/calendar/events", headers=HEADERS).json()
    assert body["ok"] is False
    assert body["error"] == "calendar_id_missing"


def test_uc04_расписание_без_календаря_не_падает(client, monkeypatch):
    """E1: календарь недоступен — честный отказ, а не 500."""
    from app import core as api

    def broken(*args, **kwargs):
        from tests.conftest import google_error

        raise google_error()

    monkeypatch.setattr(api, "CALENDAR_ID_READ", "cal")
    monkeypatch.setattr(api, "_calendar", broken)
    body = client.get("/tools/calendar/events", headers=HEADERS).json()
    assert body["ok"] is False
    assert body["error"] == "calendar_unavailable"


def test_uc04_пустой_календарь_даёт_пустой_список(client, monkeypatch):
    """E2: событий нет — пустой список, а не ошибка."""
    from app import core as api

    class Empty:
        def list(self, **kwargs):
            return self

        def execute(self):
            return {"items": []}

    class Service:
        def events(self):
            return Empty()

    monkeypatch.setattr(api, "_calendar", lambda: Service())
    monkeypatch.setattr(api, "CALENDAR_ID_READ", "cal")
    body = client.get("/tools/calendar/events", headers=HEADERS).json()
    assert body["ok"] is True
    assert body["data"] == [] and body["count"] == 0


def test_uc04_занятость_на_неделю_считается(client):
    with psycopg.connect(_test_dsn()) as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (250364741,'Неделя Тест','неделя-тест','troupe','active') returning id"
        ).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')", (pid,))
        today = service_today()
        for offset in range(3):
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s, %s, 10, false, 'busy', 'bot')""",
                         (pid, today + dt.timedelta(days=offset)))
        conn.commit()

    body = client.get("/tools/availability/summary?days=7", headers=HEADERS).json()
    assert body["ok"] is True
    assert isinstance(body["data"], list)
