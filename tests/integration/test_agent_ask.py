"""`POST /tools/agent/ask` и цикл агента (A-1, docs/development/108).

Сеть не нужна: провайдер подменяется транспортом с готовыми ответами `tool_calls`.
Проверяем именно наше поведение, а не модель:
  * дата приходит из инструмента `now`, а не из памяти модели;
  * просьба записать расход — это предложение, и в базе до кнопки ничего не меняется;
  * инструмент без права не вызывается и не отдаёт данные;
  * лимит шагов, бюджет токенов и недоступность провайдера не превращаются в догадку;
  * сбой модели — ответ по правилам с понятной причиной.
"""

from __future__ import annotations

import asyncio
import json

import psycopg
import pytest
from app import core, llm
from app.agently import budget, cache, features, loop
from app.agently import registry as tool_registry
from psycopg.rows import dict_row


@pytest.fixture()
def conn(test_db: str):
    with psycopg.connect(test_db, row_factory=dict_row) as connection:
        yield connection


def _person(conn, chat_id: int, role: str = "troupe") -> dict:
    row = conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status)"
        " values (%s,%s,%s,%s,'active') returning id, display_name, role, status",
        (chat_id, f"Человек {chat_id}", f"человек-{chat_id}", role)).fetchone()
    conn.execute("insert into participant_role (participant_id, role) values (%s,%s)"
                 " on conflict do nothing", (row["id"], role))
    return dict(row) | {"roles": [role], "id": row["id"]}


def _expense_category(conn) -> str:
    return conn.execute(
        "select name from category where direction = 'expense' order by name limit 1"
    ).fetchone()["name"]


def _tool_call(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}],
        "usage": {"total_tokens": 10}}


def _answer(text: str, tokens: int = 5) -> dict:
    return {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": tokens}}


def _transport(*answers, capture: list | None = None):
    state = {"index": 0}

    def transport(payload):
        if capture is not None:
            capture.append(payload)
        index = min(state["index"], len(answers) - 1)
        state["index"] += 1
        answer = answers[index]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return transport


@pytest.fixture(autouse=True)
def _no_classifier(monkeypatch):
    """Классификатор — отдельный вызов модели; в тестах цикла он не нужен."""
    monkeypatch.setenv("AGENT_CLASSIFIER", "0")


def test_дата_берётся_из_инструмента_now(conn) -> None:
    who = _person(conn, 562001)
    payloads: list = []
    transport = _transport(_tool_call("now", {}),
                           _answer("Сегодня по Самаре."), capture=payloads)

    result = asyncio.run(loop.run(conn, who, "какой сегодня день", transport=transport))

    assert result["ok"] is True
    assert result["steps"][0]["tool"] == "now" and result["steps"][0]["ok"] is True
    today = core.now().date().isoformat()
    tool_messages = [message for message in payloads[-1]["messages"] if message["role"] == "tool"]
    assert tool_messages, "результат now должен вернуться модели"
    assert json.loads(tool_messages[0]["content"])["date"] == today
    # Правило: серверная дата стоит в системном промпте (в формате ДД.ММ.ГГГГ), чтобы модель
    # не достраивала год из памяти, но факт для ответа всё равно берётся вызовом `now`
    # (проверено выше по steps). Машиночитаемая дата ISO приходит только из результата инструмента.
    system_prompt_text = payloads[0]["messages"][0]["content"]
    assert core.now().strftime("%d.%m.%Y") in system_prompt_text
    assert today not in system_prompt_text


def test_просьба_записать_расход_это_предложение_и_база_не_меняется(conn) -> None:
    who = _person(conn, 562002, "admin") | {"caps": ["*"]}
    category = _expense_category(conn)
    before = conn.execute("select count(*) as n from budget_tx").fetchone()["n"]
    transport = _transport(
        _tool_call("propose_budget_tx", {"tx_type": "expense", "amount": 12000,
                                         "category": category, "purpose": "ткань"}),
        _answer("Предлагаю записать расход, подтвердите кнопкой."))

    result = asyncio.run(loop.run(conn, who, "купили ткань 12000", transport=transport))

    assert result["ok"] is True
    assert result["propose"]["tool"] == "budget.tx"
    assert result["propose"]["args"]["amount"] == 12000
    assert result["propose"]["human_text"].startswith("Записать расход")
    assert conn.execute("select count(*) as n from budget_tx").fetchone()["n"] == before


def test_инструмент_без_права_не_отдаёт_данные(conn) -> None:
    who = _person(conn, 562003) | {"caps": ["schedule.availability.self"]}
    transport = _transport(_tool_call("read_budget", {}), _answer("Данных у меня нет."))

    result = asyncio.run(loop.run(conn, who, "покажи бюджет", transport=transport))

    assert result["steps"][0]["ok"] is False
    assert result["steps"][0]["error"] == "forbidden"
    assert "read_budget" not in [tool.name for tool in tool_registry.available_tools(who)]


def test_лимит_шагов_это_отказ_а_не_догадка(conn) -> None:
    who = _person(conn, 562004)
    transport = _transport(_tool_call("now", {}))

    result = asyncio.run(loop.run(conn, who, "ищи", max_steps=3, transport=transport))

    assert result["ok"] is False and result["error"] == "step_limit"
    assert result["step_count"] == 3


def test_бюджет_токенов_останавливает_цикл(conn) -> None:
    who = _person(conn, 562005)
    transport = _transport(_tool_call("now", {}) | {"usage": {"total_tokens": 9000}})

    result = asyncio.run(loop.run(conn, who, "сколько времени", token_budget=5000,
                                  transport=transport))

    assert result["ok"] is False and result["error"] == "token_budget"


def test_недоступность_провайдера_это_modelunavailable(conn) -> None:
    who = _person(conn, 562006)
    transport = _transport(loop.ModelUnavailable("llm_error"))

    with pytest.raises(loop.ModelUnavailable):
        asyncio.run(loop.run(conn, who, "привет", transport=transport))


def test_классификатор_идёт_без_инструментов(conn, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_CLASSIFIER", "1")
    who = _person(conn, 562007)
    payloads: list = []
    transport = _transport(_answer("ASSISTANT"), _answer("Ответ без инструментов."),
                           capture=payloads)

    result = asyncio.run(loop.run(conn, who, "как дела", transport=transport))

    assert result["ok"] is True
    assert "tools" not in payloads[0], "классификатор не должен получать инструменты"
    assert payloads[0]["max_tokens"] == 4
    assert "tools" in payloads[1], "агентный шаг обязан видеть реестр"


def test_реестр_только_чтение_и_со_схемами() -> None:
    for tool in tool_registry.all_tools():
        assert tool.read_only is True, f"{tool.name} объявлен пишущим"
        assert tool.json_schema["type"] == "object"
        assert tool.description


def test_маршрут_агента_есть_в_схеме(client, auth) -> None:
    from app.main import app  # noqa: PLC0415 — приложение собирается в фикстуре

    assert "/tools/agent/ask" in app.openapi()["paths"]


def test_без_ключа_отвечают_правила_с_датой(client, auth, monkeypatch) -> None:
    monkeypatch.setattr(llm, "is_configured", lambda: False)

    body = client.post("/tools/agent/ask", json={"text": "какой сегодня день"},
                       headers=auth).json()

    assert body["ok"] is True and body["engine"] == "rules"
    assert body["degraded"]["reason"] == "llm_unavailable"
    assert core.now().strftime("%d.%m.%Y") in body["answer"]


def test_сбой_провайдера_даёт_ответ_по_правилам(client, auth, monkeypatch) -> None:
    async def broken(*args, **kwargs):
        raise loop.ModelUnavailable("provider_unavailable")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", broken)

    body = client.post("/tools/agent/ask", json={"text": "привет"}, headers=auth).json()

    assert body["ok"] is True and body["engine"] == "rules"
    assert body["degraded"]["reason"] == "provider_unavailable"
    assert body["answer"]


def test_успешный_агентный_ответ_идёт_с_шагами(client, auth, monkeypatch) -> None:
    async def fake_run(conn, who, text, **kwargs):
        return {"ok": True, "answer": "Сегодня 1 ноября.", "scenario": "assistant",
                "steps": [{"step": 1, "tool": "now", "ok": True, "latency_ms": 3}],
                "step_count": 1, "tokens": 0, "model": "test", "propose": None}

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", fake_run)

    body = client.post("/tools/agent/ask", json={"text": "какой сегодня день"},
                       headers=auth).json()

    assert body["ok"] is True and body["engine"] == "agent"
    assert body["steps"][0]["tool"] == "now"
    assert "latency_ms" in body
    assert "now" in body["tools_available"]


def test_пустой_текст_не_тратит_модель(client, auth, monkeypatch) -> None:
    async def fake_run(*args, **kwargs):
        raise AssertionError("модель не должна вызываться на пустом тексте")

    monkeypatch.setattr(loop, "run", fake_run)

    body = client.post("/tools/agent/ask", json={"text": "   "}, headers=auth).json()
    assert body["ok"] is False and body["error"] == "empty_text"


def test_вопрос_о_возможностях_отвечает_реестром_без_модели(conn) -> None:
    """«Что можно записать/посмотреть» — детерминированный ответ из реестра функций."""
    who = _person(conn, 562101, "admin") | {"caps": ["*"]}

    def forbidden(*args, **kwargs):
        raise AssertionError("вопрос о возможностях не должен доходить до модели")

    result = asyncio.run(loop.run(conn, who, "что можно записать?", transport=forbidden))

    assert result["ok"] is True and result["engine"] == "rules"
    assert result["steps"] == [] and result["step_count"] == 0
    assert result["propose"] is None and result["tokens"] == 0
    assert result["answer"] == features.human_capabilities(who)
    assert "Записать" in result["answer"] and "Посмотреть" in result["answer"]


def test_вопрос_о_возможностях_у_актёра_без_бюджета(conn) -> None:
    """Список действий — по правам человека, а не «что вообще бывает в студии»."""
    who = _person(conn, 562102) | {"caps": ["budget.read.self", "schedule.availability.self"]}

    result = asyncio.run(loop.run(conn, who, "что ты умеешь?", transport=None))

    assert result["engine"] == "rules"
    assert "«Мои взносы»" in result["answer"]
    assert "«Бюджет»" not in result["answer"]
    assert "Записать" not in result["answer"]


def test_перечень_действий_попадает_в_системный_промпт(conn) -> None:
    """Модель видит закрытый список действий этого человека, а не весь реестр."""
    who = _person(conn, 562103) | {"caps": ["budget.read.self", "schedule.availability.self"]}
    payloads: list = []
    transport = _transport(_answer("Привет."), capture=payloads)

    asyncio.run(loop.run(conn, who, "привет", transport=transport))

    system_prompt_text = payloads[0]["messages"][0]["content"]
    assert "Доступные тебе действия" in system_prompt_text
    assert "«Расписание»" in system_prompt_text and "«Мои взносы»" in system_prompt_text
    assert "«Бюджет»" not in system_prompt_text


# --------------------------------------------------- бюджет чата и кэш (карта A-4)


def _spend(conn, chat_id: int, *, tokens: int, steps: int) -> None:
    """Положить в журнал агентную сессию этого чата — как будто человек уже спрашивал."""
    conn.execute(
        "insert into intent_log (chat_id, text, source, resolved, tokens, latency_ms,"
        " step_count, agent_status) values (%s,'раньше','llm',true,%s,10,%s,'answer')",
        (chat_id, tokens, steps))
    conn.commit()


def test_исчерпанный_бюджет_чата_отвечает_по_правилам_с_подсказкой(
        client, auth, conn) -> None:
    """Лимит исчерпан — короткий ответ по правилам, HTTP 200 и одна строка «по кнопкам»."""
    _spend(conn, 123456789, tokens=60000, steps=5)

    response = client.post("/tools/agent/ask",
                           json={"text": "какой сегодня день", "chat_id": 123456789},
                           headers=auth)

    assert response.status_code == 200, "исчерпанный бюджет — не ошибка сервера"
    body = response.json()
    assert body["ok"] is True
    assert body["engine"] == "rules"
    assert body["degraded"]["reason"] == "chat_tokens"
    assert body["tokens"] == 0
    assert core.now().strftime("%d.%m.%Y") in body["answer"]
    assert body["answer"].rstrip().endswith(budget.EXHAUSTED_HINT)
    assert body["answer"].count(budget.EXHAUSTED_HINT) == 1
    assert body["degraded"]["budget"]["daily_tokens"] == 60000


def test_исчерпанные_шаги_закрывают_бюджет(client, auth, conn) -> None:
    _spend(conn, 123456789, tokens=100, steps=100)

    body = client.post("/tools/agent/ask",
                       json={"text": "какой сегодня день", "chat_id": 123456789},
                       headers=auth).json()

    assert body["engine"] == "rules"
    assert body["degraded"]["reason"] == "chat_steps"


def test_бюджет_одного_чата_не_трогает_другой(client, auth, conn, monkeypatch) -> None:
    """Расход соседнего чата не должен отнимать ответ у этого."""
    _spend(conn, 999999, tokens=60000, steps=100)

    async def fake_run(inner_conn, who, text, **kwargs):
        return {"ok": True, "answer": "Сегодня 02.11.2026.", "scenario": "assistant",
                "steps": [], "step_count": 1, "tokens": 10, "model": "test",
                "propose": None}

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", fake_run)

    body = client.post("/tools/agent/ask",
                       json={"text": "какой сегодня день", "chat_id": 123456789},
                       headers=auth).json()

    assert body["engine"] == "agent"


def test_повторный_вопрос_идёт_из_кэша(client, auth, monkeypatch) -> None:
    """Один и тот же вопрос дважды в пределах TTL: второй раз модели нет."""
    calls = {"count": 0}

    async def fake_run(inner_conn, who, text, **kwargs):
        calls["count"] += 1
        return {"ok": True, "answer": "Сегодня 02.11.2026, понедельник.",
                "scenario": "assistant", "steps": [], "step_count": 1, "tokens": 120,
                "model": "openai/gpt-4o-mini", "propose": None}

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", fake_run)

    payload = {"text": "какое сегодня число", "chat_id": 123456789}
    first = client.post("/tools/agent/ask", json=payload, headers=auth).json()
    second = client.post("/tools/agent/ask", json=payload, headers=auth).json()

    assert first["engine"] == "agent"
    assert second["engine"] == "cache" and second["cached"] is True
    assert second["answer"] == first["answer"]
    assert second["tokens"] == 0 and second["step_count"] == 0
    assert calls["count"] == 1, "второй ответ не должен звать модель"


def test_попадание_в_кэш_видно_в_журнале(client, auth, monkeypatch) -> None:
    async def fake_run(inner_conn, who, text, **kwargs):
        return {"ok": True, "answer": "Сегодня 02.11.2026.", "scenario": "assistant",
                "steps": [], "step_count": 1, "tokens": 120, "model": "test",
                "propose": None}

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", fake_run)

    payload = {"text": "какое сегодня число", "chat_id": 123456789}
    client.post("/tools/agent/ask", json=payload, headers=auth)
    client.post("/tools/agent/ask", json=payload, headers=auth)

    with core.pool.connection() as pool_conn:
        rows = pool_conn.execute(
            "select source, tokens, step_count from intent_log"
            " where chat_id = 123456789 order by id").fetchall()

    assert [row["source"] for row in rows] == ["llm", "cache"]
    assert rows[1]["tokens"] == 0 and rows[1]["step_count"] == 0


def test_кэш_не_мешает_карточке_подтверждения(client, auth, monkeypatch) -> None:
    """Ответ с предложением записи не кэшируется: черновик не повторяется молча."""
    calls = {"count": 0}

    async def fake_run(inner_conn, who, text, **kwargs):
        calls["count"] += 1
        return {"ok": True, "answer": "Похоже на расход 3000 ₽.", "scenario": "budget",
                "steps": [], "step_count": 1, "tokens": 50, "model": "test",
                "propose": {"tool": "budget.tx", "args": {}, "human_text": "Расход 3000"}}

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", fake_run)

    payload = {"text": "какое сегодня число", "chat_id": 123456789}
    first = client.post("/tools/agent/ask", json=payload, headers=auth).json()
    second = client.post("/tools/agent/ask", json=payload, headers=auth).json()

    assert first["propose"] and second["propose"]
    assert second["engine"] == "agent"
    assert calls["count"] == 2


def test_вопрос_вне_групп_кэша_спрашивает_модель_снова(client, auth, monkeypatch) -> None:
    calls = {"count": 0}

    async def fake_run(inner_conn, who, text, **kwargs):
        calls["count"] += 1
        return {"ok": True, "answer": "В октябре 210 998 ₽.", "scenario": "budget",
                "steps": [], "step_count": 2, "tokens": 500, "model": "test",
                "propose": None}

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(loop, "run", fake_run)

    payload = {"text": "сколько расходов в октябре", "chat_id": 123456789}
    client.post("/tools/agent/ask", json=payload, headers=auth)
    client.post("/tools/agent/ask", json=payload, headers=auth)

    assert calls["count"] == 2, "бюджет меняется: такие ответы не кэшируются"
    assert cache.stats()["entries"] == 0


def test_рамка_задачи_добавляет_строку_в_подсказку(conn) -> None:
    """«Озадачить»: просьба про шаги в системной подсказке, сценарий `task`.

    Рамка меняет формулировку задачи, а не права и не набор инструментов, и
    классификатор не зовётся: намерение уже названо кнопкой (решение 05.11.2026).
    """
    who = _person(conn, 562010)
    payloads: list = []
    transport = _transport(_answer("Шаг 1: проверить зал. Шаг 2: записать операцию."),
                           capture=payloads)

    result = asyncio.run(loop.run(conn, who, "подготовь смету", transport=transport,
                                  task=True))

    assert result["ok"] is True and result["scenario"] == "task"
    system = payloads[0]["messages"][0]["content"]
    assert "предложи шаги" in system
    assert len(payloads) == 1, "второй разбор (классификатор) — лишний расход"


def test_без_рамки_подсказка_не_просит_шагов(conn) -> None:
    """Обычный вопрос работает как раньше (Ш-12): строки про задачу в промпте нет."""
    who = _person(conn, 562011)
    payloads: list = []
    transport = _transport(_answer("Пока не умею."), capture=payloads)

    result = asyncio.run(loop.run(conn, who, "привет", transport=transport))

    assert result["scenario"] != "task"
    assert "предложи шаги" not in payloads[0]["messages"][0]["content"]
