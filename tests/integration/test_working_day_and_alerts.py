"""Решения заказчика 28.10.2026: рабочий день, «удобно», крупная трата (C-4, C-5, C-8, FR-B5)."""

import psycopg
from app.person_key import make_key

from tests.conftest import ADMIN_CHAT, _test_dsn
from tests.integration.test_smoke_api_fixes import HEADERS
from tests.integration.test_webapp_profile import _api

JSON = {**HEADERS, "Content-Type": "application/json"}


def _conn():
    return psycopg.connect(_test_dsn())


def _person(name: str, chat_id: int, role: str = "troupe") -> int:
    with _conn() as conn:
        pid = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status) "
            "values (%s,%s,%s,%s,'active') returning id",
            (chat_id, name, make_key(name), role)).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                     (pid, role))
        conn.commit()
    return pid


def _marks(pid: int, day: str, hours: list[int], kind: str, is_free: bool) -> None:
    with _conn() as conn:
        for hour in hours:
            conn.execute("""insert into availability_hour
                              (participant_id, day, hour, is_free, kind, source)
                            values (%s,%s,%s,%s,%s,'bot')
                            on conflict (participant_id, day, hour) do update
                              set is_free = excluded.is_free, kind = excluded.kind""",
                         (pid, day, hour, is_free, kind))
        conn.commit()


# --- C-5: часы вне рабочего дня ------------------------------------------------

def test_c5_ночная_отметка_требует_подтверждения(client):
    """Час вне 08–23 требует подтверждения на каноне записи (З1, C-5).

    Раньше правило проверялось через `POST /webapp/grid`; сетка месяца теперь
    только чтение (Ш-04), поэтому вход тот же, что у Mini App и чата, —
    `POST /tools/availability/commit`. Проверка одна на все входы, иначе «ночную»
    отметку можно было бы поставить обходным путём.
    """
    pid = _person("Ночной Первый", 781101)
    headers = {**HEADERS, "Content-Type": "application/json", "X-Actor-Chat-Id": "781101"}
    body = {"chat_id": 781101, "day": "2026-11-10", "start": 2, "end": 4, "kind": "busy"}
    r = client.post("/tools/availability/commit", headers=headers, json=body)
    assert r.status_code == 400
    assert "outside_work_day" in r.json()["detail"]

    r = client.post("/tools/availability/commit", headers=headers,
                    json={**body, "allow_outside_hours": True})
    assert r.status_code == 200, pid


def test_c5_занятость_вне_рабочего_дня_требует_подтверждения(client):
    body = {"chat_id": ADMIN_CHAT, "day": "2026-11-10", "start": 1, "end": 3, "kind": "busy"}
    assert client.post("/tools/availability/commit", headers=JSON, json=body).status_code == 400
    r = client.post("/tools/availability/commit", headers=JSON,
                    json={**body, "allow_outside_hours": True})
    assert r.status_code == 200


def test_c5_рабочий_день_не_требует_подтверждения(client):
    _api()
    _person("Дневной Первый", 781102)
    headers = {**HEADERS, "Content-Type": "application/json", "X-Actor-Chat-Id": "781102"}
    r = client.post("/tools/availability/commit", headers=headers,
                    json={"chat_id": 781102, "day": "2026-11-11", "start": 19, "end": 20,
                          "kind": "free"})
    assert r.status_code == 200


# --- C-4: «удобно» = свободен с пометкой --------------------------------------

def test_c4_удобно_не_убирает_окно_и_помечается(client):
    pid = _person("Крылов Виктор", 900001)
    _marks(pid, "2026-11-12", [18, 19, 20, 21], "preferred", True)

    r = client.post("/tools/schedule/slots", headers=JSON, json={
        "participants": [pid], "date_from": "2026-11-12", "date_to": "2026-11-12",
        "min_hours": 2, "day_start": 18, "day_end": 22})
    assert r.status_code == 200
    windows = r.json()["windows"]
    assert windows, "окно с «удобно» должно предлагаться"
    assert windows[0].get("preferred"), "пометка о неудобном часе обязательна"


# --- C-8 / FR-B5: крупная трата уходит в очередь уведомлений -------------------

def test_c8_крупная_трата_пишется_сразу_без_подтверждения(client):
    """Решение 30.10.2026: второго подтверждающего нет — суммы вносят организаторы.

    Раньше операция свыше 100 000 ₽ уходила в очередь подтверждения и требовала
    второго человека. Теперь она пишется сразу, а след остаётся в аудите.
    """
    r = client.post("/tools/budget/commit", headers=JSON, json={
        "chat_id": ADMIN_CHAT, "idempotency_key": "test:big:no-approval",
        "tx_type": "expense", "amount": 150000, "category": "Реквизит и костюмы",
        "purpose": "проверка прямой записи"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "requires_approval" not in data, "очереди подтверждения больше нет"
    assert data["tx"]["id"]

    with _conn() as conn:
        status = conn.execute("select status from approval_request").fetchall()
        tx = conn.execute("select amount from budget_tx where id = %s",
                          (data["tx"]["id"],)).fetchone()
    assert status == [], "заявки на подтверждение не создаются"
    assert float(tx[0]) == 150000


