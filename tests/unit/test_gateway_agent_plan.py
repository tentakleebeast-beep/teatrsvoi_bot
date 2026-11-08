"""Задача в чате: план, вход в Mini App и строка шагов (карта «Озадачить»).

Проверяем клей gateway, а не Telegram:
  * план рисуется одной карточкой со списком шагов и кнопкой «Выполнить план»;
  * после нажатия показывается, сколько шагов прошло и на каком остановились;
  * нехватка данных ведёт в Mini App (`kind: open`), а не в придуманную дату;
  * итог ответа несёт строку шагов «календарь → окна»;
  * задача отличается от вопроса по глаголу действия (`routing.is_task`).

Обоснование: FR-L2, FR-L5, docs/development/108 §8, решение владельца 05.11.2026.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest

routing = gateway_modules.load("routing")


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeMessage:
    def __init__(self, chat_id: int = 1):
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[tuple[str, object]] = []
        self.edits: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)


class FakeCallback:
    def __init__(self, data: str, chat_id: int = 1):
        self.data = data
        self.message = FakeMessage(chat_id)
        self.alerts: list[str] = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)


PLAN = {"steps": [
    {"tool": "propose_note", "args": {"body": "Позвонить Марку"},
     "human_text": "Создать личную заметку «Позвонить Марку»."},
    {"tool": "propose_event", "args": {"card": {"kind": "rehearsal"}},
     "human_text": "Назначить репетицию «Марк / Маша» на четверг 06.11 "
                   "18:00–20:00, зал «Студия», участники: Марк, Маша."},
]}

PLAN_CARD = {"ok": True, "token": "plan-tok", "ttl_seconds": 300, "plan": PLAN}


def _plan_card(gw, monkeypatch) -> tuple[FakeMessage, dict]:
    calls: dict = {}

    async def fake_plan(chat_id, plan):
        calls["plan"] = plan
        return PLAN_CARD

    async def fake_caps(chat_id):
        return ["*"]

    monkeypatch.setattr(gw, "agent_plan", fake_plan)
    monkeypatch.setattr(gw, "caps_of", fake_caps)
    message = FakeMessage()
    handled = asyncio.run(gw.show_agent_card(
        message, 1, {"ok": True, "answer": "Собрал план.", "plan": PLAN}))
    return message, {"handled": handled, **calls}


def test_план_рисуется_одной_карточкой_со_шагами(gw, monkeypatch) -> None:
    message, calls = _plan_card(gw, monkeypatch)

    assert calls["handled"] is True and calls["plan"] == PLAN
    text, markup = message.answers[-1]
    assert "План из" in text or "Агент собрал план" in text
    assert "1. Создать личную заметку" in text and "2. Назначить репетицию" in text
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert data == ["agp:ok:plan-tok", "agp:no:plan-tok"]


def test_без_токена_план_не_рисуется_а_ответ_остаётся(gw, monkeypatch) -> None:
    async def fake_plan(chat_id, plan):
        return {"ok": False, "error": "forbidden"}

    monkeypatch.setattr(gw, "agent_plan", fake_plan)
    message = FakeMessage()

    handled = asyncio.run(gw.show_agent_card(
        message, 1, {"ok": True, "answer": "Собрал план.", "plan": PLAN}))

    assert handled is True and message.answers[0][0] == "Собрал план."


def test_нехватка_данных_ведёт_в_приложение(gw, monkeypatch) -> None:
    monkeypatch.setattr(gateway_modules.load("routers.menu"), "WEBAPP_BASE_URL",
                        "https://teatr-svoi.online")
    proposal = {"kind": "open", "screen": "/webapp/assign",
                "for": "event", "text": "Заполните в приложении — я открою нужный экран"}
    message = FakeMessage()

    handled = asyncio.run(gw.show_agent_card(
        message, 1, {"ok": True, "answer": "", "propose": proposal}))

    assert handled is True
    text, markup = message.answers[-1]
    assert "Заполните в приложении" in text
    button = markup.inline_keyboard[0][0]
    assert button.text == "Открыть приложение"
    assert button.web_app.url == "https://teatr-svoi.online/webapp/assign"


def test_без_адреса_приложения_остаётся_текст(gw, monkeypatch) -> None:
    monkeypatch.setattr(gateway_modules.load("routers.menu"), "WEBAPP_BASE_URL", "")
    proposal = {"kind": "open", "screen": "/webapp/assign", "text": "Заполните в приложении"}

    message = FakeMessage()
    handled = asyncio.run(gw.show_agent_card(message, 1, {"propose": proposal}))

    assert handled is True and message.answers[0][1] is None


def test_открытие_экрана_не_требует_права_записи(gw, monkeypatch) -> None:
    """Вход в дом — не запись: его видит и человек без права записи."""
    monkeypatch.setattr(gw, "WEBAPP_BASE_URL", "https://example.test")
    monkeypatch.setattr(gw, "caps_of", lambda chat_id: _value([]))
    message = FakeMessage()

    handled = asyncio.run(gw.show_agent_card(
        message, 1, {"propose": {"kind": "open", "screen": "/webapp/week"}}))

    assert handled is True


async def _value(value):
    return value


def test_план_выполняется_кнопкой_и_показывает_частичный_успех(gw, monkeypatch) -> None:
    calls: dict = {}

    async def fake_confirm(chat_id, token):
        calls.update(chat_id=chat_id, token=token)
        return {"ok": False, "done": 1, "total": 2,
                "results": [
                    {"step": 1, "tool": "note.create", "title": "заметка", "ok": True},
                    {"step": 2, "tool": "event.assign", "title": "событие", "ok": False,
                     "error": "slot_taken", "message": "зал занят"}],
                "failed_step": {"step": 2, "message": "зал занят"}}

    monkeypatch.setattr(gw, "agent_plan_confirm", fake_confirm)
    cb = FakeCallback("agp:ok:plan-tok")

    asyncio.run(gw.agent_plan_cb(cb))

    assert calls == {"chat_id": 1, "token": "plan-tok"}
    text = cb.message.edits[-1]
    assert "не полностью" in text and "1 из 2" in text
    assert "Остановился на шаге 2" in text and "зал занят" in text


def test_отмена_плана_ничего_не_выполняет(gw, monkeypatch) -> None:
    async def fake_cancel(chat_id, token):
        return {"ok": True, "cancelled": True}

    async def fake_confirm(chat_id, token):       # не должен вызваться
        raise AssertionError("отмена не выполняет план")

    monkeypatch.setattr(gw, "agent_cancel", fake_cancel)
    monkeypatch.setattr(gw, "agent_plan_confirm", fake_confirm)
    cb = FakeCallback("agp:no:plan-tok")

    asyncio.run(gw.agent_plan_cb(cb))

    assert cb.message.edits[-1] == gw.AGENT_NOTHING_WRITTEN


def test_итог_ответа_содержит_строку_шагов(gw) -> None:
    answer = gw.visible_answer({"answer": "Свободно в четверг.",
                                "step_line": "календарь → занятость → окна"})

    assert "Свободно в четверг." in answer
    assert "Шаги: календарь → занятость → окна." in answer


@pytest.mark.parametrize("text,expected", [
    ("собери окна на неделю для репетиции", True),
    ("поставь встречу с Марком", True),
    ("найди в интернете, когда открывается театральный сезон", True),
    ("какой сегодня день", False),
    ("сколько денег в бюджете за ноябрь", False),
])
def test_задача_отличается_от_вопроса(text: str, expected: bool) -> None:
    assert routing.is_task(text) is expected
