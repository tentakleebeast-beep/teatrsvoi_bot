"""
Экраны организатора в Mini App: операция, профиль участника, поиск окон.

Решение 30.10.2026: расход и доход вносятся формой в окне (план не входит в
баланс), профиль участника открывается организатору для тарифа, окна ищутся по
критериям с пояснениями.

Правила и требования раздела: docs/development/73-admin-panel-webapp.md.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

from tests.conftest import TOKEN, _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _person(conn, chat_id: int, name: str, role: str = "org") -> int:
    pid = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status, phone) "
        "values (%s,%s,%s,%s,'active','+79171232418') returning id",
        (chat_id, name, make_key(name), role)).fetchone()[0]
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)",
                 (pid, role))
    conn.commit()
    return pid


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _expense_total(client, chat_id: int) -> float:
    """Сколько расходов видит бюджет: план туда попадать не должен."""
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Actor-Chat-Id": str(chat_id)}
    body = client.get("/tools/budget/summary", headers=headers).json()
    assert body.get("ok") is True, body
    if "expense_total" in body:
        return float(body["expense_total"])
    return float(sum(period["expense"] for period in body.get("periods") or []))


def test_план_не_входит_в_баланс_а_факт_входит(client) -> None:
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 790001, "Форма Операций")
    headers = _token(client, 790001)
    before = _expense_total(client, 790001)

    plan = client.post("/webapp/tx/save", headers=headers, json={
        "tx_type": "expense", "amount": 5000, "category": "Реквизит и костюмы",
        "tx_date": "2026-10-15", "is_plan": True, "idempotency_key": "test:plan:1"}).json()
    assert plan["ok"] is True
    assert _expense_total(client, 790001) == before, "план не меняет расход"

    fact = client.post("/webapp/tx/save", headers=headers, json={
        "tx_type": "expense", "amount": 5000, "category": "Реквизит и костюмы",
        "tx_date": "2026-10-15", "is_plan": False, "idempotency_key": "test:fact:1"}).json()
    assert fact.get("ok") is True, fact
    assert _expense_total(client, 790001) == before + 5000


def test_форма_операции_отдаётся(client) -> None:
    page = client.get("/webapp/tx").text
    assert "Расход" in page and "План или факт" in page


def test_чужой_телефон_маскируется_без_права_на_контакт(client) -> None:
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        owner = _person(conn, 790030, "Владелец Телефона", role="troupe")
        reader = _person(conn, 790031, "Читатель Без Права", role="troupe")
        conn.execute("insert into participant_capability_override "
                     "(participant_id, capability, allowed, reason) "
                     "values (%s,'participants.view',true,'тест')", (reader,))
        conn.commit()
    info = client.get(f"/webapp/person/info?id={owner}", headers=_token(client, 790031)).json()
    assert "·" in (info["data"]["phone"] or ""), "без права номер приходит маской"


def test_организатор_видит_профиль_и_меняет_тариф(client) -> None:
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 790010, "Орг Профилей")
        target = _person(conn, 790011, "Участник С Тарифом", role="troupe")
    headers = _token(client, 790010)

    info = client.get(f"/webapp/person/info?id={target}", headers=headers).json()
    assert info["ok"] is True
    assert info["data"]["display_name"] == "Участник С Тарифом"
    # организатору номер нужен по работе: у роли есть `participants.contact`
    assert info["data"]["phone"], "организатор видит номер"

    saved = client.post("/webapp/person/tariff", headers=headers,
                        json={"participant_id": target, "tariff": 1500}).json()
    assert saved["ok"] is True
    with psycopg.connect(_test_dsn()) as conn:
        tariff = conn.execute("select dues_tariff from participant where id = %s",
                              (target,)).fetchone()[0]
    assert float(tariff) == 1500


def test_страница_профиля_открывается(client) -> None:
    page = client.get("/webapp/person?id=1").text
    assert "Взнос" in page and "Сохранить тариф" in page


def test_поиск_окон_отдаёт_результат_с_пояснениями(client) -> None:
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        _person(conn, 790020, "Орг Окон")
        first = _person(conn, 790021, "Первый Свободный", role="troupe")
        second = _person(conn, 790022, "Второй Свободный", role="troupe")
        for pid in (first, second):
            for day in ("2026-11-03", "2026-11-04"):
                for hour in range(8, 23):
                    conn.execute(
                        "insert into availability_hour (participant_id, day, hour, is_free, kind, source) "
                        "values (%s,%s,%s,true,'free','bot') on conflict do nothing",
                        (pid, day, hour))
        conn.commit()
    headers = _token(client, 790020)

    people = client.get("/webapp/slots/people", headers=headers).json()
    assert any(p["id"] == first for p in people["people"])

    found = client.post("/webapp/slots/find", headers=headers, json={
        "date_from": "2026-11-03", "date_to": "2026-11-04", "min_hours": 2,
        "participants": [first], "optional": [second]}).json()
    assert found["ok"] is True
    assert found["windows"], "окна должны найтись: оба свободны весь день"
    assert found["text"], "пояснения к окнам приходят вместе с результатом"
