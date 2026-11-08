"""Наблюдаемость агента: шаги и инструменты в intent_log и экран разбора (A-5, docs/development/108 §6).

Обоснование: до этой карты по ответу агента был виден только текст. Проверяем ровно
критерии приёмки A-5:
  * вопрос в боте → в журнале шаги, инструменты, время и цена;
  * экран в админке отдаёт последние сессии и шаги, право проверено (`admin.panel`);
  * чистка на 90 дней удаляет старые записи вместе с шагами;
  * в аргументах записи нет токенов и паролей.

Карта AUD-10 (`t_4a9705ea`, FR-E10 / INT-010, docs/reports/119 §9 п.4–5, §10 п.4–5)
добавляет две вещи, без которых журнал не считался по факту: `intent_log.degraded_reason`
(причина отказа рядом с отказом) и настоящий расход в `llm_usage` — раздельные
`tokens_in` / `tokens_out` и `cost_rub` по ценам `config/agent-models.yaml`, а у модели
без цены — `NULL` и `price_status = 'no_price'`.
"""

from __future__ import annotations

import datetime
import json

import intent_report
import psycopg
import pytest
from app import llm
from app.agently import loop, models
from psycopg.rows import dict_row

from tests.conftest import _test_dsn
from tests.integration.test_webapp_profile import _api, init_data


def _token(client, chat_id: int) -> dict:
    token = client.post("/webapp/auth", json={"init_data": init_data(chat_id)}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _rows():
    """Все строки журнала: читаем базу, а не ответ эндпоинта."""
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute("select * from intent_log order by id").fetchall()


def _fake_run(result: dict):
    async def run(conn, who, text, **kwargs):
        return result

    return run


def test_вопрос_пишется_в_журнал_с_шагами_инструментами_и_временем(client, auth, monkeypatch):
    """Given вопрос в боте, when разбор, then в журнале есть шаги, инструменты и время."""
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": True, "answer": "Сегодня 01.11.2026.", "scenario": "assistant",
        "steps": [{"step": 1, "tool": "now", "args": {"tz": "Samara"},
                   "ok": True, "error": None, "latency_ms": 12}],
        "step_count": 1, "tokens": 210, "model": "test", "propose": None}))

    body = client.post("/tools/agent/ask", json={"text": "какой сегодня день"},
                       headers=auth).json()

    assert body["engine"] == "agent"
    row = _rows()[-1]
    assert row["agent_status"] == "answer"
    assert row["text"] == "какой сегодня день"
    assert row["answer"] == "Сегодня 01.11.2026."
    assert row["step_count"] == 1 and row["tokens"] == 210
    assert row["latency_ms"] is not None and row["ts"] is not None
    steps = row["steps"]
    assert steps[0]["tool"] == "now" and steps[0]["latency_ms"] == 12
    assert steps[0]["args"] == {"tz": "Samara"}


def test_предложение_отмечается_отдельным_статусом(client, auth, monkeypatch):
    """Просьба записать расход — «предложение», а не «ответ»: разбор их различает."""
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": True, "answer": "Предлагаю записать расход.", "scenario": "budget",
        "steps": [{"step": 1, "tool": "propose_budget_tx",
                   "args": {"amount": 1200, "purpose": "ткань"}, "ok": True,
                   "error": None, "latency_ms": 20}],
        "step_count": 1, "tokens": 300, "model": "test",
        "propose": {"tool": "budget.tx", "args": {"amount": 1200}, "human_text": "Записать расход"}}))

    client.post("/tools/agent/ask", json={"text": "купили ткань 1200"}, headers=auth)

    assert _rows()[-1]["agent_status"] == "proposal"


def test_деградация_к_правилам_тоже_попадает_в_журнал(client, auth, monkeypatch):
    """Отказ агента виден в разборе: иначе половина сессий исчезает из журнала."""
    monkeypatch.setattr(llm, "is_configured", lambda: False)

    body = client.post("/tools/agent/ask", json={"text": "какой сегодня день"},
                       headers=auth).json()

    assert body["engine"] == "rules"
    row = _rows()[-1]
    assert row["agent_status"] == "refusal"
    assert row["source"] == "rules"
    assert row["step_count"] == 0 and row["steps"] == []
    assert row["answer"], "ответ по правилам тоже нужен разбору"


def test_аргументы_в_журнале_без_токенов_и_паролей(client, auth, monkeypatch):
    """Given запись, when чтение, then в аргументах нет токенов и паролей."""
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": True, "answer": "Готово.", "scenario": "assistant",
        "steps": [{"step": 1, "tool": "read_dues",
                   "args": {"participant_id": 42, "token": "sk-live-777",
                            "password": "qwerty", "period": "2026-11"},
                   "ok": True, "error": None, "latency_ms": 7}],
        "step_count": 1, "tokens": 100, "model": "test", "propose": None}))

    client.post("/tools/agent/ask", json={"text": "мои взносы"}, headers=auth)

    dumped = json.dumps(_rows()[-1]["steps"], ensure_ascii=False)
    assert "sk-live-777" not in dumped and "qwerty" not in dumped
    assert "2026-11" in dumped, "обычные аргументы остаются: по ним виден вызов"
    assert "read_dues" in dumped


def test_экран_разбора_только_с_правом_admin_panel(client):
    """Given экран в админке, when открытие, then видно сессии и шаги, право проверено."""
    _api()
    with psycopg.connect(_test_dsn()) as conn:
        admin_id = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,'admin','active') returning id",
            (783001, "Разбор Админ", "разбор-админ")).fetchone()[0]
        troupe_id = conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,%s,%s,'troupe','active') returning id",
            (783002, "Разбор Труппа", "разбор-труппа")).fetchone()[0]
        conn.execute("insert into participant_role (participant_id, role) values (%s,'admin')",
                     (admin_id,))
        conn.execute("insert into participant_role (participant_id, role) values (%s,'troupe')",
                     (troupe_id,))
        conn.execute("""
            insert into intent_log (text, answer, source, agent_status, step_count,
                                    tokens, latency_ms, steps)
            values ('сколько денег в бюджете', 'В ноябре пришло 120 тыс.', 'llm', 'answer',
                    1, 250, 1800,
                    '[{"step": 1, "tool": "read_budget", "args": {"period": "2026-11"},'
                    ' "ok": true, "error": null, "latency_ms": 15}]')""")

    admin = client.get("/webapp/admin/agent", headers=_token(client, 783001))
    assert admin.status_code == 200
    body = admin.json()
    assert body["total"] == 1
    session = body["sessions"][0]
    assert session["question"] == "сколько денег в бюджете"
    assert session["answer"] == "В ноябре пришло 120 тыс."
    assert session["status"] == "answer"
    assert session["steps"][0]["tool"] == "read_budget"
    assert session["latency_ms"] == 1800

    troupe = client.get("/webapp/admin/agent", headers=_token(client, 783002))
    assert troupe.status_code == 403, "журнал содержит вопросы людей: не для всех"

    page = client.get("/webapp/admin/t/agent")
    assert page.status_code == 200 and "Разбор агента" in page.text


def test_чистка_90_дней_удаляет_старые_записи_вместе_с_шагами(client, monkeypatch):
    """Given 90 дней, when чистка, then старые записи удалены (как у intent_log)."""
    old_ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=91)
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("""
            insert into intent_log (ts, text, source, agent_status, step_count, steps)
            values (%s, 'старый вопрос', 'llm', 'answer', 1, '[{"step": 1, "tool": "now"}]')""",
                     (old_ts,))
        conn.execute("""
            insert into intent_log (text, source, agent_status, step_count, steps)
            values ('свежий вопрос', 'llm', 'answer', 1, '[{"step": 1, "tool": "now"}]')""")

    monkeypatch.setattr(intent_report, "DSN", _test_dsn())
    intent_report.purge()

    texts = [row["text"] for row in _rows()]
    assert "старый вопрос" not in texts
    assert "свежий вопрос" in texts


def test_отчёт_каскада_не_считает_сессии_агента(client, monkeypatch, capsys):
    """Given сессия агента, when отчёт по каскаду, then она в счётчики не попадает.

    Агент пишет в ту же `intent_log` (`source='llm'` — ответ, `source='rules'` —
    деградация к правилам), но категорий не выбирает. Без фильтра каждая деградация
    поднимала «правила разобрали» и «потеряно» — очередь доработки словаря врала.
    """
    with psycopg.connect(_test_dsn()) as conn:
        conn.execute("delete from intent_log")
        # каскад разбора: одну строку разобрала модель, одну спросили у человека
        conn.execute("insert into intent_log (text, source, resolved)"
                     " values ('записала сама', 'llm', true)")
        conn.execute("insert into intent_log (text, source, resolved)"
                     " values ('не знаю такую трату', 'buttons', false)")
        # сессии агента: ответ, деградация к правилам, отказ модели
        conn.execute("insert into intent_log (text, source, resolved, agent_status)"
                     " values ('какой сегодня день', 'llm', true, 'answer')")
        conn.execute("insert into intent_log (text, source, resolved, agent_status)"
                     " values ('купили ткань', 'rules', false, 'refusal')")
        conn.execute("insert into intent_log (text, source, resolved, agent_status)"
                     " values ('что по взносам', 'llm', false, 'refusal')")

    monkeypatch.setattr(intent_report, "DSN", _test_dsn())
    intent_report.show_gaps(7)
    out = capsys.readouterr().out

    assert "всего 2, правила 0 (0%), модель 1, человек 1, потеряно 1" in out, out
    # сессии агента не должны протекать и в «топ фраз, где правила молчат»
    assert "купили ткань" not in out and "что по взносам" not in out


# --------------------------------- AUD-10: причина отказа и настоящая стоимость


def _usage_rows() -> list[dict]:
    """Строки учёта расхода: читаем базу, а не ответ эндпоинта."""
    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        return conn.execute("select * from llm_usage order by id").fetchall()


def test_причина_деградации_пишется_в_журнал_а_не_только_в_аудит(client, auth,
                                                                monkeypatch):
    """AUD-10 (FR-E10, INT-010): причина отказа лежит рядом с отказом.

    Обоснование: карта `t_4a9705ea`, docs/reports/119 §9 п.4 — до неё `degraded.reason`
    была только в `audit_event.after`, и журнал разбора не отвечал на вопрос «почему».
    """
    monkeypatch.setattr(llm, "is_configured", lambda: False)

    client.post("/tools/agent/ask", json={"text": "какой сегодня день"}, headers=auth)

    row = _rows()[-1]
    assert row["agent_status"] == "refusal"
    assert row["degraded_reason"] == "llm_unavailable"


def test_отказы_по_провайдеру_и_бюджету_считаются_одним_запросом(client, auth,
                                                                monkeypatch):
    """AUD-10: «сколько из-за провайдера, сколько из-за бюджета» — один select.

    Второй рубеж критерия: причина не только записана, но и объяснена видом
    (`v_intent_degraded`), поэтому запросу не нужно знать список причин наизусть.
    """
    chat_id = 770001
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    client.post("/tools/agent/ask", json={"text": "какой сегодня день"}, headers=auth)

    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute("insert into intent_log (chat_id, text, source, resolved, tokens,"
                     " step_count, agent_status)"
                     " values (%s,'раньше','llm',true,60000,3,'answer')", (chat_id,))
    body = client.post("/tools/agent/ask",
                       json={"text": "какой сегодня день", "chat_id": chat_id},
                       headers=auth).json()
    assert body["degraded"]["reason"] == "chat_tokens"

    assert [row["degraded_reason"] for row in _rows()
            if row["agent_status"] == "refusal"] == ["llm_unavailable", "chat_tokens"]

    with psycopg.connect(_test_dsn()) as conn:
        counts = dict(conn.execute(
            "select вид, sum(отказов) from v_intent_degraded group by вид").fetchall())
    assert counts == {"провайдер": 1, "бюджет": 1}, counts


def test_раздельные_токены_и_рубли_считаются_по_конфигу(client, auth, monkeypatch):
    """AUD-10: вход и выход раздельно, `cost_rub` — по ценам из конфига.

    Сумма сверяется с ручным расчётом по тем же цифрам `config/agent-models.yaml`:
    тест проверяет равенство арифметике, а не «число больше нуля».
    """
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": True, "answer": "Сегодня 01.11.2026.", "scenario": "assistant",
        "steps": [], "step_count": 1, "tokens": 1000, "tokens_in": 800,
        "tokens_out": 200, "model": "openai/gpt-4o-mini", "propose": None}))

    client.post("/tools/agent/ask", json={"text": "какой сегодня день"}, headers=auth)

    usage = _usage_rows()[-1]
    price_in, price_out = models.prices()["openai/gpt-4o-mini"]
    rate = models.rates()["usd_rub"]
    expected = round((800 * price_in + 200 * price_out) / 1_000_000 * rate, 4)

    assert usage["tokens_in"] == 800 and usage["tokens_out"] == 200
    assert usage["price_status"] == "known"
    assert expected > 0, "цена модели обязана быть в конфиге, иначе тест проверяет ноль"
    assert float(usage["cost_rub"]) == pytest.approx(expected)


def test_неизвестная_модель_это_нет_цены_а_не_ноль(client, auth, monkeypatch):
    """AUD-10: у модели без цены в конфиге — NULL и `no_price`, а не ноль рублей.

    Ноль означал бы «посчитали, вышло даром» и молча занижал бы сумму расходов.
    """
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": True, "answer": "Сегодня 01.11.2026.", "scenario": "assistant",
        "steps": [], "step_count": 1, "tokens": 900, "tokens_in": 700,
        "tokens_out": 200, "model": "deepseek-v4.1-flash", "propose": None}))

    client.post("/tools/agent/ask", json={"text": "какой сегодня день"}, headers=auth)

    usage = _usage_rows()[-1]
    assert usage["tokens_in"] == 700 and usage["tokens_out"] == 200
    assert usage["cost_rub"] is None, "неизвестная цена — NULL, а не 0"
    assert usage["price_status"] == "no_price"


def test_деградация_с_потраченными_токенами_тоже_стоит_денег(client, auth, monkeypatch):
    """AUD-10: правила ответили, но шаги уже потратили токены — это расход.

    Таймаут и бюджет запроса останавливают цикл после вызова модели: без строки в
    `llm_usage` такие сессии были бы бесплатными в отчёте и невидимыми в квоте.
    """
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": False, "error": "token_budget", "scenario": "assistant",
        "steps": [{"step": 1, "tool": "now", "args": {}, "ok": True, "error": None,
                   "latency_ms": 9}],
        "step_count": 1, "tokens": 500, "tokens_in": 400, "tokens_out": 100,
        "model": "openai/gpt-4o-mini"}))

    body = client.post("/tools/agent/ask", json={"text": "какой сегодня день"},
                       headers=auth).json()

    assert body["engine"] == "rules" and body["degraded"]["reason"] == "token_budget"
    assert body["tokens"] == 500 and body["tokens_in"] == 400
    row = _rows()[-1]
    assert row["agent_status"] == "refusal" and row["degraded_reason"] == "token_budget"

    usage = _usage_rows()[-1]
    assert usage["tokens_in"] == 400 and usage["tokens_out"] == 100
    assert usage["price_status"] == "known" and float(usage["cost_rub"]) > 0



def test_рамка_задачи_помечается_в_ответе_и_в_расходе(client, auth, monkeypatch):
    """«Озадачить»: сценарий `task` в ответе и в `llm_usage`, а не `agent`.

    Иначе по журналу не отличить задачу от обычного вопроса, и стоимость задач
    растворяется в общем расходе (решение 05.11.2026).
    """
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", _fake_run({
        "ok": True, "answer": "Шаг 1: проверить зал.", "scenario": "task",
        "steps": [], "step_count": 1, "tokens": 1200, "tokens_in": 900,
        "tokens_out": 300, "model": "openai/gpt-4o-mini", "propose": None}))

    body = client.post("/tools/agent/ask",
                       json={"text": "подготовь смету", "task": True},
                       headers=auth).json()

    assert body["scenario"] == "task"
    usage = _usage_rows()[-1]
    assert usage["intent"] == "task", "задача считается отдельным сценарием"
    assert usage["tokens_in"] == 900 and usage["tokens_out"] == 300
