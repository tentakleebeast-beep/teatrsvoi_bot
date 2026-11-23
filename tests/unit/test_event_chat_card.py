"""Разбор «когда перенести» и вид карточки события (С2, С3; FR-D18, FR-D19).

Обоснование: FR-D18, FR-D19, карта t_dfb5f169 (Ш-06).

Разбор вынесен в чистую функцию: ошибка в нём — это перенос не на тот день,
и увидеть её дешевле на строках, чем на живой базе.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
if str(GATEWAY_APP) not in sys.path:
    sys.path.insert(0, str(GATEWAY_APP))

TODAY = dt.date(2026, 11, 5)


@pytest.fixture(scope="module")
def chat():
    from routers import events  # noqa: PLC0415 — путь gateway подставлен выше

    return events


def test_дата_и_время_цифрами(chat):
    assert chat.parse_when("11.12 19:00", TODAY) == (dt.date(2026, 12, 11), 19)
    assert chat.parse_when("7.11 в 18", TODAY) == (dt.date(2026, 11, 7), 18)


def test_дата_словом(chat):
    assert chat.parse_when("7 ноября 19:00", TODAY) == (dt.date(2026, 11, 7), 19)


def test_относительный_день(chat):
    assert chat.parse_when("завтра 18:00", TODAY) == (dt.date(2026, 11, 6), 18)
    assert chat.parse_when("послезавтра 10:00", TODAY) == (dt.date(2026, 11, 7), 10)


def test_минуты_округляются_вверх_до_целого_часа(chat):
    """API принимает целый час начала; человек видит результат в подтверждении."""
    assert chat.parse_when("11.12 19:30", TODAY) == (dt.date(2026, 12, 11), 20)
    assert chat.parse_when("11.12 19:15", TODAY) == (dt.date(2026, 12, 11), 19)


def test_без_даты_не_угадываем(chat):
    """«Сегодня же» по умолчанию — догадка, а перенос необратим."""
    assert chat.parse_when("19:00", TODAY) is None
    assert chat.parse_when("в 19", TODAY) is None


def test_без_времени_не_угадываем(chat):
    assert chat.parse_when("11.12", TODAY) is None


def test_несуществующая_дата_отклоняется(chat):
    assert chat.parse_when("31.02 19:00", TODAY) is None


def test_карточка_несёт_день_часы_помещение_и_состав(chat):
    event = {"event_id": 5, "kind_title": "Репетиция", "title": "Прогон",
             "status": "confirmed", "date": "2026-11-05", "from_hour": 20, "to_hour": 22,
             "venue": "stage", "venue_name": "Сцена",
             "participants": [{"id": 1, "name": "Витя"}]}

    text = chat.card_text(event)

    assert text.splitlines()[0] == "Репетиция: Прогон"
    assert "Четверг, 05.11, 20:00–22:00" in text
    assert "Помещение: Сцена" in text
    assert "Участники: Витя" in text


def test_отменённое_событие_показано_словом(chat):
    event = {"event_id": 5, "kind_title": "Репетиция", "title": "Прогон",
             "status": "cancelled", "date": "2026-11-05", "from_hour": 20, "to_hour": 22,
             "participants": []}

    assert "Событие отменено." in chat.card_text(event)


def test_кнопки_карточки_ведут_к_отмене_и_переносу(chat):
    markup = chat.event_markup(12)
    data = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert data == ["evcancel:12", "evmove:12"]


def test_после_назначения_карточка_события_уже_с_кнопками(chat, monkeypatch):
    """Чат даёт карточку сразу после записи: отменять и переносить можно с неё."""
    import core as gateway_core  # noqa: PLC0415 — модуль gateway
    from routers import assign  # noqa: PLC0415

    class Message:
        def __init__(self) -> None:
            self.chat = types.SimpleNamespace(id=555)
            self.answers: list[tuple[str, object]] = []

        async def answer(self, text, reply_markup=None, **kwargs):
            self.answers.append((text, reply_markup))

    class Callback:
        data = "evok:7"
        message = Message()
        alerts: list = []

        async def answer(self, text=None, **kwargs):
            self.alerts.append(text)

    async def fake_api(path, payload=None, chat_id=None, method="POST"):
        assert path == "/tools/events/assign"
        return {"ok": True, "event_id": 42, "in_calendar": True, "notified": 1,
                "card": {"kind_title": "Репетиция", "title": "Прогон", "weekday": "четверг",
                         "date": "2026-11-05",
                         "from_hour": 20, "to_hour": 22, "venue_name": "Сцена"}}

    monkeypatch.setattr(gateway_core, "api", fake_api)
    callback = Callback()
    asyncio.run(assign.assign_confirm(callback))

    text, markup = callback.message.answers[-1]
    assert text.startswith("Назначено:")
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert data == ["evcancel:42", "evmove:42"]


class _Message:
    def __init__(self) -> None:
        self.chat = types.SimpleNamespace(id=555)
        self.text = ""
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))


class _Callback:
    def __init__(self, data: str, message: _Message) -> None:
        self.data = data
        self.message = message
        self.alerts: list = []

    async def answer(self, text=None, **kwargs):
        self.alerts.append(text)


class _State:
    """Подмена FSMContext: хранит состояние и данные, как aiogram."""

    def __init__(self) -> None:
        self.state = None
        self.data: dict = {}

    async def get_state(self):
        return self.state

    async def set_state(self, state) -> None:
        self.state = state

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def clear(self) -> None:
        self.state = None
        self.data.clear()


def _event(event_id: int = 7) -> dict:
    return {"event_id": event_id, "kind_title": "Репетиция", "title": "Прогон",
            "status": "confirmed", "date": "2026-12-10", "from_hour": 18, "to_hour": 21,
            "venue": "stage", "venue_name": "Сцена",
            "participants": [{"id": 3, "name": "Витя"}]}


def test_отмена_переноса_снимает_состояние(chat, monkeypatch):
    """После «Отмены» форма закрыта: свободный текст не уходит в разбор даты.

    Иначе `EventMove.await_time` и `ev_new_*` остаются жить и следующий текст
    человек получает ответ «Не понял дату и время» вместо обычной обработки.
    """
    import core as gateway_core  # noqa: PLC0415 — модуль gateway
    from states import EventMove  # noqa: PLC0415

    async def fake_api(path, payload=None, chat_id=None, method="POST"):
        assert method == "GET" and path == "/tools/events/7"
        return {"ok": True, "event": _event()}

    async def fake_today(_chat_id=None):
        return dt.date(2026, 11, 5)

    monkeypatch.setattr(gateway_core, "api", fake_api)
    monkeypatch.setattr(gateway_core, "today_local", fake_today)
    message = _Message()
    state = _State()

    asyncio.run(chat.event_move_ask(_Callback("evmove:7", message), state))
    assert asyncio.run(state.get_state()) == EventMove.await_time

    message.text = "11.12 19:00"
    asyncio.run(chat.event_move_text(message, state))
    assert "Станет: Пятница, 11.12, 19:00–22:00" in message.answers[-1][0]
    assert "ev_new_day" in asyncio.run(state.get_data())

    asyncio.run(chat.event_card(_Callback("evcard:7", message), state))

    assert asyncio.run(state.get_state()) is None, "форма переноса осталась активной"
    assert asyncio.run(state.get_data()) == {}, "в состоянии остались ev_new_*"


def test_отмена_события_из_формы_переноса_снимает_состояние(chat, monkeypatch):
    """«Да, отменить» из формы переноса тоже закрывает форму, а не только отменяет событие."""
    import core as gateway_core  # noqa: PLC0415 — модуль gateway
    from states import EventMove  # noqa: PLC0415

    async def fake_api(path, payload=None, chat_id=None, method="POST"):
        if method == "GET":
            return {"ok": True, "event": _event()}
        assert path == "/tools/events/cancel"
        return {"ok": True, "event_id": 7, "notified": 1}

    monkeypatch.setattr(gateway_core, "api", fake_api)
    message = _Message()
    state = _State()

    asyncio.run(chat.event_move_ask(_Callback("evmove:7", message), state))
    assert asyncio.run(state.get_state()) == EventMove.await_time

    asyncio.run(chat.event_cancel_ask(_Callback("evcancel:7", message), state))
    assert asyncio.run(state.get_state()) is None, "вопрос об отмене не закрыл форму"
    asyncio.run(chat.event_cancel_ask(_Callback("evcancel:yes:7", message), state))
    assert message.answers[-1][0].startswith("Отменено.")
    assert asyncio.run(state.get_state()) is None
    assert asyncio.run(state.get_data()) == {}


def test_карточка_не_сбрасывает_чужую_форму(chat, monkeypatch):
    """Старую карточку можно открыть посреди другой записи — та форма не теряется."""
    import core as gateway_core  # noqa: PLC0415 — модуль gateway

    async def fake_api(path, payload=None, chat_id=None, method="POST"):
        return {"ok": True, "event": _event()}

    monkeypatch.setattr(gateway_core, "api", fake_api)
    message = _Message()
    state = _State()
    asyncio.run(state.set_state("BudgetForm:amount"))
    asyncio.run(state.update_data(amount="3000"))

    asyncio.run(chat.event_card(_Callback("evcard:7", message), state))

    assert asyncio.run(state.get_state()) == "BudgetForm:amount"
    assert asyncio.run(state.get_data()) == {"amount": "3000"}


def test_карточка_показывает_своё_время_участия():
    """V-134: «ты задействован 19:30–21:00» и личные окна в карточке назначения.

    Обоснование: docs/reports/194 §2 п.3. Строка берётся из `my_time`, который
    сервер считает по составу события: пустое окно он уже расширил интервалом,
    поэтому карточка не решает, что показать при пустом поле.
    """
    from routers import assign, events  # noqa: PLC0415 — путь gateway подставлен выше

    event = {"kind_title": "Репетиция", "title": "Сцена", "date": "2026-12-21",
             "from_hour": 18, "to_hour": 22, "venue_name": "Сцена",
             "participants": [{"id": 1, "name": "Витя"}],
             "my_time": {"time_from": "19:30", "time_to": "21:00"}}
    assert "Ты задействован: 19:30–21:00" in events.card_text(event)

    card = {"kind_title": "Репетиция", "title": "Сцена", "weekday": "понедельник",
            "date": "2026-12-21", "from_hour": 18, "to_hour": 22, "venue_name": "Сцена",
            "participants": [{"id": 1, "name": "Витя",
                              "time_from": "19:30", "time_to": "21:00"},
                             {"id": 2, "name": "Люда",
                              "time_from": None, "time_to": None}]}
    text = assign.card_text(card)
    assert "Витя (19:30–21:00)" in text, "окно участника видно организатору с минутами"
    assert "Люда (" not in text, "пустое окно не рисуется как своё"


def test_предупреждение_переноса_доходит_до_организатора(monkeypatch):
    """V-134 AC4: «окна не сдвинуты» сказано словами, а не только в JSON.

    Обоснование: docs/reports/194 §2 п.3. Проверяется ответ бота на перенос:
    `warnings` tool-api человек видит строкой «Внимание: …».
    """
    import asyncio
    import types

    from routers import events  # noqa: PLC0415 — путь gateway подставлен выше

    async def fake_api(path, payload, chat_id):
        return {"ok": True, "event_id": 5, "starts_at": "2026-12-25T18:00:00+04:00",
                "ends_at": "2026-12-25T20:00:00+04:00", "notified": 1,
                "warnings": [{"code": "personal_windows_outside",
                              "text": "Новый интервал не покрывает личное время: Витя. "
                                      "Окна не сдвинуты."}]}

    monkeypatch.setattr(events.core, "api", fake_api)

    class State:
        data = {"ev_id": 5, "ev_new_day": "2026-12-25", "ev_new_from": 18,
                "ev_new_to": 20, "ev_kind": "Репетиция", "ev_title": "Сцена",
                "ev_day": "2026-12-25", "ev_from": 18, "ev_to": 22, "ev_people": {}}

        async def get_data(self):
            return dict(self.data)

        async def clear(self):
            self.data.clear()

    answers: list[str] = []

    class Message:
        chat = types.SimpleNamespace(id=1)

        async def answer(self, text, reply_markup=None):
            answers.append(text)

    class Callback:
        message = Message()

        async def answer(self, text=None, show_alert=False):
            pass

    asyncio.run(events._move(Callback(), State()))
    text = "\n".join(answers)
    assert "Внимание: Новый интервал не покрывает личное время: Витя" in text, text
