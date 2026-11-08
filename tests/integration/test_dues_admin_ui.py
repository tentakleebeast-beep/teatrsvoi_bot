"""FR-B7, Д6/Д8/Д12: взносы — оболочка начисления, тариф и один текст на два места.

Проверяем то, ради чего карта Ш-10: путь начисления `POST /tools/dues/accrue`
перестал быть API-сиротой (кнопка в Mini App, тот же обработчик), тариф доступен
организатору по праву пути записи `budget.write` (расхождение 4.5 отчёта 122),
а «Мои взносы» в чате и на экране называют один месяц и одни числа (4.7).

Экранный путь — настоящие маршруты `/webapp/*`: токен выдаёт боевой `issue_token`,
поэтому проверяется цепочка «токен → участник → права → данные», а не тело ответа.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
from app import core
from app.webapp import issue_token
from psycopg.rows import dict_row

from services.shared.dues import dues_lines
from tests.conftest import ADMIN_CHAT, TOKEN, _test_dsn

TZ = timezone(timedelta(hours=4))
PERIOD = "2026-11"


def _chat_auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}


def _screen_auth(chat_id: int) -> dict:
    return {"Authorization": f"Bearer {issue_token(chat_id, core.WEBAPP_SECRET)}"}


def _person(chat_id: int, name: str, role: str = "troupe",
            tariff: float | None = None, tariff_from: str | None = None) -> int:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        pid = conn.execute(
            """insert into participant (tg_chat_id, display_name, role, status,
                                        dues_tariff, dues_tariff_from)
               values (%s, %s, %s, 'active', %s, %s) returning id""",
            (chat_id, name, role, tariff, tariff_from)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s, %s) "
                     "on conflict do nothing", (pid, role))
    return pid


def _allow(participant_id: int, capability: str) -> None:
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into participant_capability_override
                          (participant_id, capability, allowed, reason)
                        values (%s, %s, true, 'тестовая фикстура')
                        on conflict (participant_id, capability) do update set allowed = true""",
                     (participant_id, capability))


def test_кнопка_начисления_идемпотентна(client):
    """Д6: начисление из Mini App — тот же путь и тот же повтор без перезаписи."""
    pid = _person(883001, "Начисляемый", tariff=1500, tariff_from="2026-09-01")

    first = client.post("/webapp/admin/dues/accrue", json={"period": PERIOD},
                        headers=_screen_auth(ADMIN_CHAT))
    assert first.status_code == 200
    assert first.json()["created"] == 1
    assert first.json()["amount_total"] == 1500

    again = client.post("/webapp/admin/dues/accrue", json={"period": PERIOD},
                        headers=_screen_auth(ADMIN_CHAT)).json()
    assert again["created"] == 0
    assert again["skipped"] == 1
    assert again["amount_total"] == 0

    with psycopg.connect(_test_dsn(), autocommit=True, row_factory=dict_row) as conn:
        rows = conn.execute("select period, amount_due from dues_charge "
                            "where participant_id = %s", (pid,)).fetchall()
    assert rows == [{"period": PERIOD, "amount_due": 1500}]

    shown = client.get(f"/webapp/admin/dues?period={PERIOD}",
                       headers=_screen_auth(ADMIN_CHAT)).json()
    mine = next(item for item in shown["data"] if item["id"] == pid)
    assert mine["charged"] is True and mine["amount_due"] == 1500
    assert shown["totals"]["due"] >= 1500


def test_тариф_виден_организатору_по_budget_write(client):
    """Д8 (4.5): право пути записи довольно — `participants.manage` не нужен."""
    pid = _person(883002, "Организатор Без Прав", tariff=2000, tariff_from="2026-09-01")
    _allow(pid, "budget.write")

    shown = client.get(f"/webapp/admin/dues?period={PERIOD}",
                       headers=_screen_auth(883002))
    assert shown.status_code == 200
    mine = next(item for item in shown.json()["data"] if item["id"] == pid)
    assert mine["tariff"] == 2000
    assert mine["tariff_applies"] is True

    # Тариф меняется тем же правом и тем же маршрутом, что у карточки человека.
    saved = client.post("/webapp/admin/tariff",
                        json={"participant_id": pid, "tariff": 2500},
                        headers=_screen_auth(883002))
    assert saved.status_code == 200
    assert saved.json()["dues_tariff"] == 2500

    # Без budget.write экран не отдаёт ни тарифов, ни начислений.
    _person(883003, "Без Денег")
    denied = client.get(f"/webapp/admin/dues?period={PERIOD}",
                        headers=_screen_auth(883003))
    assert denied.status_code == 403


def test_один_месяц_и_один_текст_в_чате_и_на_экране(client):
    """Д12 (4.7): `/dues` и `/webapp/dues` показывают один месяц и одни числа."""
    pid = _person(883004, "Один Текст", tariff=1200, tariff_from="2026-01-01")
    current = datetime.now(TZ).date().strftime("%Y-%m")
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("""insert into dues_charge (participant_id, period, amount_due,
                                                 amount_paid, status)
                        values (%s, %s, 1200, 500, 'partial')""", (pid, current))
        conn.execute("""insert into dues_charge (participant_id, period, amount_due,
                                                 amount_paid, status)
                        values (%s, '2026-08', 1200, 0, 'due')""", (pid,))

    chat = client.get("/tools/dues/me", headers=_chat_auth(883004)).json()
    screen = client.get("/webapp/my/dues", headers=_screen_auth(883004)).json()

    assert chat["current"] == screen["current"], "разные цифры одного месяца"
    assert chat["summary"] == screen["summary"], "разный месяц или разные слова"
    assert chat["summary"]["period"] == current
    assert chat["summary"]["lines"] == screen["summary"]["lines"] == dues_lines(chat)

    text = "\n".join(dues_lines(chat))
    assert chat["summary"]["title"] in text, "месяц не назван словами"
    assert "осталось 700 ₽" in text
    assert "Долг за прошлые месяцы: 1 200 ₽." in text
