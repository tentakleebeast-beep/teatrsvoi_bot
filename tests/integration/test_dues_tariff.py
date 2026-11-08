"""FR-B7, часть 2: тариф взноса в профиле — видимость и назначение.

Правило заказчика 28.10.2026: тариф живёт в профиле участника, меняет его
администратор, новый тариф действует со следующего месяца, перерасчётов нет.
Человек видит тариф в анкете и в «Мои взносы».
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from tests.conftest import TOKEN, _test_dsn

TZ = timezone(timedelta(hours=4))


def _next_month_start() -> str:
    today = datetime.now(TZ).date()
    return ((today.replace(day=28) + timedelta(days=4)).replace(day=1)).isoformat()


def _add_person(chat_id: int, name: str, role: str = "troupe",
                tariff: float | None = None, tariff_from: str | None = None) -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        row = conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status,
                                        dues_tariff, dues_tariff_from)
               values (%s, %s, %s, 'active', %s, %s) returning id""",
            (chat_id, name, role, tariff, tariff_from)).fetchone()
        conn.execute("insert into participant_role (participant_id, role) values (%s, %s) "
                     "on conflict do nothing", (row[0], role))
    return row[0]


def _person_auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _tariff_row(participant_id: int) -> dict:
    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        return conn.execute("select dues_tariff, dues_tariff_from from participant "
                            "where id = %s", (participant_id,)).fetchone()


def test_админ_назначает_тариф_со_следующего_месяца(client, auth):
    pid = _add_person(881001, "Тариф Назначаемый")

    saved = client.post(f"/tools/participants/{pid}/dues-tariff",
                        json={"tariff": 1500}, headers=auth)
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["dues_tariff"] == 1500
    assert body["dues_tariff_from"] == _next_month_start()
    assert body["display_name"] == "Тариф Назначаемый"

    row = _tariff_row(pid)
    assert float(row["dues_tariff"]) == 1500
    assert row["dues_tariff_from"].isoformat() == _next_month_start()

    shown = client.get(f"/tools/participants/{pid}/dues-tariff", headers=auth).json()
    assert shown["ok"] is True
    # в текущем месяце тариф ещё не действует — начислять по нему нельзя
    assert shown["current"]["tariff"] == 1500
    assert shown["current"]["amount"] is None
    assert shown["next"]["amount"] == 1500

    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        events = conn.execute(
            """select action, before, after from audit_event
               where action = 'dues.tariff.set' and entity_id = %s""",
            (str(pid),)).fetchall()
    assert len(events) == 1
    assert events[0]["after"]["tariff"] == 1500
    assert events[0]["before"]["tariff"] is None


def test_человек_видит_тариф_в_анкете_и_в_взносах(client, auth):
    pid = _add_person(881002, "Тариф Видимый")
    assert client.post(f"/tools/participants/{pid}/dues-tariff",
                       json={"tariff": 2000}, headers=auth).status_code == 200

    me = client.get("/tools/profile/me", headers=_person_auth(881002)).json()
    assert me["dues_tariff"] == 2000
    assert me["dues_tariff_from"] == _next_month_start()

    dues = client.get("/tools/dues/me", headers=_person_auth(881002)).json()
    assert dues["ok"] is True
    assert dues["tariff"]["tariff"] == 2000
    assert dues["tariff"]["amount"] is None          # со следующего месяца
    assert dues["next"]["amount"] == 2000
    assert dues["data"] == []


def test_действующий_тариф_виден_в_моих_взносах(client):
    pid = _add_person(881003, "Тариф Действующий", tariff=1200, tariff_from="2026-01-01")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into dues_charge (participant_id, period, amount_due, amount_paid)
                        values (%s, '2026-10', 1200, 1200)""", (pid,))

    dues = client.get("/tools/dues/me", headers=_person_auth(881003)).json()
    assert dues["tariff"]["tariff"] == 1200
    assert dues["tariff"]["amount"] == 1200
    assert dues["data"][0]["period"] == "2026-10"
    assert float(dues["data"][0]["amount_paid"]) == 1200


def test_снятие_тарифа_останавливает_будущие_начисления(client, auth):
    pid = _add_person(881004, "Тариф Снимаемый", tariff=900, tariff_from="2026-01-01")

    saved = client.post(f"/tools/participants/{pid}/dues-tariff",
                        json={"tariff": None}, headers=auth)
    assert saved.status_code == 200
    assert saved.json()["dues_tariff"] is None
    assert saved.json()["dues_tariff_from"] is None

    row = _tariff_row(pid)
    assert row["dues_tariff"] is None
    assert row["dues_tariff_from"] is None


def test_без_права_на_бюджет_403(client, auth):
    pid = _add_person(881005, "Тариф Безправный")
    _add_person(881006, "Просто Студиец")

    denied = client.post(f"/tools/participants/{pid}/dues-tariff",
                         json={"tariff": 500}, headers=_person_auth(881006))
    assert denied.status_code == 403
    assert _tariff_row(pid)["dues_tariff"] is None


def test_неизвестный_участник_404(client, auth):
    assert client.get("/tools/participants/999999/dues-tariff",
                      headers=auth).status_code == 404
    assert client.post("/tools/participants/999999/dues-tariff",
                       json={"tariff": 100}, headers=auth).status_code == 404


def test_заявки_об_оплате_взноса_нет_и_взносы_сами_не_двигаются(client):
    """FR-B7, S-10: взносы только импортом, заявки «я оплатил» нет (карта t_28e08a7f).

    Решение владельца 04.11.2026: участник не заявляет об оплате — организатор сам
    вносит взносы как статью дохода студии. Поэтому сирота-маршрут
    `POST /webapp/my/dues/claim` удалён, а не получил оболочку
    (`docs/architecture/25` §9.4 строка Д7, правило 7). Проверка держит инвариант I4:
    попытка участника заявить об оплате не двигает ни `dues_charge.amount_paid`,
    ни `budget_tx`.
    """
    from app.main import app  # noqa: PLC0415

    paths = app.openapi()["paths"]
    assert "/webapp/my/dues/claim" not in paths
    assert "/webapp/my/dues" in paths            # чтение своих взносов осталось

    pid = _add_person(882001, "Без Заявки")
    period = datetime.now(TZ).date().strftime("%Y-%m")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into dues_charge (participant_id, period, amount_due, amount_paid)
                        values (%s, %s, 5000, 0)""", (pid, period))

    attempt = client.post("/webapp/my/dues/claim", json={"period": period},
                          headers=_person_auth(882001))
    assert attempt.status_code == 404

    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        charge = conn.execute("select amount_paid from dues_charge where participant_id = %s",
                              (pid,)).fetchone()
        tx = conn.execute("select count(*) c from budget_tx where participant_id = %s",
                          (pid,)).fetchone()
    assert float(charge["amount_paid"]) == 0
    assert tx["c"] == 0
