"""Личное время участия из бота: кнопка «Время: имя» и текст «19:30–21:00».

Обоснование: docs/reports/194 §2 п.3 (решение владельца V-134). AC2 требует
необязательные поля «с / до» и из бота: организатор выбирает человека кнопкой
(имя из текста пришлось бы разбирать заново и разошлось бы с составом карточки),
а время пишет строкой. Минуты доходят до `assign` без округления.
"""

from __future__ import annotations

import asyncio

import gateway_modules
import pytest


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeState:
    def __init__(self, data: dict | None = None, state: str | None = None):
        self.data = dict(data or {})
        self.state = state

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def get_state(self):
        return self.state

    async def clear(self):
        self.data.clear()
        self.state = None


class FakeMessage:
    def __init__(self):
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


def _card(participant_id: int = 7, name: str = "Витя") -> dict:
    return {"kind_title": "Репетиция", "title": "Сцена", "weekday": "понедельник",
            "date": "2026-12-21", "from_hour": 18, "to_hour": 22, "venue_name": "Сцена",
            "participants": [{"id": participant_id, "name": name,
                              "time_from": None, "time_to": None}]}


def test_карточка_даёт_кнопку_времени_на_каждого(gw):
    """Состав уже в карточке, поэтому человек выбирается кнопкой, а не разбором."""
    res = {"proposal_id": 5, "can_assign": True,
           "card": _card()}
    keyboard = gw.card_keyboard(res, res["card"])
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "evwin:5:7" in data, data
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("Витя" in label for label in labels), labels
    assert "Назначить" in labels


def test_кнопки_времени_нет_без_права_назначить(gw):
    """Нечего подтверждать — нечего и время задавать."""
    res = {"proposal_id": 5, "can_assign": False, "card": _card()}
    assert gw.card_keyboard(res, res["card"]) is None


def test_текст_окна_сохраняет_минуты_и_перерисовывает_карточку(gw):
    """«19:30–21:00» остаётся минутами, а карточка показывает новое окно."""
    state = FakeState({"awho": {"id": 7, "name": "Витя"},
                       "ap": {"proposal_id": 5, "card": _card()}},
                      state=gw.AssignDraft.await_window.state)
    message = FakeMessage()

    handled = asyncio.run(gw._window_reply(message, state, "с 19:30 до 21:00"))

    assert handled is True
    assert state.data["awin"]["7"] == {"time_from": "19:30", "time_to": "21:00"}
    assert any("Витя (19:30–21:00)" in text for text in message.answers), message.answers


def test_весь_интервал_снимает_окно(gw):
    """«весь» — не пустая строка в базе, а отсутствие личного окна."""
    state = FakeState({"awho": {"id": 7, "name": "Витя"},
                       "awin": {"7": {"time_from": "19:30", "time_to": "21:00"}}},
                      state=gw.AssignDraft.await_window.state)
    message = FakeMessage()

    asyncio.run(gw._window_reply(message, state, "весь"))

    assert "7" not in (state.data.get("awin") or {})
    assert "весь интервал события" in message.answers[-1]


def test_кривое_время_отказ_словами(gw):
    """«21:00–19:30» и «25:00» — вопрос заново, а не молчаливая правка."""
    state = FakeState({"awho": {"id": 7, "name": "Витя"}},
                      state=gw.AssignDraft.await_window.state)
    message = FakeMessage()

    asyncio.run(gw._window_reply(message, state, "21:00–19:30"))
    asyncio.run(gw._window_reply(message, state, "25:00–26:00"))

    assert "awin" not in state.data
    assert all("Не понял время" in text for text in message.answers), message.answers


def test_окна_уходят_в_assign_с_минутами(gw):
    """Тело запроса на подтверждении несёт `time_from`/`time_to` строкой."""
    payload = gw._windows_payload({"awin": {"7": {"time_from": "19:30", "time_to": "21:00"},
                                            "2": {"time_from": None, "time_to": None}}})
    assert payload == [{"participant_id": 2, "time_from": None, "time_to": None},
                       {"participant_id": 7, "time_from": "19:30", "time_to": "21:00"}]
