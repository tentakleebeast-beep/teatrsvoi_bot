"""Тихие часы и типы уведомлений в чате — FR-G3, строка О3 реестра (карта AUD-5).

`docs/development/12` §5 обещает настройку «из Telegram», но до этой карты её не было:
экран `/webapp/notifications` умел, чат — нет. Проверяем три вещи:

  * команда `/quiet` (`/тихие`) и кнопки показывают значения сервера, а не свои;
  * запись идёт тем же путём, что у экрана — постом на `/tools/notifications/settings`,
    где tool-api зовёт общий обработчик `_settings_save`;
  * «выкл» даёт пустой интервал 00:00–00:00, а не отдельное поле.

Обоснование: FR-G3, карта t_0c0d023d (AUD-5), docs/architecture/25 §9.2 п.2.
"""

import asyncio
import types

import gateway_modules
import pytest

CARD = {"rehearsals": True, "dues": True, "schedule_changes": True,
        "quiet_from": "20:00", "quiet_to": "08:00"}


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeState:
    """Минимальное FSM: команда только очищает состояние."""

    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def clear(self):
        self.data, self.state = {}, None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.state = value

    async def get_state(self):
        return self.state


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


def _install(monkeypatch, gw, *, settings: dict | None = None, caps=None,
             fail: bool = False):
    """Подменить tool-api и права: запомнить каждый вызов и хранить настройки."""
    calls: list[tuple[str, str, dict | None]] = []
    state = {"settings": dict(settings or CARD)}

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        calls.append((method, path, payload))
        if path != "/tools/notifications/settings":
            return {"ok": False, "error": "unexpected_path"}
        if fail:
            return {"ok": False, "error": "tool_api_unavailable"}
        if method == "GET":
            return {"ok": True, "settings": dict(state["settings"])}
        state["settings"].update(payload)
        return {"ok": True, "settings": dict(state["settings"])}

    async def fake_caps_of(chat_id):
        return ["schedule.availability.self"] if caps is None else caps

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", fake_caps_of)
    return calls, state


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _data(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_fr_g3_тихие_часы_показываются_из_сервера(gw, monkeypatch):
    """FR-G3: `/quiet` читает значения серверным путём, а не держит свои умолчания."""
    calls, _ = _install(monkeypatch, gw, settings={**CARD, "quiet_from": "21:30",
                                                   "quiet_to": "09:00"})
    message = FakeMessage("/quiet")
    asyncio.run(gw.cmd_quiet(message, FakeState()))

    assert calls[0][:2] == ("GET", "/tools/notifications/settings")
    text, markup = message.answers[-1]
    assert "Тихие часы: 21:30–09:00" in text
    assert "Приходят: репетиции, взносы, изменения расписания" in text
    assert _labels(markup) == ["Репетиции", "Взносы", "Изменения расписания",
                              "20:00–08:00", "Выключить тихие часы", "Своё время"]
    assert _data(markup)[0] == "quiet:type:rehearsals"


def test_fr_g3_русская_команда_тоже_работает(gw, monkeypatch):
    """FR-G3: `/тихие` Telegram командой не считает — приходит текстом и должна сработать."""
    calls, _ = _install(monkeypatch, gw)
    message = FakeMessage("/тихие")
    asyncio.run(gw.cmd_quiet(message, FakeState()))
    assert calls and message.answers, "русская команда не дошла до обработчика"
    assert gw.parse_quiet_hours  # обработчик разбора доступен тестам


@pytest.mark.parametrize("arg, expected", [
    ("21:00-09:00", ("21:00", "09:00")),
    ("20-8", ("20:00", "08:00")),
    ("7:05-23:15", ("07:05", "23:15")),
    ("20:00–08:00", ("20:00", "08:00")),      # типографский тире из кнопки
    ("выкл", ("00:00", "00:00")),
    ("", None),
    ("когда-нибудь", None),
    ("25:00-09:00", None),
])
def test_fr_g3_разбор_интервала_тихих_часов(gw, arg, expected):
    """FR-G3: человек пишет интервал по-разному — разбираем детерминированно."""
    assert gw.parse_quiet_hours(arg) == expected


def test_fr_g3_команда_с_аргументом_пишет_все_поля(gw, monkeypatch):
    """FR-G3: запись идёт полным набором полей — как с экрана, без частичной записи."""
    calls, state = _install(monkeypatch, gw)
    message = FakeMessage("/quiet 21:30-09:15")
    asyncio.run(gw.cmd_quiet(message, FakeState()))

    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1, f"ожидалась одна запись, вызовы: {calls}"
    method, path, payload = posts[0]
    assert (method, path) == ("POST", "/tools/notifications/settings")
    assert payload == {"rehearsals": True, "dues": True, "schedule_changes": True,
                       "quiet_from": "21:30", "quiet_to": "09:15"}
    assert state["settings"]["quiet_from"] == "21:30"
    assert "Сохранено" in message.answers[-1][0]


def test_fr_g3_выключение_тихих_часов_даёт_пустой_интервал(gw, monkeypatch):
    """FR-G3: «выкл» — 00:00–00:00, в такой интервал не попадает ни один час."""
    calls, _ = _install(monkeypatch, gw)
    asyncio.run(gw.cmd_quiet(FakeMessage("/quiet выкл"), FakeState()))
    posts = [call for call in calls if call[0] == "POST"]
    assert posts and posts[-1][2]["quiet_from"] == "00:00"
    assert posts[-1][2]["quiet_to"] == "00:00"


def test_fr_g3_кривой_аргумент_не_пишет(gw, monkeypatch):
    """FR-G3: не разобрали — говорим как писать и ничего не меняем."""
    calls, _ = _install(monkeypatch, gw)
    message = FakeMessage("/quiet когда-нибудь")
    asyncio.run(gw.cmd_quiet(message, FakeState()))
    assert all(method == "GET" for method, _, _ in calls)
    assert "20:00-08:00" in message.answers[-1][0]


def test_fr_g3_кнопка_переключает_тип_уведомления(gw, monkeypatch):
    """FR-G3: кнопка «Взносы» выключает взносы и сохраняет остальные значения как были."""
    calls, state = _install(monkeypatch, gw)
    cb = FakeCallback("quiet:type:dues")
    asyncio.run(gw.quiet_action(cb, FakeState()))

    assert calls[-1][2] == {"rehearsals": True, "dues": False, "schedule_changes": True,
                            "quiet_from": "20:00", "quiet_to": "08:00"}
    assert state["settings"]["dues"] is False
    text, markup = cb.message.edits[-1]
    assert "Выключено: взносы" in text
    assert _labels(markup)[1] == "Взносы"


def test_fr_g3_кнопка_ставит_привычный_интервал(gw, monkeypatch):
    """FR-G3: «20:00–08:00» — тот же путь записи, интервал берётся из колбэка."""
    calls, _ = _install(monkeypatch, gw, settings={**CARD, "quiet_from": "22:00",
                                                   "quiet_to": "10:00"})
    asyncio.run(gw.quiet_action(FakeCallback("quiet:preset:20:00-08:00"), FakeState()))
    assert calls[-1][2]["quiet_from"] == "20:00"
    assert calls[-1][2]["quiet_to"] == "08:00"


def test_fr_g3_своё_время_просит_написать_команду(gw, monkeypatch):
    """FR-G3: «Своё время» не заводит отдельную форму — тот же разбор аргумента команды."""
    calls, _ = _install(monkeypatch, gw)
    cb = FakeCallback("quiet:custom")
    asyncio.run(gw.quiet_action(cb, FakeState()))
    assert calls == [], "кнопка «Своё время» ничего не записывает сама"
    assert "/quiet" in cb.message.edits[-1][0]


def test_fr_g3_устаревшая_кнопка_не_пишет(gw, monkeypatch):
    """FR-G3: кнопка от старого сообщения не должна сохранять чужие значения."""
    calls, _ = _install(monkeypatch, gw)
    cb = FakeCallback("quiet:type:неизвестно")
    asyncio.run(gw.quiet_action(cb, FakeState()))
    assert all(method == "GET" for method, _, _ in calls)
    assert cb.alerts and "устарела" in cb.alerts[0]


def test_fr_g3_недоступный_сервис_объясняется_человеку(gw, monkeypatch):
    """FR-G3: tool-api молчит — честный отказ, а не «по умолчанию 20:00–08:00»."""
    _install(monkeypatch, gw, fail=True)
    message = FakeMessage("/quiet")
    asyncio.run(gw.cmd_quiet(message, FakeState()))
    assert "недоступны" in message.answers[-1][0]


def test_fr_g3_без_участника_команда_отказывает(gw, monkeypatch):
    """FR-G3: тихие часы — личная настройка участника, постороннему их не показываем."""
    calls, _ = _install(monkeypatch, gw, caps=[])
    message = FakeMessage("/quiet")
    asyncio.run(gw.cmd_quiet(message, FakeState()))
    assert calls == []
    assert "участник студии" in message.answers[-1][0]


def test_fr_g3_чат_зовёт_ту_же_точку_что_экран(gw):
    """FR-G3, ADR-13 п.2: у чата тот же путь записи, а не копия экранного маршрута.

    Экранный маршрут требует токен Mini App и подписи initData; из чата в него не
    попасть, поэтому оболочка чата ходит на `/tools/notifications/settings`, а
    tool-api зовёт общий обработчик `_settings_save`.
    """
    source = gateway_modules.source("routers.menu")
    assert 'api("/tools/notifications/settings"' in source
    assert 'api("/webapp/notifications' not in source, "чат не должен звать экранный маршрут"
    # и в tool-api экранная оболочка зовёт ту же функцию записи
    tool_api = (gateway_modules.ROOT / "services" / "tool-api" / "app" / "api"
                / "webapp_api.py").read_text(encoding="utf-8")
    assert tool_api.count("_settings_save(who[\"id\"]") == 2, (
        "и экран, и чат должны звать один обработчик записи")
