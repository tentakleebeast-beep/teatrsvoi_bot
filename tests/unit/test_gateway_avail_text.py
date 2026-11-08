"""AUD-4: занятость свободным текстом идёт через уже написанный разбор.

Дефект: `routing.py` ловил слово «занят» и отдавал подсказку «данные вводятся
командой или формой», хотя разбор фразы детерминирован и уже работает у `/busy`.
Человек, написавший «занят завтра с 10 до 14», уходил в форму, а при праве на
бюджет фраза с цифрой превращалась в черновик траты на 10 ₽.

Контракт (`docs/architecture/25` §9.5, требования 2 и 3): текст допустим там, где
разбор детерминирован и ошибка видна до записи. Поэтому фраза даёт карточку с
«Верно»/«Исправить», запись идёт только после подтверждения, а часы вне рабочего
дня студии 08:00–23:00 (`docs/architecture/11`) — отказ с текстом, не карточка.

Обоснование: FR-C2, FR-C3, S-15 (docs/development/19, docs/development/20);
карта AUD-4 t_1ef9e4c7.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest
from app.rules import parse_availability

PHRASE = "занят завтра с 10 до 14"
OUTSIDE_PHRASE = "занят завтра с 6 до 9"
UNDATED_PHRASE = "занят с 10 до 14"
HINT_MARK = "данные бот принимает командой или формой"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeSent:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=777)

    async def edit_text(self, text, **kwargs):
        self.text = text
        return self

    async def delete(self):
        return None

    async def answer(self, *args, **kwargs):
        return FakeSent()


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=777)
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

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data.clear()
        self.state = None


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answers: list = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


def _buttons(message: FakeMessage) -> list[str]:
    markup = message.answers[0][1].get("reply_markup")
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _texts(message: FakeMessage) -> str:
    return "\n".join(text for text, _ in message.answers)


async def _value(value):
    return value


def run(gw, monkeypatch, phrase: str, caps: list[str] | None = None):
    """Свободный текст в on_text; сеть подменена настоящим разбором tool-api."""
    calls: list[tuple] = []
    caps = caps if caps is not None else ["schedule.availability.self"]

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        if path == "/tools/availability/parse":
            return parse_availability(payload["text"])
        if path == "/tools/availability/commit":
            return {"ok": True, "day": payload["day"],
                    "hours": f"{payload['start']:02d}–{payload['end']:02d}"}
        if path == "/tools/assistant/ask":
            return {"ok": True, "answer": "Занятость сегодня: данных нет."}
        return {"ok": True}

    async def fake_router(text, user=None, categories=None):
        calls.append(("router", text))
        return {"intent": "other"}

    async def fake_agent(chat_id, text, task=False):
        calls.append(("agent", text))
        return {}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", lambda chat_id: _value(caps))
    monkeypatch.setattr(gateway_modules.load("core"), "router_ask", fake_router)
    monkeypatch.setattr(gateway_modules.load("core"), "user_ctx", lambda chat_id: _value({}))
    monkeypatch.setattr(gateway_modules.load("core"), "agent_ask", fake_agent)

    message = FakeMessage(phrase)
    state = FakeState()
    asyncio.run(gw.on_text(message, state))
    return calls, message, state


def paths(calls) -> list[str]:
    return [c[0] for c in calls if isinstance(c[0], str)]


def test_aud4_фраза_про_занятость_даёт_карточку_а_не_подсказку(gw, monkeypatch):
    """S-15, FR-C2: «занят завтра с 10 до 14» — карточка подтверждения.

    Падает, если вернуть подсказку про форму: без вызова парсера карточки нет.
    """
    calls, message, state = run(gw, monkeypatch, PHRASE)

    assert "/tools/availability/parse" in paths(calls), "фраза не дошла до детерминированного разбора"
    assert "router" not in paths(calls) and "agent" not in paths(calls), "разбор ушёл в модель"
    text = _texts(message)
    assert "Записываю занятость:" in text
    assert "10:00–14:00, занят" in text
    assert "Верно?" in text
    assert HINT_MARK not in text.lower(), "человеку снова отдали подсказку про форму"
    assert {"av:ok", "form:busy", "av:no"} <= set(_buttons(message))
    assert state.data.get("av", {}).get("start") == 10


def test_aud4_запись_только_после_подтверждения(gw, monkeypatch):
    """FR-C3: карточка ничего не пишет; «Верно» вызывает commit с разобранным интервалом."""
    calls, message, state = run(gw, monkeypatch, PHRASE)
    assert "/tools/availability/commit" not in paths(calls), "запись произошла до подтверждения"

    cb = FakeCallback("av:ok", FakeSent())
    asyncio.run(gw.av_save(cb, state))

    commits = [payload for path, payload in calls if path == "/tools/availability/commit"]
    assert len(commits) == 1, "подтверждение не записало занятость"
    commit = commits[0]
    assert (commit["start"], commit["end"]) == (10, 14)
    assert commit["kind"] == "busy"
    assert commit["allow_outside_hours"] is False
    assert "Записал" in cb.message.text


def test_aud4_исправить_возвращает_в_форму(gw, monkeypatch):
    """FR-C3: «Исправить» ведёт в ту же форму, что кнопка «Занятость»."""
    _, message, _ = run(gw, monkeypatch, PHRASE)
    assert "form:busy" in _buttons(message)

    state = FakeState()
    cb = FakeCallback("form:busy", FakeSent())
    asyncio.run(gw.form_start(cb, state))
    assert state.state == gw.AvailForm.day.state, "«Исправить» не открыл форму дня"


def test_aud4_часы_вне_рабочего_дня_это_отказ(gw, monkeypatch):
    """FR-C2, docs/architecture/11: «занят завтра с 6 до 9» — отказ с текстом, не карточка."""
    calls, message, state = run(gw, monkeypatch, OUTSIDE_PHRASE)

    text = _texts(message)
    assert "08:00–23:00" in text
    assert "Записываю занятость:" not in text
    assert "av:ok" not in _buttons(message)
    assert "/tools/availability/commit" not in paths(calls)
    assert state.data.get("av") is None


def test_aud4_без_дня_в_тексте_карточки_нет(gw, monkeypatch):
    """FR-C2: без дня разбор честно отказывает — карточка на «с 6 до 9» не рисуется."""
    calls, message, _ = run(gw, monkeypatch, UNDATED_PHRASE)

    text = _texts(message)
    assert "какой день" in text.lower()
    assert "Записываю занятость:" not in text
    assert "/tools/availability/commit" not in paths(calls)


def test_aud4_вопрос_о_чужой_занятости_не_становится_карточкой(gw, monkeypatch):
    """Ш-52: «кто сегодня занят» — вопрос к агенту, а не ввод своих данных."""
    calls, message, _ = run(gw, monkeypatch, "кто сегодня занят")
    assert "/tools/availability/parse" not in paths(calls)
    assert "Записываю занятость:" not in _texts(message)


def test_aud4_трата_с_суммой_остаётся_карточкой_траты(gw, monkeypatch):
    """Регрессия порядка: ветка занятости не должна перехватывать бюджетную фразу."""
    calls, message, _ = run(gw, monkeypatch, "потратил 3000 на реквизит",
                            caps=["budget.write", "schedule.availability.self"])
    assert "/tools/availability/parse" not in paths(calls)
    text = _texts(message)
    assert "3 000" in text
    assert "bud:ok" in _buttons(message)
