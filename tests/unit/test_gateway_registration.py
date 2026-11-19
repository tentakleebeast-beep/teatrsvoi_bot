"""
Обращение №12: бот молчит на инвайт-код при регистрации.

Дефект: разбор кода жил в `on_text`, а сам `on_text` был зарегистрирован с
`StateFilter(None)`. После `/start` состояние `Reg.await_code` уже не None,
поэтому сообщение с кодом не обрабатывал никто — бот молчал.

Обращение №8: кнопка «Права» в админ-панели. У `org` были `participants.view`
и `registration.approve` (второе снято миграцией `068`, 19.11.2026), но панель
требовала только `participants.manage`,
поэтому организатор получал «Панель доступна администратору». Теперь вход в
панель и её пункты считаются по возможностям.
"""

import asyncio
import importlib.util
import pathlib
import re
import sys
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"

CODE = "ABCD1234"

ADMIN_CAPS = ["*"]
ORG_CAPS = ["budget.import", "budget.read", "budget.read.self", "budget.write", "calendar.write",
            "feedback.send", "files.access", "notes.write", "participants.contact",
            "participants.view", "poll.manage", "schedule.availability.self",
            "schedule.read.all", "schedule.slots", "schedule.write", "system.health"]
TROUPE_CAPS = ["budget.read.self", "schedule.availability.self", "system.health"]


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeSent:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.markup = None

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.markup = kwargs.get("reply_markup")
        return self

    async def delete(self):
        return None


class FakeMessage:
    def __init__(self, text: str, chat_id: int = 901) -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[tuple[str, dict]] = []
        self.markup = None
        self.from_user = types.SimpleNamespace(full_name="Новый Человек")

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        self.markup = kwargs.get("reply_markup")
        return FakeSent(text)

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))
        self.markup = kwargs.get("reply_markup")
        return FakeSent(text)

    async def delete(self):
        return None


class FakeState:
    def __init__(self, state=None) -> None:
        self.data: dict = {}
        self.state = state

    async def get_state(self):
        return self.state

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def clear(self):
        self.data.clear()
        self.state = None


def buttons(markup) -> list[str]:
    if markup is None:
        return []
    return [b.text for row in markup.inline_keyboard for b in row]


def run_code(gw, monkeypatch, answer: dict):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        if path == "/tools/participants/check_code":
            return answer
        raise AssertionError(f"неожиданный вызов {path}")

    async def fake_menu(message, chat_id, name, roles, caps):
        calls.append(("menu", name))

    async def no_router(text, user=None, categories=None):
        raise AssertionError("код ушёл в n8n-роутер")

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("routers.menu"), "send_menu", fake_menu)
    monkeypatch.setattr(gateway_modules.load("core"), "router_ask", no_router)

    state = FakeState(gw.Reg.await_code.state)
    message = FakeMessage(CODE)
    asyncio.run(gw.on_text(message, state))
    return calls, message, state


def test_код_принимается_и_показывает_кандидатов(gw, monkeypatch):
    calls, message, state = run_code(gw, monkeypatch, {
        "ok": True, "role": "troupe",
        "candidates": [{"id": 7, "display_name": "Фомина Арина"}],
    })
    assert calls[0][0] == "/tools/participants/check_code"
    # Ш-46: список кандидатов — отдельное состояние, ввод букв сужает кнопки
    assert state.state == gw.Reg.choose.state
    assert state.data.get("code") == CODE
    assert any("Код принят" in text for text, _ in message.answers)
    assert "Фомина Арина" in buttons(message.markup)
    assert "Меня нет в списке" in buttons(message.markup)


def run_filter(gw, monkeypatch, answer: dict, query: str = "Смирнова"):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, method))
        assert path.startswith("/tools/participants/search?q=")
        return answer

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    state = FakeState(gw.Reg.choose.state)
    state.data["code"] = CODE
    message = FakeMessage(query)
    asyncio.run(gw.reg_filter(message, state))
    return calls, message, state


def test_ввод_букв_сужает_список_кандидатов(gw, monkeypatch):
    """Ш-46 (V-006): «Смирнова» находит «Дарья Смирнова», и кнопка одна — своя."""
    calls, message, state = run_filter(gw, monkeypatch, {
        "ok": True,
        "candidates": [{"id": 42, "display_name": "Дарья Смирнова", "role": "troupe"}],
    })
    assert calls and calls[0][1] == "GET"
    assert state.state == gw.Reg.choose.state
    assert "Дарья Смирнова" in buttons(message.markup)
    assert "Меня нет в списке" in buttons(message.markup)


def test_пустой_поиск_ведёт_в_заявку(gw, monkeypatch):
    """Ш-46: никого не нашли — человек пишет имя, чужой профиль не привязывается."""
    _, message, state = run_filter(gw, monkeypatch, {"ok": True, "candidates": []},
                                   query="Несуществующий")
    assert state.state == gw.Reg.await_name.state
    assert any("никого не нашёл" in text for text, _ in message.answers)
    assert message.markup is None, "выбирать не из чего — кнопок быть не должно"


def test_неверный_код_объясняет_причину(gw, monkeypatch):
    _, message, state = run_code(gw, monkeypatch, {"ok": False, "error": "code_not_found"})
    assert any("Код не найден" in text for text, _ in message.answers)
    assert state.state == gw.Reg.await_code.state


def test_истёкший_код_просит_новый(gw, monkeypatch):
    _, message, _ = run_code(gw, monkeypatch, {"ok": False, "error": "code_expired"})
    assert any("попросите новый" in text for text, _ in message.answers)


def test_уже_зарегистрирован_сразу_открывает_меню(gw, monkeypatch):
    calls, message, state = run_code(gw, monkeypatch, {
        "error": "already_registered",
        "participant": {"display_name": "Фомина Арина", "role": "troupe",
                        "roles": ["troupe"], "capabilities": TROUPE_CAPS},
    })
    assert ("menu", "Фомина Арина") in calls
    assert state.state is None


def test_фильтр_свободного_текста_пропускает_состояние_ожидания_кода(gw):
    catch_all = next(h for h in gw.router.message.handlers
                     if h.callback.__name__ == "on_text")
    state_filter = catch_all.filters[0].callback
    assert gw.Reg.await_code in state_filter.states, (
        "сообщение с кодом снова не дойдёт до обработчика")
    assert None in state_filter.states, "свободный текст вне регистрации тоже нужен"


def test_панель_организатора_без_прав_и_с_аудитом(gw):
    """AUD-3 / Л4 (FR-A6): «Права» — по participants.manage, а не по просмотру.

    Раньше пункт висел на `participants.view`, и организатор видел вход, за
    которым его ждал отказ. Заявок в панели у него тоже нет с миграции 068:
    обработка заявок — работа администратора. Рабочие пункты (бюджет, люди,
    сверка листов) остаются.
    """
    rows = gw.panel_rows(ORG_CAPS)
    labels = [b[0] for row in rows for b in row]
    assert "Права" not in labels, "права выдаются по participants.manage"
    assert "Заявки" not in labels, "заявки — администратор (миграция 068)"
    assert "Аудит" not in labels, "org без audit.view не должен видеть аудит"
    assert "Бюджет" in labels


def test_панель_админа_со_звёздочкой_полная(gw):
    labels = [b[0] for row in gw.panel_rows(ADMIN_CAPS) for b in row]
    assert {"Участники", "Заявки", "Права", "Бюджет", "Аудит", "Сервисы",
            "Выпустить инвайт-код"} <= set(labels)


def test_панель_труппы_пуста(gw):
    assert gw.panel_rows(["budget.read.self"]) == []


def run_panel(gw, monkeypatch, caps):
    async def fake_caps_of(chat_id):
        return caps

    monkeypatch.setattr(gateway_modules.load("core"), "caps_of", fake_caps_of)
    message = FakeMessage("Админ-панель")
    asyncio.run(gw.panel_root(message, edit=False))
    return message


def test_организатору_панель_не_нужна(gw, monkeypatch):
    """Решение 29.10.2026: панель только у администратора, орг работает кнопками меню."""
    message = run_panel(gw, monkeypatch, ORG_CAPS)
    assert not any("Админ-панель" in text for text, _ in message.answers)
    assert any("доступна организатору" in text for text, _ in message.answers)
    assert buttons(message.markup) == []


def test_заявки_и_приглашения_у_того_у_кого_есть_права(gw):
    """Пункты меню — по праву, а не по названию роли.

    С 068 у организатора прав на заявки и коды нет: обработка заявок
    и выпуск приглашений — работа администратора. Проверяем обе стороны:
    у администратора пункты на месте, у организатора их нет ни в одной группе,
    а «Участники» он не теряет.
    """
    admin_groups = dict(gw.menu_groups(ADMIN_CAPS))
    assert admin_groups["Люди"] == ["Участники"]
    assert {"Заявки", "Приглашения"} <= set(admin_groups["Управление"])

    org_groups = dict(gw.menu_groups(ORG_CAPS))
    org_items = {item for items in org_groups.values() for item in items}
    assert org_groups["Люди"] == ["Участники"]
    assert "Заявки" not in org_items
    assert "Приглашения" not in org_items


def test_труппа_не_видит_панель(gw, monkeypatch):
    message = run_panel(gw, monkeypatch, TROUPE_CAPS)
    assert any("доступна организатору" in text for text, _ in message.answers)
    assert buttons(message.markup) == []


def test_точка_входа_в_конце_файла():
    """Обработчики не должны объявляться после запуска polling (ревью 27.10)."""
    source = gateway_modules.source()
    assert source.count('if __name__ == "__main__":') == 1
    entry = source.index('if __name__ == "__main__":')
    handlers = [m.start() for m in re.finditer(r"@router\.", source)]
    assert handlers, "в файле нет обработчиков"
    late = [h for h in handlers if h > entry]
    assert not late, f"{len(late)} обработчиков объявлены после запуска polling"


def test_кнопка_сверки_зарегистрирована():
    source = gateway_modules.source()
    assert 'F.data == "panel:reconcile"' in source
    assert 'F.data == "rec:apply"' in source
