"""Пункт «Знакомство» открывает дозаполнение профиля, а кодов в чате больше нет.

Обоснование: docs/development/182 §2.3 и §4.3.3 (V-117/V-118) — решение владельца
21.11.2026: код выпускается только в Mini App, а в группе «Профиль» появляется
пункт «Знакомство». Файл прежде проверял чатовый выпуск кода («Приглашения» → код)
и переписан вместе с ним: чатового пути больше не существует, поэтому сторож
теперь обратный — такого пункта в меню нет, а «Знакомство» зовёт `intro_fill`,
а не общую справку.

Второй сторож — класс ошибки, а не один ярлык: каждая подпись из `MENU_ACTIONS`
имеет свою ветку в `run_menu_action`. Расхождение «строка в меню» ↔ «строка
в обработчике» (из-за него пункт когда-то падал в «Что умею») не доживёт до приёмки.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
MENU_PY = GATEWAY_APP / "routers" / "menu.py"

ADMIN = ["*"]

#: Сравнения подписи с константой: `if label == texts.TITLE_X:`
CONST_COMPARE_RE = re.compile(r"if\s+label\s*==\s*texts\.([A-Z_][A-Z0-9_]*)")
#: Сравнения подписи с литералом — источник дефекта, их быть не должно.
LITERAL_COMPARE_RE = re.compile(r"if\s+label\s*==\s*[\"']([^\"']+)[\"']")


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeState:
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
    """`Message` без Telegram: запоминает ответы, разметку и `parse_mode`."""

    def __init__(self, text: str = "", chat_id: int = 1):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[dict] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append({"text": text, "markup": reply_markup,
                             "parse_mode": parse_mode})


def _run(gw, monkeypatch, label: str):
    called: list[str] = []

    async def fake_intro_fill(message, state):
        called.append("intro_fill")

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        return {"ok": True}

    async def fake_help(*args, **kwargs):
        return "Что умею"

    monkeypatch.setattr(gateway_modules.load("routers.intro"), "intro_fill", fake_intro_fill)
    monkeypatch.setattr(gw.core, "api", fake_api)
    monkeypatch.setattr(gw.keyboards, "help_text", fake_help)
    message = FakeMessage()
    asyncio.run(gw.run_menu_action(message, FakeState(), label))
    return called, message


def _menu_action_body() -> str:
    """Исходник `run_menu_action` целиком: от объявления до следующего обработчика."""
    source = MENU_PY.read_text(encoding="utf-8")
    start = source.index("async def run_menu_action(")
    end = source.index("\n@router.", start)
    return source[start:end]


def test_пункт_знакомство_открывает_дозаполнение_профиля(gw, monkeypatch) -> None:
    """«Профиль» → «Знакомство»: обработчик `intro_fill`, а не общая справка."""
    called, message = _run(gw, monkeypatch, gw.texts.TITLE_FILL)

    assert called == ["intro_fill"], "пункт снова провалился в чужую ветку"
    assert not [item for item in message.answers if item["text"].startswith("Что умею")], \
        "пункт отвечает справкой вместо дозаполнения"


def test_подпись_знакомства_равна_решению_владельца(gw) -> None:
    """Подпись дословно «Знакомство» (не «Дозаполнить профиль»), docs/182 §7.3."""
    assert gw.texts.TITLE_FILL == "Знакомство"
    assert gw.texts.TITLE_FILL in gw.MENU_ACTIONS
    assert gw.texts.TITLE_FILL in dict(gw.menu_groups(ADMIN))["Профиль"]


def test_чатового_выпуска_кодов_нет(gw) -> None:
    """V-118: пункта «Приглашения» в меню нет, ветки выпуска в обработчике — тоже."""
    groups = dict(gw.menu_groups(ADMIN))
    items = {item for values in groups.values() for item in values}

    assert "Приглашения" not in items
    assert not hasattr(gw.texts, "TITLE_INVITES"), "константа подписи не удалена"
    body = _menu_action_body()
    assert "TITLE_INVITES" not in body
    assert "send_invite" not in body


def test_каждая_подпись_меню_имеет_свою_ветку(gw) -> None:
    """Сторож класса ошибки: меню и обработчик не расходятся по подписям."""
    body = _menu_action_body()
    handled = {getattr(gw.texts, name) for name in CONST_COMPARE_RE.findall(body)
               if hasattr(gw.texts, name)}
    declared = set(gw.MENU_ACTIONS)

    missing = sorted(declared - handled)
    assert not missing, f"пункты меню без ветки в run_menu_action: {missing}"


def test_в_обработчике_нет_сравнений_с_литералами(gw) -> None:
    """Вторая половина сторожа: недостижимых веток на выдуманные подписи нет."""
    literals = LITERAL_COMPARE_RE.findall(_menu_action_body())

    assert literals == [], f"сравнение подписи с литералом вместо texts.*: {literals}"
