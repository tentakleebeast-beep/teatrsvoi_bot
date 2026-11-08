"""V-009 (обращение №17): знакомство — опросник с подтверждением перед записью.

Бот спрашивает короткое имя, телефон и день рождения (если их нет), дорогу,
спектакли, роли и тренинги. Ответы копятся в состоянии, в профиль уходят одним
запросом только после сводки «так записать?». Проверяем разбор, план вопросов,
накопление и обе ветки подтверждения.

Телефон в фикстурах вымышленный (+7 917 123-45-67): настоящий номер в тест не попадает,
менять его на живой нельзя.
"""

import asyncio
import importlib.util
import pathlib
import sys
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


@pytest.fixture(scope="module")
def intro(gw):
    return sys.modules["intro"]


# ------------------------------------------------------------------ разбор ответов

@pytest.mark.parametrize("text,expected", [
    ("40", 40),
    ("40 минут", 40),
    ("сорок минут", 40),
    ("двадцать пять минут", 25),
    ("полчаса", 30),
    ("половину часа", 30),
    ("пол часа", 30),
    ("час", 60),
    ("полтора часа", 90),
    ("1,5 часа", 90),
    ("два часа", 120),
    ("0", 0),
    ("300", None),          # больше четырёх часов — недорога, а опечатка
    ("часто хожу пешком", None),
    ("не знаю", None),
    ("", None),
])
def test_дорога_в_минутах(intro, text, expected):
    assert intro.parse_travel_minutes(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Меня зовут Витя", "Витя"),
    ("витя, но лучше Виктор", "витя"),
    ("Просто Марк", "Марк"),
    ("Витя.", "Витя"),
    ("Аня", "Аня"),
    ("я", None),            # одна буква — не имя
    ("нет", None),
])
def test_короткое_имя(intro, text, expected):
    assert intro.parse_alias(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("+7 917 123-45-67", "+79171234567"),
    ("8 (917) 123-45-67", "+79171234567"),
    ("9171234567", "+79171234567"),
    ("123", None),          # не телефон
    ("нет", None),
])
def test_телефон_V009(intro, text, expected):
    assert intro.parse_phone(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("12.03.1990", "1990-03-12"),
    ("1990-03-12", "1990-03-12"),
    ("12 марта 1990", "1990-03-12"),
    ("31.02.1990", None),   # такого дня нет
    ("12.03", None),        # без года не записываем
    ("нет", None),
])
def test_день_рождения_V009(intro, text, expected):
    assert intro.parse_birthday(text) == expected


def test_заметка_для_помощника(intro):
    assert intro.parse_text_field("не ставьте репетиции до 11") == "не ставьте репетиции до 11"
    assert intro.parse_text_field("нет") is None
    assert intro.parse_text_field("   ") is None
    assert len(intro.parse_text_field("а" * 700, limit=600)) == 600


def test_тело_запроса_по_шагам(intro):
    assert intro.parse_step("alias", "Витя") == {"alias_add": "Витя"}
    assert intro.parse_step("travel", "полчаса") == {"travel_minutes": 30}
    assert intro.parse_step("phone", "+7 917 123-45-67") == {"phone": "+79171234567"}
    assert intro.parse_step("birthday", "12.03.1990") == {"birth_date": "1990-03-12"}
    assert intro.parse_step("plays", "Чайка, Гамлет") == {"plays": "Чайка, Гамлет"}
    assert intro.parse_step("roles", "Треплев") == {"roles_note": "Треплев"}
    assert intro.parse_step("trainings", "импровизация") == {"interests": "импровизация"}
    assert intro.parse_step("manifest", "приезжаю на машине") == {
        "ai_manifest": "приезжаю на машине"}
    assert intro.parse_step("alias", "нет") == {}
    assert intro.parse_step("unknown", "что угодно") == {}


def test_план_не_переспрашивает_известное_V009(intro):
    """Телефон и день рождения спрашиваются только когда их нет; имя — если одно слово."""
    plan = intro.build_plan({"name": "Иванов Виктор", "phone": "+79171234567",
                             "birth_date": "1990-03-12"})
    assert plan[:2] == ["alias", "travel"]
    assert "phone" not in plan and "birthday" not in plan and "name" not in plan
    plan = intro.build_plan({"name": "Витя"})
    assert plan[0] == "name"
    assert "phone" in plan and "birthday" in plan


def test_сводка_и_одно_тело_запроса_V009(intro):
    answers = {"alias": "Витя", "phone": "+79171234567", "birthday": "1990-03-12",
               "travel": 30}
    text = intro.summary(answers)
    assert "Короткое имя: Витя" in text
    assert "Дорога: 30 мин" in text
    assert "12.03.1990" in text
    assert intro.build_patch(answers) == {
        "alias_add": "Витя", "phone": "+79171234567", "birth_date": "1990-03-12",
        "travel_minutes": 30}


# ------------------------------------------------------------------ сценарий в боте

class FakeSent:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.markup = None

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.markup = kwargs.get("reply_markup")
        return self


class FakeMessage:
    def __init__(self, text: str = "", chat_id: int = 901) -> None:
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[str] = []
        self.markup = None

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        self.markup = kwargs.get("reply_markup")
        return FakeSent(text)

    async def edit_text(self, text, **kwargs):
        self.answers.append(text)
        self.markup = kwargs.get("reply_markup")
        return FakeSent(text)

    async def delete(self):
        return None


class FakeCallback:
    def __init__(self, message: FakeMessage, data: str = "") -> None:
        self.message = message
        self.data = data
        self.answered = False

    async def answer(self, text=None, **kwargs):
        self.answered = True


class FakeState:
    def __init__(self, state=None, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.state = state

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


def buttons(markup) -> list[str]:
    if markup is None:
        return []
    return [b.text for row in markup.inline_keyboard for b in row]


PROFILE = {"ok": True, "name": "Иванов Виктор", "phone": "+79171234567",
           "birth_date": "1990-03-12", "travel_minutes": 60}


def answer(gw, monkeypatch, step: str, text: str, plan=None, answers=None,
           llm_result: dict | None = None):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload, method))
        if path == "/tools/profile/intro-parse":
            return llm_result if llm_result is not None else {
                "ok": False, "error": "llm_unavailable"}
        return {"ok": True}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    plan = plan or ["alias", "travel", "manifest"]
    state = FakeState(getattr(gw.Intro, step).state,
                      {"plan": plan, "answers": dict(answers or {})})
    message = FakeMessage(text)
    asyncio.run(gw.intro_answer(message, state, step))
    return calls, message, state


def test_старт_строит_план_по_профилю_V009(gw, monkeypatch):
    async def fake_api(path, payload, chat_id, method="POST"):
        assert path == "/tools/profile/me" and method == "GET"
        return PROFILE

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    state = FakeState(None)
    message = FakeMessage()
    asyncio.run(gw.intro_start(message, 901, state))
    assert state.data["plan"] == ["alias", "travel", "plays", "roles",
                                  "trainings", "manifest"]
    assert state.state == gw.Intro.alias.state
    assert "Как вас называть" in message.answers[-1]
    assert "Пропустить" in buttons(message.markup)


def test_ответ_копит_и_не_пишет_в_профиль_V009(gw, monkeypatch):
    calls, message, state = answer(gw, monkeypatch, "alias", "Меня зовут Витя")
    assert calls == []                       # до подтверждения ничего не ушло
    assert state.data["answers"] == {"alias": "Витя"}
    assert state.state == gw.Intro.travel.state
    assert "дорога" in message.answers[-1].lower()


def test_последний_ответ_показывает_сводку_и_кнопки_V009(gw, monkeypatch):
    calls, message, state = answer(gw, monkeypatch, "manifest", "приезжаю на машине",
                                   answers={"alias": "Витя"})
    assert calls == []
    assert state.state == gw.Intro.confirm.state
    assert "Так записать?" in message.answers[-1]
    assert "приезжаю на машине" in message.answers[-1]
    assert buttons(message.markup) == ["Записать", "Отменить"]


def test_непонятый_телефон_переспрашивает_и_не_копит_V009(gw, monkeypatch):
    calls, message, state = answer(gw, monkeypatch, "phone", "спросите у Маши",
                                   plan=["phone", "travel"])
    assert calls == []
    assert state.data["answers"] == {}
    assert state.state == gw.Intro.phone.state
    assert "Не понял номер" in message.answers[-1]


def test_пропуск_ничего_не_копит_V009(gw, monkeypatch):
    calls, message, state = answer(gw, monkeypatch, "phone", "нет",
                                   plan=["phone", "travel"])
    assert calls == []
    assert state.data["answers"] == {}
    assert state.state == gw.Intro.travel.state


def test_подтверждение_пишет_одним_запросом_V009(gw, monkeypatch):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    state = FakeState(gw.Intro.confirm.state, {
        "plan": ["alias", "phone", "birthday", "travel"],
        "answers": {"alias": "Витя", "phone": "+79171234567",
                    "birthday": "1990-03-12", "travel": 30}})
    message = FakeMessage()
    asyncio.run(gw.intro_save(FakeCallback(message), state))
    assert calls == [("/tools/profile/me", {
        "alias_add": "Витя", "phone": "+79171234567",
        "birth_date": "1990-03-12", "travel_minutes": 30})]
    assert state.state is None and state.data == {}
    assert "Спасибо" in message.answers[-1]


def test_отмена_не_пишет_и_очищает_V009(gw, monkeypatch):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    state = FakeState(gw.Intro.confirm.state, {"answers": {"alias": "Витя"}})
    message = FakeMessage()
    asyncio.run(gw.intro_cancel(FakeCallback(message), state))
    assert calls == []
    assert state.state is None and state.data == {}
    assert "ничего не записал" in message.answers[-1]


def test_пустая_сводка_не_пишет_V009(gw, monkeypatch):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    state = FakeState(gw.Intro.confirm.state, {"answers": {}})
    message = FakeMessage()
    asyncio.run(gw.intro_save(FakeCallback(message), state))
    assert calls == []
    assert "ничего не записал" in message.answers[-1]


def test_дорога_разобрана_моделью_когда_правила_молчат(gw, monkeypatch):
    calls, message, state = answer(
        gw, monkeypatch, "travel", "недалеко, пешком",
        llm_result={"ok": True, "patch": {"travel_minutes": 20}})
    assert [path for path, _, _ in calls] == ["/tools/profile/intro-parse"]
    assert state.data["answers"] == {"travel": 20}
    assert state.state == gw.Intro.manifest.state


def test_модель_не_пишет_чужое_поле(gw, monkeypatch):
    calls, message, state = answer(
        gw, monkeypatch, "travel", "много",
        llm_result={"ok": True, "patch": {"alias_add": "Витя"}})
    assert [path for path, _, _ in calls] == ["/tools/profile/intro-parse"]
    assert state.data["answers"] == {}
    assert "Не понял время" in message.answers[-1]


def test_кнопка_пропустить_идёт_дальше(gw):
    state = FakeState(gw.Intro.alias.state, {"plan": ["alias", "travel"]})
    message = FakeMessage()
    asyncio.run(gw.intro_skip_current(message, state))
    assert state.state == gw.Intro.travel.state
    assert "дорога" in message.answers[-1].lower()


def test_кнопка_вне_сценария_подсказывает_команду(gw):
    state = FakeState(None)
    message = FakeMessage()
    asyncio.run(gw.intro_skip_current(message, state))
    assert "/intro" in message.answers[-1]


def test_регистрация_из_списка_начинает_знакомство(gw, monkeypatch):
    """V-009: после привязки к человеку из списка сразу идёт знакомство."""
    started: list[int] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        assert path == "/tools/participants/attach"
        return {"ok": True, "participant": {"display_name": "Фомина Арина",
                                            "role": "troupe", "roles": ["troupe"],
                                            "capabilities": []}}

    async def fake_menu(*args, **kwargs):
        return None

    async def fake_intro(message, chat_id, state):
        started.append(chat_id)

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("routers.menu"), "send_menu", fake_menu)
    monkeypatch.setattr(gateway_modules.load("routers.intro"), "intro_start", fake_intro)
    state = FakeState(None, {"code": "ABCD1234"})
    message = FakeMessage()
    asyncio.run(gw.reg_pick(FakeCallback(message, "reg:pick:5"), state))
    assert started == [901]
