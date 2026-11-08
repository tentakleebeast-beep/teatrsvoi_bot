"""Свободный текст без переключателя: всегда каскад/агент, а не отдельный режим (Ш-12).

Решение П-1 (отчёт 124 §2, план 126 Ш-12): переключателя «режим агента» больше нет,
команды `/agent` тоже. Свободный текст идёт в агент tool-api (`POST /tools/agent/ask`),
а честный отказ агента — в каскад интентов (`POST /tools/route`). Нераспознанное
намерение получает честное «не разобрал» и вход в дом действий (хаб Mini App), а не
догадку. Отдельного режима нет — есть один путь и два разбора на нём.

Обоснование: FR-E1, FR-E8 (слито в FR-I1), S-13/S-14 (docs/development/19, 20);
карта Ш-12 `t_5efdb3bd`.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest

PHRASE = "расскажи, что нового в студии"


class FakeSent:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.markup = None
        self.edits: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.edits.append(text)
        if kwargs.get("reply_markup") is not None:
            self.markup = kwargs["reply_markup"]
        return self

    async def delete(self):
        return None


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=570002)
        self.sent: list[FakeSent] = []

    async def answer(self, text, **kwargs):
        sent = FakeSent(text)
        sent.markup = kwargs.get("reply_markup")
        self.sent.append(sent)
        return sent


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

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data.clear()
        self.state = None


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


async def _value(value):
    return value


def run(gw, monkeypatch, phrase: str = PHRASE, agent: dict | None = None,
        routed: dict | None = None, base_url: str = "https://example.test"):
    """Свободный текст в `on_text`: сеть агента и каскада подменена, порядок виден."""
    order: list[str] = []

    async def fake_agent(chat_id, text, task=False):
        order.append("agent")
        return agent if agent is not None else {}

    async def fake_router(text, user=None, categories=None):
        order.append("router")
        return routed if routed is not None else {"intent": "other"}

    async def fake_api(path, payload, chat_id, method="POST"):
        order.append(path)
        return {"ok": False, "error": "llm_unavailable"}

    monkeypatch.setattr(gw, "agent_ask", fake_agent)
    monkeypatch.setattr(gw, "router_ask", fake_router)
    monkeypatch.setattr(gw, "api", fake_api)
    monkeypatch.setattr(gw, "caps_of", lambda chat_id: _value([]))
    monkeypatch.setattr(gw, "user_ctx", lambda chat_id: _value({}))
    monkeypatch.setattr(gateway_modules.load("keyboards"), "WEBAPP_BASE_URL", base_url)

    message = FakeMessage(phrase)
    asyncio.run(gw.on_text(message, FakeState()))
    return order, message


def test_свободный_текст_без_команды_идёт_в_агент_и_каскад(gw, monkeypatch):
    """FR-E1: один путь — агент первым, каскад вторым разбором на его отказе."""
    order, _ = run(gw, monkeypatch)

    assert order[:2] == ["agent", "router"], (
        "свободный текст обязан идти в /tools/agent/ask, а его отказ — в /tools/route")


def test_ответ_агента_не_зовёт_каскад_второй_раз(gw, monkeypatch):
    """Каскад — не второй режим: при готовом ответе агента он не нужен."""
    order, message = run(gw, monkeypatch, agent={"ok": True, "answer": "В студии всё по плану."})

    assert order == ["agent"]
    assert message.sent[-1].text == "В студии всё по плану."


def test_нераспознанное_дает_честный_ответ_и_вход_в_дом(gw, monkeypatch):
    """124 §2 п.2: «не разобрал» плюс кнопка-вход в хаб, а не догадка."""
    _, message = run(gw, monkeypatch)

    sent = message.sent[-1]
    assert "Не разобрал просьбу" in sent.text
    labels = [b.text for row in sent.markup.inline_keyboard for b in row]
    urls = [getattr(b.web_app, "url", None) for row in sent.markup.inline_keyboard for b in row]
    assert "Открыть меню" in labels, "нет входа в дом действий"
    assert "https://example.test/webapp/home" in urls


def test_без_адреса_приложения_кнопки_входа_нет_но_текст_честный(gw, monkeypatch):
    """Нет https-адреса — нет кнопки; обещать ссылку, которой не будет, нельзя."""
    _, message = run(gw, monkeypatch, base_url="")

    sent = message.sent[-1]
    assert "Не разобрал просьбу" in sent.text
    labels = [b.text for row in sent.markup.inline_keyboard for b in row]
    assert "Открыть меню" not in labels


def test_команды_agent_больше_нет(gw):
    """Критерий карты: `/agent` и `/агент` не зарегистрированы как команды."""
    source = gateway_modules.source()
    for command in ("agent", "агент"):
        assert f'Command("{command}")' not in source, f"команда /{command} вернулась"


def test_в_справке_нет_переключателя_режима(gw):
    """Справка не обещает режим и команду, которых нет (решение П-1)."""
    text = gw.help_text(["budget.write", "budget.read.self", "schedule.availability.self",
                         "feedback.send", "admin.panel"])

    assert "/agent" not in text
    assert "режим" not in text.lower(), "справка всё ещё говорит о режиме агента"
