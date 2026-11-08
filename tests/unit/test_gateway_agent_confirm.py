"""Карточка подтверждения предложения агента и кнопки на ней (A-3, docs/development/108 §8).

Проверяем клей gateway, а не Telegram:
  * без права `budget.write` карточка не рисуется и токен не запрашивается;
  * с правом карточка несёт сумму, категорию и дату, а кнопки — «Подтвердить»
    и «Отмена» с одноразовым токеном, а не с аргументами операции;
  * нажатия уходят в `/tools/agent/confirm` и `/tools/agent/cancel`, отказ
    объясняется человеку словами, а не кодом;
  * тексты карточки и отказов живут в `texts.py`, а не в обработчике.
"""

from __future__ import annotations

import asyncio
import pathlib
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


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


PROPOSAL = {"tool": "budget.tx",
            "args": {"tx_type": "expense", "amount": 3000, "category": "Реквизит и костюмы",
                     "purpose": "костюмы", "tx_date": "2026-11-01"},
            "human_text": "Записать расход: 3000 ₽"}

CARD = {"ok": True, "token": "tok-123", "ttl_seconds": 300,
        "proposal": {"id": 7, "tool": "budget.tx", "args": PROPOSAL["args"],
                     "human_text": PROPOSAL["human_text"]}}


def _card(gw, monkeypatch, caps: list[str], card: dict | None = None,
          answer: str = "Предлагаю записать расход.") -> tuple[FakeMessage, dict]:
    calls: dict = {}

    async def fake_caps(chat_id):
        return caps

    async def fake_propose(chat_id, proposal):
        calls["proposal"] = proposal
        return card if card is not None else CARD

    monkeypatch.setattr(gw, "caps_of", fake_caps)
    monkeypatch.setattr(gw, "agent_propose", fake_propose)
    message = FakeMessage()
    handled = asyncio.run(gw.show_agent_card(
        message, 1, {"ok": True, "answer": answer, "propose": PROPOSAL}))
    return message, {"handled": handled, **calls}


def test_без_права_записи_карточка_не_рисуется(gw, monkeypatch) -> None:
    message, calls = _card(gw, monkeypatch, ["schedule.availability.self"])

    assert calls["handled"] is True
    assert "proposal" not in calls, "без права записи токен запрашивать незачем"
    assert message.answers and message.answers[0][1] is None, "кнопок быть не должно"


def test_с_правом_карточка_показывает_сумму_категорию_и_дату(gw, monkeypatch) -> None:
    message, calls = _card(gw, monkeypatch, ["budget.write"])

    assert calls["handled"] is True and calls["proposal"] == PROPOSAL
    text, markup = message.answers[-1]
    assert "3 000" in text and "Реквизит и костюмы" in text and "01.11.2026" in text
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["Подтвердить", "Отмена"]
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert data == ["agc:ok:tok-123", "agc:no:tok-123"]


def test_без_токена_карточку_не_рисуем(gw, monkeypatch) -> None:
    message, calls = _card(gw, monkeypatch, ["budget.write"],
                           card={"ok": False, "error": "forbidden"})

    assert calls["handled"] is False
    assert message.answers == [], "без токена подтверждать нечего"


def test_предложения_нет_обработчик_молчит(gw, monkeypatch) -> None:
    message = FakeMessage()
    handled = asyncio.run(gw.show_agent_card(message, 1, {"ok": True, "answer": "Привет"}))
    assert handled is False and message.answers == []


def test_подтверждение_уходит_в_детерминированный_эндпоинт(gw, monkeypatch) -> None:
    calls: dict = {}

    async def fake_confirm(chat_id, token):
        calls.update(chat_id=chat_id, token=token)
        return {"ok": True, "deduped": False,
                "proposal": {"args": PROPOSAL["args"]}}

    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:ok:tok-123")

    asyncio.run(gw.agent_confirm_cb(cb))

    assert calls == {"chat_id": 1, "token": "tok-123"}
    assert cb.message.edits and "Записано" in cb.message.edits[-1]


def test_подтверждение_события_не_пишет_про_деньги(gw, monkeypatch) -> None:
    """Событие подтверждается словами про событие, а не бюджетным шаблоном.

    Ревью 05.11.2026: для любого токена печаталось «Записано: 0 ₽ · Прочее · .».
    """
    card = {"kind_title": "Репетиция", "title": "Репетиция с Марком",
            "date": "2026-11-06", "from_hour": 18, "to_hour": 20, "venue_name": "Сцена"}

    async def fake_confirm(chat_id, token):
        return {"ok": True, "event_id": 5, "in_calendar": True, "card": card,
                "proposal": {"id": 8, "tool": "event.assign", "args": {"card": card}}}

    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:ok:tok-123")

    asyncio.run(gw.agent_confirm_cb(cb))

    text = cb.message.edits[-1]
    assert text.startswith("Назначено:")
    assert "06.11.2026" in text and "18:00–20:00" in text and "Сцена" in text
    assert "₽" not in text


def test_подтверждение_заметки_не_пишет_про_деньги(gw, monkeypatch) -> None:
    """Заметка — это заметка: ни суммы, ни категории в итоге быть не должно."""
    async def fake_confirm(chat_id, token):
        return {"ok": True, "note": {"id": 3},
                "proposal": {"id": 9, "tool": "note.create",
                             "args": {"title": "Свет", "body": "текст"}}}

    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:ok:tok-123")

    asyncio.run(gw.agent_confirm_cb(cb))

    assert cb.message.edits[-1] == gw.AGENT_NOTE_WRITTEN
    assert "₽" not in cb.message.edits[-1]


def test_повторное_нажатие_объясняется_словами(gw, monkeypatch) -> None:
    async def fake_confirm(chat_id, token):
        return {"ok": False, "error": "token_used"}

    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:ok:tok-123")

    asyncio.run(gw.agent_confirm_cb(cb))

    assert cb.message.edits[-1] == gw.AGENT_ALREADY


def test_истёкший_токен_объясняется_словами(gw, monkeypatch) -> None:
    async def fake_confirm(chat_id, token):
        return {"ok": False, "error": "token_expired"}

    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:ok:tok-123")

    asyncio.run(gw.agent_confirm_cb(cb))

    assert cb.message.edits[-1] == gw.AGENT_EXPIRED


def test_отмена_ничего_не_подтверждает(gw, monkeypatch) -> None:
    calls: dict = {}

    async def fake_cancel(chat_id, token):
        calls.update(chat_id=chat_id, token=token)
        return {"ok": True, "cancelled": True, "note": "отменено"}

    async def fake_confirm(chat_id, token):        # не должен вызваться
        raise AssertionError("отмена не подтверждает запись")

    monkeypatch.setattr(gw, "agent_cancel", fake_cancel)
    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:no:tok-123")

    asyncio.run(gw.agent_confirm_cb(cb))

    assert calls["token"] == "tok-123"
    assert cb.message.edits[-1] == gw.AGENT_NOTHING_WRITTEN


def test_битая_кнопка_не_вызывает_сервис(gw, monkeypatch) -> None:
    async def fake_confirm(chat_id, token):
        raise AssertionError("битый callback не должен идти в tool-api")

    monkeypatch.setattr(gw, "agent_confirm", fake_confirm)
    cb = FakeCallback("agc:ok")

    asyncio.run(gw.agent_confirm_cb(cb))

    assert cb.alerts == [gw.STALE_BUTTON]


def test_формулировки_живут_в_texts() -> None:
    """Граница карты: текст карточки собирается в texts.py, а не в обработчике."""
    source = (ROOT / "services" / "tg-gateway" / "app" / "texts.py").read_text("utf-8")
    assert "def agent_card(" in source
    assert "AGENT_CARD_TITLE" in source
    router_source = (ROOT / "services" / "tg-gateway" / "app" / "routers"
                     / "confirm.py").read_text("utf-8")
    assert "texts.AGENT_" in router_source
    assert '"Подтвердить"' not in router_source, "подпись кнопки берётся из texts"
