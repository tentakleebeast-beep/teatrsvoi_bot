"""
Ш-20: главное меню по правам и список участников таблицей.

Обращение №13: в меню висели кнопки, недоступные человеку («Админ-панель»,
«Бюджет», «Расход»), бот на них отвечал отказом. Обращение №14, часть 1:
список участников в админ-панели шёл строками «Имя — роли» без колонок.
"""

import asyncio
import pathlib
import sys
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "services" / "tg-gateway"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


ADMIN = ["*"]
ORG = ["participants.view", "participants.manage", "registration.approve",
       "registration.code.issue", "budget.read", "budget.write", "budget.read.self",
       "schedule.read.all", "schedule.write", "schedule.availability.self"]
TROUPE = ["schedule.availability.self", "budget.read.self"]
STUDENT = ["schedule.availability.self"]


def keyboard_labels(markup):
    return [b.text for row in markup.keyboard for b in row]


class FakeState:
    """Минимальное состояние FSM: меню только очищает его и ставит новое."""

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
    def __init__(self, text: str = ""):
        self.text = text
        self.chat = types.SimpleNamespace(id=1)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


def inline_labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def inline_data(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def menu_titles(gw, caps) -> list[str]:
    """Подписи групп главного меню — то, что строит production `menu_groups`."""
    return [label for label, _ in gw.menu_groups(caps)]


def test_у_админа_видны_группы_и_помощь(gw):
    """Ш-47 / V-008 + docs/82: группы — для подменю, клавиатура — управление диалогом."""
    assert menu_titles(gw, ADMIN) == ["Деньги", "Занятость", "Профиль",
                                     "Люди", "Админ", "Помощь"]
    # В чате — ровно три контроля разговора (решение 05.11.2026)
    keyboard = keyboard_labels(gw.menu_kb(ADMIN))
    assert set(keyboard) == {"Обнулить", "Озадачить", "Сколько мне осталось"}
    assert "Админ" not in keyboard and "Помощь" not in keyboard
    items = {text for _, texts in gw.menu_groups(ADMIN) for text in texts}
    assert items == set(gw.MENU_ACTIONS)


def test_состав_групп_по_решению_28_10(gw):
    """Ш-47, №21: «Деньги» — доход, расход, бюджет, взносы; «Время» — занятость, расписание, окна."""
    groups = dict(gw.menu_groups(ADMIN))
    assert groups["Деньги"] == ["Расход и доход", "Бюджет", "Сверка листов", "Мои взносы"]
    assert groups["Занятость"] == ["Занятость", "Расписание", "Поиск окон"]
    assert groups["Профиль"] == ["Мой профиль"]
    assert groups["Помощь"] == ["Обратная связь", "Что умею", "Справка"]
    # «Участники» переехали в группу «Люди» картой Э1: право `participants.view` есть
    # и у педагога, а группа «Админ» ему не рисуется (docs/architecture/140 §4.1).
    assert groups["Люди"] == ["Участники"]
    assert groups["Админ"] == ["Админ-панель", "Заявки", "Приглашения"]


def test_труппа_видит_свои_пункты_в_группах(gw):
    """Ш-47: права решают не только видимость группы, но и состав её пунктов."""
    assert menu_titles(gw, TROUPE) == ["Деньги", "Занятость", "Профиль", "Помощь"]
    groups = dict(gw.menu_groups(TROUPE))
    assert groups["Деньги"] == ["Мои взносы"]
    assert groups["Занятость"] == ["Занятость"]


def test_пустые_группы_не_рисуются(gw):
    """Ш-47: без прав на деньги и админку этих групп в меню нет вовсе."""
    assert menu_titles(gw, STUDENT) == ["Занятость", "Профиль", "Помощь"]
    assert menu_titles(gw, []) == ["Профиль", "Помощь"]
    for caps in (ADMIN, ORG, TROUPE, STUDENT, []):
        markup = gw.menu_kb(caps)
        assert markup.keyboard, "клавиатура пустая"
        assert all(row for row in markup.keyboard), f"пустая строка кнопок: {caps}"
        assert set(keyboard_labels(markup)) == {"Обнулить", "Озадачить",
                                                "Сколько мне осталось"}


def test_без_особых_прав_остаются_профиль_и_помощь(gw):
    """Ш-47: пустой набор возможностей не оставляет человека без клавиатуры.

    Клавиатура — управление разговором, поэтому она не пустеет и не зависит от прав:
    три контроля видны всем (решение 05.11.2026).
    """
    labels = menu_titles(gw, [])
    markup = gw.menu_kb([])
    assert labels == ["Профиль", "Помощь"]
    assert set(keyboard_labels(markup)) == set(gw.DIALOG_CONTROLS)
    assert markup.resize_keyboard is True and markup.is_persistent is True


def test_подменю_содержит_только_свои_пункты(gw):
    """Ш-47: подменю группы — кнопки её пунктов, а не всего меню."""
    markup = gw.group_kb(ADMIN, "Деньги")
    assert inline_labels(markup) == ["Расход и доход", "Бюджет", "Сверка листов", "Мои взносы"]
    assert inline_data(markup) == [f"mi:{text}" for text in inline_labels(markup)]
    assert inline_labels(gw.group_kb(TROUPE, "Деньги")) == ["Мои взносы"]
    assert inline_labels(gw.group_kb(TROUPE, "Админ")) == []


def test_кнопка_группы_открывает_подменю(gw, monkeypatch):
    """Ш-47: «Деньги» — это навигация, а не форма расхода."""
    async def fake_caps_of(chat_id):
        return ADMIN

    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", fake_caps_of)
    message = FakeMessage("Деньги")
    state = FakeState()
    asyncio.run(gw.menu_action(message, state))
    text, markup = message.answers[-1]
    assert text.startswith("Деньги")
    assert inline_labels(markup)[0] == "Расход и доход"
    assert state.state is None


def test_пункт_подменю_выполняет_действие_по_праву(gw, monkeypatch):
    """Ш-47: кнопка из подменю проходит ту же проверку прав, что и раньше."""
    calls: list[str] = []

    async def fake_run(message, state, label):
        calls.append(label)

    async def fake_caps_of(chat_id):
        return TROUPE

    monkeypatch.setattr(gateway_modules.load("routers.menu"), "run_menu_action", fake_run)
    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", fake_caps_of)

    class Cb:
        def __init__(self, data):
            self.data = data
            self.message = FakeMessage("")
            self.alerts: list[str] = []

        async def answer(self, text=None, show_alert=False):
            if text:
                self.alerts.append(text)

    allowed = Cb("mi:Занятость")
    asyncio.run(gw.menu_item_cb(allowed, FakeState()))
    assert calls == ["Занятость"]

    denied = Cb("mi:Бюджет")      # кнопка осталась от старого меню
    asyncio.run(gw.menu_item_cb(denied, FakeState()))
    assert calls == ["Занятость"]
    assert "недоступен" in denied.message.answers[-1][0]

    stale = Cb("mi:Удалённый пункт")
    asyncio.run(gw.menu_item_cb(stale, FakeState()))
    assert calls == ["Занятость"]
    assert stale.alerts and "устарела" in stale.alerts[0]


def test_мой_профиль_показывает_роли_права_и_тариф(gw, monkeypatch):
    """Ш-47, №19: «Мой профиль» — имя, роли, права и взносы, без чужих данных."""

    async def fake_api(path, payload, chat_id, method="POST"):
        if path == "/tools/participants/me":
            return {"ok": True, "data": {"display_name": "Арина", "roles": ["troupe"],
                                         "capabilities": ["schedule.availability.self"]}}
        assert path == "/tools/dues/me"
        return {"ok": True, "tariff": {"amount": 3000, "tariff_from": "2026-09-01"},
                "current": {"period": "2026-10", "due": 3000, "paid": 0,
                            "settled": False, "charged": True},
                "debt": 0, "data": []}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    message = FakeMessage("")
    asyncio.run(gw.run_menu_action(message, FakeState(), "Мой профиль"))
    text = message.answers[-1][0]
    assert text.startswith("Арина")
    assert "актёр труппы" in text
    assert "своя занятость" in text
    assert "3 000 ₽" in text
    assert "За октябрь 2026 не учтено" in text


def test_меню_отправляется_по_правам(gw):
    """Ш-47: send_menu и /menu строят клавиатуру из caps, а не общий MAIN_MENU."""
    source = gateway_modules.source("routers.menu", "texts")
    start = source.find("async def send_menu")
    end = source.find("async def start")
    assert 0 < start < end
    body = source[start:end]
    assert "menu_kb(caps)" in body
    assert "MAIN_MENU" not in body
    cmd = source[source.find("async def menu_cmd"):]
    cmd = cmd[:cmd.find("@router.message(Command(\"admin\"))")]
    assert "menu_kb(caps)" in cmd and "MAIN_MENU" not in cmd


def test_таблица_участников_с_колонками(gw):
    people = [
        {"display_name": "Виктор", "roles": ["admin", "org"], "status": "active"},
        {"display_name": "Арина", "roles": ["troupe"], "status": "active"},
        {"display_name": "Новичок", "roles": ["student"], "status": "pending"},
    ]
    text = gw.people_table(people)
    assert text.startswith("<pre>") and text.endswith("</pre>")
    lines = text[len("<pre>"):-len("</pre>")].split("\n")
    assert lines[0].startswith("ИМЯ") and "РОЛИ" in lines[0] and "СТАТУС" in lines[0]
    assert lines[1].startswith("---")
    assert len(lines) == 5
    assert "активен" in lines[2] and "заявка" in lines[4]
    # колонки выровнены: статус начинается на одной позиции
    assert lines[2].index("активен") == lines[3].index("активен")


def test_таблица_экранирует_html(gw):
    text = gw.people_table([{"display_name": "<b>Витя</b>", "roles": ["admin"],
                             "status": "active"}])
    assert "<b>" not in text
    assert "&lt;b&gt;Витя&lt;/b&gt;" in text


def test_таблица_обрезает_длинные_значения_и_лишние_строки(gw):
    long_name = "Очень длинное имя участника студии"
    text = gw.people_table([{"display_name": long_name, "roles": ["admin"], "status": "active"}])
    assert long_name not in text and "…" in text

    many = [{"display_name": f"Человек {i}", "roles": ["troupe"], "status": "active"}
            for i in range(300)]
    big = gw.people_table(many)
    assert "… ещё" in big
    assert len(big) < 4096, "сообщение не должно упираться в лимит Telegram"


def test_панель_участников_шлёт_html(gw):
    source = gateway_modules.source("routers.admin")
    start = source.find('F.data == "panel:people"')
    # Границы функции, а не «1200 символов от декоратора»: окно по длине ломалось от
    # любой строки в теле (Ш-07 добавила строку про заблокированных) и ничего не
    # гарантировало — проверка должна видеть весь обработчик.
    end = source.find("\n\n\nasync def", start)
    body = source[start:end if end != -1 else len(source)]
    assert "people_table(" in body
    assert 'parse_mode="HTML"' in body


def test_название_раздела_админ_короткое(gw):
    """№28: «Администрирование» переносилось на кнопке — раздел называется «Админ»."""
    assert "Админ" in menu_titles(gw, ADMIN)
    assert "Администрирование" not in menu_titles(gw, ADMIN)
    assert max(len(label) for label in menu_titles(gw, ADMIN)) <= 12


def test_обратная_связь_живёт_в_помощи(gw):
    """№29: в «Профиле» только профиль, обратная связь — в «Помощи»."""
    groups = dict(gw.menu_groups(ADMIN))
    assert "Обратная связь" not in groups["Профиль"]
    assert inline_labels(gw.group_kb(ADMIN, "Помощь")) == ["Обратная связь", "Что умею", "Справка"]
    assert "Обратная связь" in gw.help_text(ADMIN)


def test_пункт_что_умею_отвечает_справкой(gw, monkeypatch):
    """№29: пункт «Что умею» из раздела «Помощь» показывает ту же справку, что /help."""
    async def fake_caps_of(chat_id):
        return ADMIN

    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", fake_caps_of)
    message = FakeMessage("Что умею")
    asyncio.run(gw.run_menu_action(message, FakeState(), "Что умею"))
    text, _ = message.answers[-1]
    assert text.startswith("Что умею:")
    assert "Админ" in text


def test_Ш49_справка_только_по_праву(gw):
    """Ш-49 (V-010): «Справка» видна только с howto.read, остальным — не рисуется."""
    with_right = ADMIN + ["howto.read"]
    assert "Справка" in dict(gw.menu_groups(with_right))["Помощь"]
    assert "Справка" not in dict(gw.menu_groups(TROUPE))["Помощь"]
    assert "Справка" not in dict(gw.menu_groups(ORG))["Помощь"]
    assert "Справка" not in gw.help_text(TROUPE)
    assert "Справка" in gw.help_text(with_right)


def test_Ш49_справка_отдаётся_разделами(gw, monkeypatch):
    """Ш-49: бот шлёт тексты из tool-api отдельными сообщениями, а не одним комком."""
    asked: list[str] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        asked.append(f"{method} {path}")
        return {"ok": True, "sections": [
            {"role": "admin", "title": "Администратор", "text": "текст админа"},
            {"role": "org", "title": "Организатор", "text": "текст орга"},
        ]}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    message = FakeMessage("")
    asyncio.run(gw.run_menu_action(message, FakeState(), "Справка"))
    assert asked == ["GET /tools/howto"]
    assert [text for text, _ in message.answers] == [
        "Администратор\n\nтекст админа", "Организатор\n\nтекст орга"]


def test_Ш49_справка_не_падает_без_api(gw, monkeypatch):
    """Ш-49: tool-api недоступен — бот отвечает словом, а не тишиной."""
    async def fake_api(path, payload, chat_id, method="POST"):
        return {"ok": False, "error": "tool_api_unavailable"}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    message = FakeMessage("")
    asyncio.run(gw.run_menu_action(message, FakeState(), "Справка"))
    assert message.answers and "недоступна" in message.answers[-1][0]
