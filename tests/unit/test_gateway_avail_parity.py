"""«Обычная неделя» и состояния дня в чате — FR-C10, FR-C9, FR-D6 (карта AUD-5).

Три обещания реестра `docs/architecture/25` §9.4:

  * З5: «Запомнить неделю» и «Заполнить по обычной неделе» зовут существующие
    серверные пути (`/tools/availability/template/from-week`, `…/apply`), тех же,
    что у экрана `/webapp/availability/template/*`;
  * З1/З4: форма дня умеет «свободен» и «не уверен» и комментарий к дню
    (`WeekDayIn`: status, from, to, note);
  * запись дня идёт тем же путём, что у экрана — `/tools/availability/week`, то
    есть тем же обработчиком `_save_week`, а не своей вставкой в `availability_hour`.

Обоснование: FR-C9, FR-C10, FR-D6, карта t_0c0d023d.
"""

import asyncio
import datetime as dt
import types

import gateway_modules
import pytest

TODAY = dt.date(2026, 11, 4)          # среда: понедельник недели — 02.11.2026
MONDAY = "2026-11-02"
TEMPLATE = {"ok": True, "filled": 2, "days": [
    {"weekday": 0, "status": "busy", "from": None, "to": "18:00",
     "note": "", "text": "занят до 18:00"},
    {"weekday": 2, "status": "free", "from": "10:00", "to": None,
     "note": "в Самаре", "text": "свободен с 10:00 (в Самаре)"},
]}


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeState:
    def __init__(self, **data):
        self.data: dict = dict(data)
        self.state = None

    async def clear(self):
        self.data, self.state = {}, None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.state = value

    async def get_state(self):
        return self.state

    async def get_data(self):
        return dict(self.data)


class FakeMessage:
    def __init__(self, text: str = "", chat_id: int = 1):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage | None = None):
        self.data = data
        self.message = message or FakeMessage()
        self.alerts: list[str] = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)


def _install(monkeypatch, gw, *, caps=None, template=None, replies=None):
    """Подменить tool-api: запомнить вызовы и отдать заранее заданные ответы."""
    calls: list[tuple[str, str, dict | None]] = []
    overrides = dict(replies or {})

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        calls.append((method, path, payload))
        if path in overrides:
            answer = overrides[path]
            return answer(payload) if callable(answer) else answer
        if path == "/tools/availability/template":
            return template if template is not None else TEMPLATE
        return {"ok": True}

    async def fake_caps_of(chat_id):
        return ["schedule.availability.self"] if caps is None else caps

    async def fake_today(chat_id):
        return TODAY

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", fake_caps_of)
    monkeypatch.setattr(gateway_modules.load("core"), "today_local", fake_today)
    return calls


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _data(markup) -> set[str]:
    return {button.callback_data for row in markup.inline_keyboard for button in row}


# ------------------------------------------------------------ «обычная неделя» (З5)
def test_fr_c10_week_показывает_обычную_неделю_с_сервера(gw, monkeypatch):
    """FR-C10: `/week` читает шаблон существующим путём и показывает канонный текст."""
    calls = _install(monkeypatch, gw)
    message = FakeMessage("/week")
    asyncio.run(gw.cmd_week(message, FakeState()))

    assert calls[0][:2] == ("GET", "/tools/availability/template")
    text, markup = message.answers[-1]
    assert "Как обычно:" in text
    assert "понедельник: занят до 18:00" in text
    assert "среда: свободен с 10:00 (в Самаре)" in text
    assert _labels(markup) == ["Запомнить неделю", "Заполнить по обычной неделе"]
    assert _data(markup) == {"week:remember", "week:apply"}


def test_fr_c10_пустой_шаблон_объясняет_что_делать(gw, monkeypatch):
    """FR-C10: шаблона нет — не пустой экран, а подсказка и те же кнопки."""
    _install(monkeypatch, gw, template={"ok": True, "filled": 0, "days": []})
    message = FakeMessage("/week")
    asyncio.run(gw.cmd_week(message, FakeState()))
    text, markup = message.answers[-1]
    assert "Пока не сохранена" in text
    assert "Запомнить неделю" in _labels(markup)


def test_fr_c10_запомнить_зовёт_путь_экрана_с_понедельником(gw, monkeypatch):
    """FR-C10: «Запомнить неделю» — тот же путь, что у экрана, с текущей неделей."""
    calls = _install(monkeypatch, gw, replies={
        "/tools/availability/template/from-week": {"ok": True, "filled": 2,
                                                   "days": TEMPLATE["days"]},
    })
    cb = FakeCallback("week:remember")
    asyncio.run(gw.week_action(cb, FakeState()))

    post = [call for call in calls if call[1] == "/tools/availability/template/from-week"]
    assert post, f"путь экрана не вызван: {calls}"
    assert post[0][2] == {"start": MONDAY}
    assert "Запомнил обычную неделю: 2 дн." in cb.message.edits[-1][0]


def test_fr_c10_заполнить_не_затирает_отмеченные_дни(gw, monkeypatch):
    """FR-C10: заполнение идёт с `overwrite=False` — отмеченные дни не трогаем."""
    calls = _install(monkeypatch, gw, replies={
        "/tools/availability/template/apply": {
            "ok": True, "applied": True, "start": MONDAY, "hours": 20,
            "days": [{"day": f"{MONDAY}", "text": "занят до 18:00"}],
            "skipped": ["2026-11-04"]},
    })
    cb = FakeCallback("week:apply")
    asyncio.run(gw.week_action(cb, FakeState()))

    post = [call for call in calls if call[1] == "/tools/availability/template/apply"]
    assert post, f"путь экрана не вызван: {calls}"
    assert post[0][2] == {"start": MONDAY, "apply": True, "overwrite": False}
    text = cb.message.edits[-1][0]
    assert "Заполнил по обычной неделе: 1 дн., пропущено отмеченных: 1." in text


def test_fr_c10_ошибки_называются_по_человечески(gw, monkeypatch):
    """FR-C10: «нечего запоминать» и «нет шаблона» — разные ответы, а не общий отказ."""
    _install(monkeypatch, gw, replies={
        "/tools/availability/template/from-week": {"ok": False, "error": "nothing_to_save"},
        "/tools/availability/template/apply": {"ok": False, "error": "no_template"},
    })
    remember = FakeCallback("week:remember")
    asyncio.run(gw.week_action(remember, FakeState()))
    assert "нет отмеченных дней" in remember.alerts[0]

    apply = FakeCallback("week:apply")
    asyncio.run(gw.week_action(apply, FakeState()))
    assert "ещё не сохранена" in apply.alerts[0]


def test_fr_c10_без_права_не_показываем(gw, monkeypatch):
    """FR-C10: шаблон — своя занятость, без права отмечать его не показываем вовсе."""
    calls = _install(monkeypatch, gw, caps=["budget.read.self"])
    message = FakeMessage("/week")
    asyncio.run(gw.cmd_week(message, FakeState()))
    assert calls == []
    assert "участник студии" in message.answers[-1][0]


def test_fr_c10_устаревшая_кнопка_не_зовёт_сервер(gw, monkeypatch):
    """FR-C10: неизвестное действие из старого сообщения — отказ, а не догадка."""
    calls = _install(monkeypatch, gw)
    cb = FakeCallback("week:перенести")
    asyncio.run(gw.week_action(cb, FakeState()))
    assert all("template" not in path for _, path, _ in calls)
    assert cb.alerts and "устарела" in cb.alerts[0]


# ------------------------------------------------ состояния дня и комментарий (З1/З4)
def test_fr_d6_состояние_дня_спрашивается_кнопками(gw, monkeypatch):
    """FR-D6: после дня спрашиваем состояние — три значения модели, а не только «занят»."""
    _install(monkeypatch, gw)
    cb = FakeCallback("av:day:0")
    state = FakeState()
    asyncio.run(gw.av_day(cb, state))

    assert state.state == "AvailForm:status"
    text, markup = cb.message.edits[-1]
    assert text == "Что в этот день?"
    assert _labels(markup) == ["Занят", "Свободен", "Не уверен"]
    assert _data(markup) == {"av:s:busy", "av:s:free", "av:s:uncertain"}


def test_fr_d6_не_уверен_не_спрашивает_время(gw, monkeypatch):
    """FR-D6: «не уверен» — состояние без границ; модель такое сочетание запрещает."""
    _install(monkeypatch, gw)
    cb = FakeCallback("av:s:uncertain")
    state = FakeState(av_day="2026-11-05")
    asyncio.run(gw.av_status(cb, state))

    assert state.state == "AvailForm:note"
    assert cb.message.edits[-1][0].startswith("Комментарий к дню")


def test_fr_c9_занят_с_границами_и_комментарием_пишется_путём_экрана(gw, monkeypatch):
    """FR-C9: занят 18:00–конец дня с комментарием — запись тем же путём, что экран."""
    calls = _install(monkeypatch, gw)
    state = FakeState(av_day="2026-11-05", av_kind="busy", av_start=18, av_end=24,
                      av_note="в Самаре")
    cb = FakeCallback("av:ok", FakeMessage(chat_id=1))
    asyncio.run(gw.av_save(cb, state))

    post = [call for call in calls if call[0] == "POST"]
    assert len(post) == 1, f"ожидалась одна запись: {calls}"
    method, path, payload = post[0]
    assert path == "/tools/availability/week", "день пишется тем же путём, что экран"
    assert payload == {"start": "2026-11-05", "days": [
        {"day": "2026-11-05", "status": "busy", "from_time": "18:00",
         "to_time": None, "note": "в Самаре"}]}
    assert "Записал: 05.11.2026, четверг — занят с 18:00." in cb.message.edits[-1][0]
    assert "Комментарий: в Самаре." in cb.message.edits[-1][0]


def test_fr_d6_свободен_до_пятнадцати(gw, monkeypatch):
    """FR-D6: «свободен до 15» — статус free, верхняя граница, начало дня пустое."""
    calls = _install(monkeypatch, gw)
    state = FakeState(av_day="2026-11-05", av_kind="free", av_start=0, av_end=15)
    asyncio.run(gw.av_save(FakeCallback("av:ok"), state))

    payload = [call for call in calls if call[1] == "/tools/availability/week"][0][2]
    assert payload["days"][0] == {"day": "2026-11-05", "status": "free",
                                  "from_time": None, "to_time": "15:00", "note": None}


def test_fr_d6_не_уверен_записывается_без_границ(gw, monkeypatch):
    """FR-D6: «не уверен» уходит в модель без from/to — границы ему не нужны."""
    calls = _install(monkeypatch, gw)
    state = FakeState(av_day="2026-11-05", av_kind="uncertain", av_start=None,
                      av_end=None, av_note="может, смогу")
    asyncio.run(gw.av_save(FakeCallback("av:ok"), state))

    payload = [call for call in calls if call[1] == "/tools/availability/week"][0][2]
    assert payload["days"][0] == {"day": "2026-11-05", "status": "uncertain",
                                  "note": "может, смогу"}


def test_c5_часы_до_восьми_идут_отметкой_по_часам(gw, monkeypatch):
    """C-5: карточка дня живёт в 08:00–24:00, часы до восьми — отдельная отметка.

    Иначе «занят с 5 до 9» превратился бы в карточку «занят весь день»: модель
    материализует день начиная с 08:00 (`availability_card.to_hours`).
    """
    calls = _install(monkeypatch, gw)
    state = FakeState(av_day="2026-11-05", av_kind="busy", av_start=5, av_end=9,
                      av_note="аэропорт")
    asyncio.run(gw.av_save(FakeCallback("av:ok"), state))

    post = [call for call in calls if call[0] == "POST"][0]
    assert post[1] == "/tools/availability/commit"
    assert post[2]["allow_outside_hours"] is True
    assert post[2]["raw_text"] == "занят с 05:00 до 09:00 (аэропорт)"
    assert post[2]["kind"] == "busy"


def test_fr_c9_комментарий_из_фразы_команды(gw):
    """FR-C9: `/busy занят 28.10 до 18 (в Самаре)` — комментарий берём из скобок."""
    assert gw._note_of("занят 28.10 до 18 (в Самаре)") == "в Самаре"
    assert gw._note_of("занят 28.10 до 18") == ""
    assert gw._note_of("занят 28.10 (  с  двух   пробелов )") == "с двух пробелов"


def test_fr_c9_карточка_дня_говорит_языком_канона(gw):
    """FR-C9: формулировки карточки совпадают с каноном `availability_card.to_text`."""
    card = gw.day_card_text
    assert card("2026-11-05", "busy", 10, 14) == (
        "05.11.2026, четверг — занят с 10:00 до 14:00.\n\nЗаписать?")
    assert "занят до 15:00" in card("2026-11-05", "busy", None, 15, question=False)
    assert "занят с 18:00" in card("2026-11-05", "busy", 18, None, question=False)
    assert "свободен" in card("2026-11-05", "free", question=False)
    assert "не уверен" in card("2026-11-05", "uncertain", question=False)
    assert card("не дата", "busy", question=False).startswith("не дата")


def test_fr_c9_форма_дня_пишет_через_путь_экрана(gw):
    """FR-C9, ADR-13 п.2: у формы тот же путь записи, что у экрана `/webapp/...`.

    Проверяем кодом: гейтвей зовёт `/tools/availability/week` (общий `_save_week`),
    и не заводит своей записи в `availability_hour`.
    """
    source = gateway_modules.source("routers.availability")
    assert '"/tools/availability/week"' in source
    assert "insert into availability_hour" not in source, "чат не пишет таблицу сам"
    assert "/tools/availability/template/from-week" in source
    assert "/tools/availability/template/apply" in source
