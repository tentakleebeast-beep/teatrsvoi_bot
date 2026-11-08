"""
B-14: фраза «назначь репетицию…» не должна уходить в бюджет.

Дефект: свой обработчик `assign_start` зарегистрирован в main.py ниже catch-all
`on_text`, поэтому aiogram до него не доходит. Фраза с цифрой не попадала в
бюджетный разбор (защита `looks_like_assign` стояла), но уходила в n8n-роутер:
если роутер отвечал `intent: budget`, человек получал черновик траты на 20 ₽.
Здесь роутер — заглушка, которая специально врёт «budget».
"""

import asyncio
import importlib.util
import pathlib
import sys
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"

ASSIGN_PHRASE = "назначь репетицию с Витей в четверг в 20"
BUDGET_PHRASE = "потратил 3000 на реквизит"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeSent:
    """Ответ бота на сообщение — у него есть edit_text и delete."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    async def edit_text(self, text, **kwargs):
        self.text = text
        return self

    async def delete(self):
        return None


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=555)
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return FakeSent(text)

    async def delete(self):
        return None


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None

    async def get_state(self):
        return self.state

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def clear(self):
        self.data.clear()
        self.state = None


CARD = {
    "kind_title": "Репетиция",
    "title": "Репетиция с Витей",
    "weekday": "четверг",
    "date": "2026-10-29",
    "from_hour": 20,
    "to_hour": 22,
    "venue_name": "Зал",
    "participants": [{"name": "Витя"}],
    "directors": [],
    "ambiguity": {},
}


async def _value(value):
    return value


def run(gw, monkeypatch, text, router_intent="budget"):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        if path == "/tools/events/preview":
            return {"ok": True, "can_assign": True, "proposal_id": 7,
                    "card": CARD, "warnings": [], "errors": []}
        if path == "/tools/budget/parse":
            return {"ok": True,
                    "draft": {"amount": 3000, "tx_type": "expense", "category": "Реквизит",
                              "tx_date": "2026-10-28", "purpose": "реквизит"},
                    "flags": []}
        return {"ok": True}

    async def fake_router(text, user=None, categories=None):
        calls.append(("router", text))
        return {"intent": router_intent}

    async def fake_user_ctx(chat_id):
        return {}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("core"), "router_ask", fake_router)
    monkeypatch.setattr(gateway_modules.load("core"), "user_ctx", fake_user_ctx)
    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", lambda chat_id: _value(["budget.write"]))

    message = FakeMessage(text)
    asyncio.run(gw.on_text(message, FakeState()))
    return calls, message


def paths(calls):
    return [c[0] for c in calls if isinstance(c[0], str)]


def test_назначение_идёт_в_предпросмотр_а_не_в_бюджет(gw, monkeypatch):
    calls, message = run(gw, monkeypatch, ASSIGN_PHRASE)
    assert "/tools/events/preview" in paths(calls), "фраза не дошла до события"
    assert "router" not in paths(calls), "фраза ушла в n8n-роутер"
    assert "/tools/budget/parse" not in paths(calls)
    assert any("Репетиция" in text for text, _ in message.answers)


def test_роутер_не_может_увести_назначение_в_бюджет(gw, monkeypatch):
    """Даже если роутер отвечает intent=budget, назначение остаётся назначением."""
    calls, _ = run(gw, monkeypatch, ASSIGN_PHRASE, router_intent="budget")
    assert not [c for c in calls if isinstance(c[0], str) and c[0].startswith("/tools/intent/")]


def test_трата_текстом_сразу_даёт_карточку(gw, monkeypatch):
    """docs/82 §4.1: фраза с суммой превращается в карточку, а не в лекцию про команды.

    Раньше здесь проверялось обратное («свободный текст данных не вводит») — это
    ровно то поведение, из-за которого сценарий не работал.
    """
    calls, message = run(gw, monkeypatch, BUDGET_PHRASE)
    assert "router" not in paths(calls), "фраза ушла в роутер вместо карточки"
    assert "/tools/budget/parse" not in paths(calls), "LLM не нужен: разбор детерминированный"
    text = message.answers[0][0]
    assert "Записать расход?" in text
    assert "Реквизит и костюмы" in text
    assert "3 000" in text
    buttons = [b.callback_data for row in message.answers[0][1]["reply_markup"].inline_keyboard for b in row]
    assert "bud:ok" in buttons


def test_без_права_budget_write_карточки_нет(gw, monkeypatch):
    """Кнопка «Записать» без права ведёт в 403 — карточку такому человеку не показываем."""
    calls, message = run(gw, monkeypatch, BUDGET_PHRASE)
    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", lambda chat_id: _value([]))
    message = FakeMessage(BUDGET_PHRASE)
    asyncio.run(gw.on_text(message, FakeState()))
    text = message.answers[0][0]
    assert "Записать расход?" not in text
    assert "bud:ok" not in str(message.answers[0][1])
