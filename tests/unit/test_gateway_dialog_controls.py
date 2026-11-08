"""Клавиатура диалога: «Обнулить», «Озадачить», «Сколько мне осталось» (05.11.2026).

Решение владельца: постоянная клавиатура управляет **разговором**, а не доменами.
Тест держит три обещания, которые нельзя проверить глазами:

  * «Обнулить» выпускает из формы занятости и из назначения события — не только из
    пустого состояния (человек не должен застревать в боте);
  * «Озадачить» — одноразовая рамка: следующий текст уходит в агент с `task: true`,
    а состояние снимается само, следующий вопрос снова обычный;
  * «Сколько мне осталось» отвечает текстом сервера и не зовёт модель.

Обоснование: docs/development/82 §3.3, docs/development/92 §8, решение 05.11.2026.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest

_states = gateway_modules.load("states")
AvailForm = _states.AvailForm
EventMove = _states.EventMove
TaskFrame = _states.TaskFrame


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeState:
    """Состояние FSM ровно в тех операциях, которые трогают обработчики."""

    def __init__(self, state=None, data: dict | None = None):
        self.state = state
        self.data: dict = dict(data or {})

    async def clear(self):
        self.state, self.data = None, {}

    async def set_state(self, value):
        self.state = value

    async def get_state(self):
        return self.state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return self.data

    def set(self, state=None):
        self.state = state
        return self


class FakePlaceholder:
    """Сообщение «Думаю…»: его удаляют или переписывают ответом."""

    def __init__(self):
        self.edited: list[str] = []
        self.deleted = False

    async def edit_text(self, text, reply_markup=None):
        self.edited.append(text)

    async def delete(self):
        self.deleted = True


class FakeMessage:
    def __init__(self, text: str = "", chat_id: int = 77):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[tuple[str, object]] = []
        self.placeholders: list[FakePlaceholder] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))
        placeholder = FakePlaceholder()
        self.placeholders.append(placeholder)
        return placeholder

    @property
    def last(self) -> str:
        return self.answers[-1][0]

    @property
    def shown(self) -> str:
        """Что человек прочитал: ответ сообщением или переписанное «Думаю…»."""
        if self.placeholders and self.placeholders[-1].edited:
            return self.placeholders[-1].edited[-1]
        return self.last


def _no_agent(*_args, **_kwargs):
    raise AssertionError("модель звать нельзя: этот путь обязан отвечать без неё")


# --------------------------------------------------------------- «Обнулить»
@pytest.mark.parametrize("state", [
    AvailForm.time,          # посреди формы занятости: «с 18:00 до 23:00»
    AvailForm.note,          # посреди комментария к дню
    EventMove.await_time,    # посреди назначения события: новое время спрашивали текстом
])
def test_обнулить_выпускает_из_любого_состояния(gw, monkeypatch, state):
    """«Обнулить» посреди формы и назначения возвращает чат в обычный режим.

    Именно это ломалось 05.11.2026: человек вводил время назначения, нажимал
    «Обнулить», и бот продолжал ждать время — потерянная нить назначения.
    """
    monkeypatch.setattr(gateway_modules.load("core"), "agent_ask", _no_agent)
    fsm = FakeState(state, {"bd": {"amount": 3000}, "event_id": 12})
    message = FakeMessage(gw.BTN_RESET)

    asyncio.run(gw.dialog_control(message, fsm))

    assert fsm.state is None, "состояние не снято: форма продолжит съедать следующий текст"
    assert fsm.data == {}, "недособранная карточка осталась в состоянии"
    assert message.last == gw.DIALOG_RESET_DONE
    assert "Сбросил" in message.last


def test_обнулить_не_отменяет_записанное(gw):
    """«Обнулить» — про диалог, а не про откат записи: откат остаётся у «Вернуть»."""
    source = gateway_modules.source("routers.menu")
    block = source[source.find("async def dialog_control"):
                   source.find("async def on_dialog_control")]
    assert "delete" not in block and "cancel" not in block
    assert "state.clear()" in block


# --------------------------------------------------------------- «Озадачить»
def test_озадачить_ставит_рамку_и_текст_уходит_агенту_с_task(gw, monkeypatch):
    """Следующий текст идёт в агент с `task: true`, рамка снимается после ответа."""
    calls: list[dict] = []

    async def fake_agent(chat_id, text, timeout=20, task=False):
        calls.append({"chat_id": chat_id, "text": text, "task": task})
        return {"ok": True, "answer": "Шаг 1: проверить зал. Шаг 2: записать операцию.",
                "scenario": "task"}

    monkeypatch.setattr(gateway_modules.load("core"), "agent_ask", fake_agent)
    fsm = FakeState()
    message = FakeMessage(gw.BTN_TASK)

    asyncio.run(gw.dialog_control(message, fsm))
    assert fsm.state == TaskFrame.await_text.state
    assert message.last == gw.TASK_FRAME_ASK

    task_message = FakeMessage("подготовь смету на реквизит")
    asyncio.run(gw.task_frame_text(task_message, fsm))

    assert calls == [{"chat_id": 77, "text": "подготовь смету на реквизит", "task": True}]
    assert fsm.state is None, "рамка одноразовая: после ответа состояние снимается"
    assert "Шаг 1" in task_message.shown


def test_рамка_не_режим_и_не_права(gw, monkeypatch):
    """«Озадачить» меняет формулировку задачи, а не права агента.

    Рамка живёт одно нажатие: после ответа состояние пустое, и следующий свободный
    текст идёт обычным путём (Ш-12) — переключателя режима по-прежнему нет.
    """
    calls: list[bool] = []

    async def fake_agent(chat_id, text, timeout=20, task=False):
        calls.append(task)
        return {"ok": True, "answer": "ответ", "scenario": "task"}

    monkeypatch.setattr(gateway_modules.load("core"), "agent_ask", fake_agent)
    fsm = FakeState()
    asyncio.run(gw.dialog_control(FakeMessage(gw.BTN_TASK), fsm))
    asyncio.run(gw.task_frame_text(FakeMessage("ещё одна задача"), fsm))

    assert calls == [True], "рамка ставит признак задачи, но не второй режим"
    assert fsm.state is None
    # обработчик рамки привязан к состоянию: без него текст идёт в on_text
    handler = next(h for h in gateway_modules.load("main").build_dispatcher()
                   .sub_routers[0].message.handlers
                   if h.callback.__name__ == "task_frame_text")
    assert "TaskFrame" in repr(handler.filters)


def test_область_рамки_снимается_явным_намерением(gw, monkeypatch):
    """Данные и заметка внутри рамки рамку снимают: «занят завтра» — не задача."""
    asked: list[dict] = []

    async def fake_busy(message, text, state):
        asked.append({"kind": "availability", "text": text})
        return True

    monkeypatch.setattr(gateway_modules.load("routers.availability"), "busy_card", fake_busy)
    fsm = FakeState(TaskFrame.await_text.state)
    message = FakeMessage("занят завтра с 10 до 14")

    asyncio.run(gw.task_frame_text(message, fsm))

    assert asked == [{"kind": "availability", "text": "занят завтра с 10 до 14"}]
    assert fsm.state is None


def test_рамка_возвращает_диалог_назначения(gw, monkeypatch):
    """Фраза внутри рамки проходит обработку назначения, а не теряется.

    Человек был в диалоге назначения, нажал «Озадачить» и вместо задачи написал
    данные («занят завтра с 10 до 14»). Состояние назначения возвращается до
    `on_text`, поэтому реплика не превращается в задачу и не пропадает.
    """
    menu = gateway_modules.load("routers.menu")
    seen: dict = {}

    async def fake_on_text(message, state):
        seen["state"] = await state.get_state()
        seen["data"] = await state.get_data()

    monkeypatch.setattr(menu, "on_text", fake_on_text)
    fsm = FakeState(_states.AssignDraft.await_time.state,
                    {"ad": "назначь репетицию с Витей в четверг"})

    asyncio.run(gw.dialog_control(FakeMessage(gw.BTN_TASK), fsm))
    assert fsm.state == TaskFrame.await_text.state
    asyncio.run(gw.task_frame_text(FakeMessage("занят завтра с 10 до 14"), fsm))

    assert seen["state"] == _states.AssignDraft.await_time.state, \
        "диалог назначения не восстановлен: реплика ушла бы в задачу"
    assert seen["data"]["ad"] == "назначь репетицию с Витей в четверг"


def test_start_и_menu_снимают_рамку(gw):
    """`/start` и `/menu` — явный выход из рамки (docs/development/108 §5)."""
    source = gateway_modules.source("routers.menu")
    start = source[source.find("async def start("):source.find("async def about_me(")]
    assert "state.clear()" in start
    menu_at = source.find("async def menu_cmd(")
    menu = source[menu_at:source.find("\n@router", menu_at)]
    assert "state.clear()" in menu, "рамка «Озадачить» переживёт /menu"


# ------------------------------------------------- «Сколько мне осталось»
def test_сколько_осталось_отвечает_цифрами_без_модели(gw, monkeypatch):
    """Ответ приходит текстом сервера: модель не зовётся, квота не тратится."""
    called: list[str] = []

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        called.append(f"{method} {path}")
        assert payload is None, "читающий маршрут не принимает тело"
        return {"ok": True, "tokens_left": 1234, "tokens_limit": 2000,
                "text": "Токенов на месяц осталось: 1 234 из 2 000."}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("core"), "agent_ask", _no_agent)
    fsm = FakeState(TaskFrame.await_text.state)
    message = FakeMessage(gw.BTN_QUOTA)

    asyncio.run(gw.dialog_control(message, fsm))

    assert called == ["GET /tools/quota/me"]
    assert "1 234" in message.last and "2 000" in message.last
    assert fsm.state is None, "вопрос о лимитах — другое намерение, рамку снимает"


def test_сколько_осталось_без_сервера_не_выдумывает(gw, monkeypatch):
    """tool-api недоступен — бот говорит об этом, а не отвечает нулями."""
    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        return {"ok": False, "error": "tool_api_unavailable"}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    message = FakeMessage(gw.BTN_QUOTA)
    asyncio.run(gw.dialog_control(message, FakeState()))
    assert message.last == gw.QUOTA_UNAVAILABLE
    assert "0" not in message.last
