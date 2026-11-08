"""Свободный текст в gateway идёт в агент tool-api, а не в одиночный вызов модели.

Проверяем клей, а не Telegram: `core.agent_ask` обязан отдавать ответ агента,
а на честный отказ (`ok:false`) — пустой словарь, чтобы обработчик ушёл на
маршрутизатор и кнопки, а не оставил человека без ответа (docs/development/108).
"""

from __future__ import annotations

import asyncio

import gateway_modules

core = gateway_modules.load("core")


def _run(monkeypatch, result: dict, text: str = "какой сегодня день") -> tuple[dict, dict]:
    calls: dict = {}

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        calls.update(path=path, payload=payload, chat_id=chat_id, timeout=timeout)
        return result

    monkeypatch.setattr(core, "api", fake_api)
    return asyncio.run(core.agent_ask(570001, text)), calls


def test_ответ_агента_возвращается_как_есть(monkeypatch) -> None:
    answer = {"ok": True, "engine": "agent", "answer": "Сегодня 1 ноября.",
              "steps": [{"tool": "now"}], "propose": None}

    got, calls = _run(monkeypatch, answer)

    assert got["answer"] == "Сегодня 1 ноября."
    assert calls["path"] == "/tools/agent/ask"
    assert calls["payload"]["text"] == "какой сегодня день"


def test_честный_отказ_это_пустой_словарь(monkeypatch) -> None:
    got, _ = _run(monkeypatch, {"ok": False, "error": "step_limit"})
    assert got == {}


def test_предложение_записи_доходит_до_бота(monkeypatch) -> None:
    answer = {"ok": True, "answer": "Подтвердите кнопкой.",
              "propose": {"tool": "budget.tx", "args": {"amount": 12000},
                          "human_text": "Записать расход"}}

    got, _ = _run(monkeypatch, answer, "купили ткань 12000")

    assert got["propose"]["tool"] == "budget.tx"


# --- видимая строка при деградации (возврат ревью A-4, 02.11.2026) -----------
menu = gateway_modules.load("routers.menu")

BUDGET_RESPONSE = {
    "ok": True, "engine": "rules", "degraded": {"reason": "chat_tokens"},
    "answer": "Сегодня завтра — среда.\nДальше — по кнопкам.",
    "propose": None, "steps": [], "step_count": 0, "tokens": 0, "model": None,
}


def test_исчерпанный_бюджет_не_даёт_ложной_строки_и_внутреннего_кода() -> None:
    """Критерий карты: короткий ответ по правилам и одна строка, без ошибки.

    Причина `chat_tokens` — не «модель недоступна»: gateway не должен добавлять
    пояснение и повторять подсказку; внутренний код человеку не показывается.
    """
    shown = menu.visible_answer(BUDGET_RESPONSE)

    assert shown == "Сегодня завтра — среда.\nДальше — по кнопкам."
    assert "недоступна" not in shown
    assert "chat_tokens" not in shown
    assert shown.count("по кнопкам") == 1


def test_исчерпанные_шаги_не_дают_ложной_строки() -> None:
    shown = menu.visible_answer({**BUDGET_RESPONSE, "degraded": {"reason": "chat_steps"}})

    assert "недоступна" not in shown
    assert "chat_steps" not in shown


def test_реальная_недоступность_модели_поясняется_по_русски() -> None:
    """Провайдер недоступен — это правда, и причина называется словами."""
    shown = menu.visible_answer({**BUDGET_RESPONSE, "degraded": {"reason": "llm_unavailable"}})

    assert "Модель сейчас недоступна — ответил по правилам (llm_unavailable)." in shown
    assert "chat_tokens" not in shown


def test_ответ_без_деградации_не_трогается() -> None:
    shown = menu.visible_answer({"answer": "Свободны: Аня.", "degraded": None})
    assert shown == "Свободны: Аня."

