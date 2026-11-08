"""Живой диалог назначения: «встреча с Марком и Машей» → «завтра» → «с 15-00 до 18-00».

Обоснование: docs/architecture/51 (назначение события), FR-F7 (команды и диалоги чата),
карта t_ebce081e. Диалог восстановлен по скриншоту 05.11.2026: бот спросил дату, потерял
разговор на слове «завтра» и закончил карточкой расхода на 15 ₽.

Проверяется поведение входа целиком — `menu.on_text`, как его зовёт Telegram. Серверная
часть ненастоящая только в сетевом слое: разбор текста берётся из `app.assign`, то есть
из того же кода, что работает в tool-api, подменён лишь HTTP-вызов `core.api`.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import types
from datetime import date, timedelta

import gateway_modules
import pytest
from app.assign import parse_assignment

GATEWAY_APP = pathlib.Path(__file__).resolve().parents[2] / "services" / "tg-gateway" / "app"
if str(GATEWAY_APP) not in sys.path:
    sys.path.insert(0, str(GATEWAY_APP))

BUDGET_REPLY = "Записать расход?"
TODAY = date(2026, 11, 5)


class FakeSent:
    """Ответ бота на сообщение: у него есть edit_text и delete."""

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
        # `start` ставит кнопку меню в чате: вызов есть, Telegram за него не отвечает
        self.bot = types.SimpleNamespace(set_chat_menu_button=_noop)
        self.from_user = types.SimpleNamespace(full_name="Марк")

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return FakeSent(text)

    async def delete(self):
        return None


class FakeState:
    """Состояние как у aiogram: `get_state` возвращает строку «AssignDraft:await_date»."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.state: str | None = None

    async def get_state(self):
        return self.state

    async def set_state(self, state):
        self.state = getattr(state, "state", state)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data.clear()
        self.state = None


#: Имена из фразы в том виде, в каком их вернул бы сервер.
NAMES = {"марком": "Марк", "машей": "Маша", "витей": "Витя", "людой": "Люда"}


def _missing(code: str, text: str) -> dict:
    """Ответ tool-api, когда данных не хватает (контракт `/tools/events/preview`)."""
    return {"ok": False, "errors": [{"code": code, "text": text}], "input_error": True,
            "card": None, "warnings": [], "can_assign": False, "proposal_id": None}


def _day(parsed) -> date:
    if parsed.day:
        return date.fromisoformat(parsed.day)
    if parsed.relative_days is not None:
        return TODAY + timedelta(days=parsed.relative_days)
    return TODAY + timedelta(days=(parsed.weekday - TODAY.weekday()) % 7)


def preview(text: str) -> dict:
    """Предпросмотр события: та же логика проверок, что в `/tools/events/preview`."""
    parsed = parse_assignment(text)
    if not parsed.kind:
        return _missing("no_kind", "Не понял тип события: репетиция, показ, тренинг или аренда?")
    if parsed.day is None and parsed.relative_days is None and parsed.weekday is None:
        return _missing("no_date", "Не понял день: назовите дату или день недели.")
    if parsed.hour is None:
        return _missing("no_time", "Не понял время начала.")
    day = _day(parsed)
    from_hour = parsed.hour
    to_hour = from_hour + parsed.duration_hours
    people: list[str] = [NAMES.get(name) or name for name in parsed.names]
    return {"ok": True, "errors": [], "warnings": [], "input_error": False,
            "can_assign": True, "proposal_id": 7,
            "card": {"kind": parsed.kind, "kind_title": "Встреча",
                     "title": " / ".join(people) or "Встреча",
                     "date": day.isoformat(), "weekday": "пятница",
                     "from_hour": from_hour, "to_hour": to_hour,
                     "venue_name": "Зал",
                     "participants": [{"id": index + 1, "name": name}
                                      for index, name in enumerate(people)],
                     "directors": [], "ambiguity": {}}}


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


@pytest.fixture
def calls(gw, monkeypatch):
    """Подмена сети: предпросмотр считает настоящий разбор, остальное — пустой ответ."""
    seen: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        seen.append((path, payload))
        if path == "/tools/events/preview":
            return preview(payload.get("text") or "")
        return {"ok": False}

    async def fake_router(text, user=None, categories=None):
        seen.append(("router", text))
        return {}

    async def fake_user_ctx(chat_id):
        return {}

    core = gateway_modules.load("core")
    monkeypatch.setattr(core, "api", fake_api)
    monkeypatch.setattr(core, "router_ask", fake_router)
    monkeypatch.setattr(core, "user_ctx", fake_user_ctx)
    return seen


def say(gw, state: FakeState, text: str) -> list[str]:
    """Реплика человека в чат: тот же вход, что у Telegram, — `menu.on_text`."""
    return reply(gw, state, text)[0]


def reply(gw, state: FakeState, text: str) -> tuple[list[str], list[str]]:
    """Ответы бота на реплику: тексты и колбэки кнопок (что человек увидит)."""
    message = FakeMessage(text)
    asyncio.run(gw.on_text(message, state))
    data = [button.callback_data
            for _, kwargs in message.answers
            for markup in [kwargs.get("reply_markup")] if markup is not None
            for row in markup.inline_keyboard for button in row]
    return [text for text, _ in message.answers], data


def test_живой_диалог_собирает_карточку_за_три_реплики(gw, calls):
    """Критерий приёмки: «встреча с Марком и Машей» → день → время → карточка события."""
    state = FakeState()

    first = say(gw, state, "поставь встречу с Марком и Машей")
    assert "День не назван" in " ".join(first), first
    assert state.state == "AssignDraft:await_date"

    second = say(gw, state, "завтра")
    assert "Время не названо" in " ".join(second), second
    assert state.state == "AssignDraft:await_time"

    third = say(gw, state, "с 15-00 до 18-00")
    card = " ".join(third)
    assert "15:00–18:00" in card, third
    assert "Участники: Марк, Маша" in card, third
    assert state.state is None, "диалог не закрыт после карточки"

    texts = first + second + third
    assert not any(BUDGET_REPLY in text for text in texts), "вместо события показали расход"
    assert "/tools/budget/parse" not in [path for path, _ in calls]


def test_карточка_собирается_и_одной_фразой(gw, calls):
    """Тот же состав целиком: интервал через дефис понимается и без диалога."""
    state = FakeState()
    said = say(gw, state, "поставь встречу на завтра с Машей и Марком с 15-00 до 18-00")
    card = " ".join(said)
    assert "15:00–18:00" in card, said
    assert "Участники: Маша, Марк" in card, said
    assert state.state is None


def test_интервал_времени_без_назначения_не_становится_расходом(gw, calls):
    """Критерий приёмки: «с 15-00 до 18-00» вне диалога не рождает черновик траты."""
    state = FakeState()
    said, data = reply(gw, state, "с 15-00 до 18-00")
    assert not any(BUDGET_REPLY in text for text in said), said
    assert "bud:ok" not in data, "интервал часов превратился в карточку расхода"
    assert state.state is None


def test_день_можно_назвать_числом(gw, calls):
    """Диалог обещает «5.11» — вторая реплика с датой числом тоже работает."""
    state = FakeState()
    say(gw, state, "поставь встречу с Марком и Машей")
    said = say(gw, state, "5.11")
    assert "Время не названо" in " ".join(said), said

    card = " ".join(say(gw, state, "с 15:00 до 18:00"))
    assert "05.11" in card and "15:00–18:00" in card, card
    assert "Участники: Марк, Маша" in card, card
    assert state.state is None


def test_отмена_словом_снимает_назначение(gw, calls):
    """«Отмена» посреди уточнения: состояние снято, ничего не записано."""
    state = FakeState()
    say(gw, state, "поставь встречу с Марком и Машей")
    assert state.state == "AssignDraft:await_date"

    said = say(gw, state, "отмена")
    assert "Отменил" in " ".join(said), said
    assert state.state is None


def test_кнопка_отмена_снимает_назначение(gw, calls):
    """Кнопка «Отмена» под вопросом делает то же, что слово."""
    state = FakeState()
    assert "evdraft:no" in reply(gw, state, "поставь встречу с Марком и Машей")[1]

    callback = types.SimpleNamespace(
        data="evdraft:no", message=FakeMessage(""), answer=_noop)
    asyncio.run(gw.draft_cancel(callback, state))
    assert state.state is None
    assert "Отменил" in callback.message.answers[0][0]


def test_другое_намерение_снимает_назначение(gw, calls):
    """«сколько денег» посреди назначения — вопрос, а не ответ про день."""
    state = FakeState()
    say(gw, state, "поставь встречу с Марком и Машей")
    say(gw, state, "сколько денег осталось")
    assert state.state is None, "черновик назначения остался висеть"
    assert "/tools/events/preview" not in [path for path, _ in calls[1:]]


def test_menu_и_start_снимают_назначение(gw, calls):
    """Критерий приёмки: /menu и /start посреди назначения снимают состояние."""
    state = FakeState()
    say(gw, state, "поставь встречу с Марком и Машей")
    asyncio.run(gw.menu_cmd(FakeMessage("/menu"), state))
    assert state.state is None

    say(gw, state, "поставь встречу с Марком и Машей")
    assert state.state == "AssignDraft:await_date"
    asyncio.run(gw.start(FakeMessage("/start"), state))
    assert state.state != "AssignDraft:await_date"


async def _noop(*args, **kwargs):
    return None
