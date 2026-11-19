"""Пункт меню «Приглашения» выпускает код, а не отвечает справкой.

Обоснование: docs/development/174-invite-instruction.md §11.4 и §11.11 (AC-8) —
вход выдачи кода из меню чата. Дефект найден на стенде E9-5: в `run_menu_action`
ветка сравнивала подпись с литералом «Пригласить», которого в меню нет, поэтому
пункт «Приглашения» (`texts.TITLE_INVITES`, право `registration.code.issue`)
проваливался в общий ответ `keyboards.help_text` — человек ждал код, а получал
«Что умею». Здесь два сторожа: поведение (`mi:Приглашения` → код) и состав меню
(каждая подпись из `MENU_ACTIONS` имеет свою ветку, веток на несуществующие
подписи нет). Второй закрывает класс ошибки, а не один ярлык: расхождение
«строка в меню» ↔ «строка в обработчике» больше не доживёт до приёмки.
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

CODE = "K7QM2XPD"
EXPIRES = "2026-11-24T21:00:00+04:00"
ADMIN = ["*"]
#: Подпись пункта — из меню, а не литералом: тест должен падать от правки меню,
#: только если обработчик за ней не поспел.
INVITES_LABEL = "Приглашения"

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

    def __init__(self, chat_id: int = 1):
        self.text = ""
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[dict] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append({"text": text, "markup": reply_markup,
                             "parse_mode": parse_mode})


def _patch_api(gw, monkeypatch, reply) -> list[dict]:
    """Подменить `core.api`: роутеры читают его через модуль `core`."""
    calls: list[dict] = []

    async def fake_api(path, payload, chat_id, method="POST", timeout=25):
        calls.append({"path": path, "payload": payload, "chat_id": chat_id})
        return reply

    monkeypatch.setattr(gw.core, "api", fake_api)
    return calls


def _menu_action_body() -> str:
    """Исходник `run_menu_action` целиком: от объявления до следующего обработчика."""
    source = MENU_PY.read_text(encoding="utf-8")
    start = source.index("async def run_menu_action(")
    end = source.index("\n@router.", start)
    return source[start:end]


def test_пункт_меню_приглашения_выпускает_код(gw, monkeypatch) -> None:
    """Репро дефекта E9-5: нажатие «Приглашения» шлёт код с кнопкой, а не справку."""
    calls = _patch_api(gw, monkeypatch,
                       {"ok": True, "code": CODE, "expires_at": EXPIRES})

    async def fake_caps_of(chat_id):
        return ADMIN

    monkeypatch.setattr(gw.core, "caps_of", fake_caps_of)
    message = FakeMessage()

    asyncio.run(gw.run_menu_action(message, FakeState(), INVITES_LABEL))

    assert len(message.answers) == 1, "на нажатие пункта — ровно один ответ"
    sent = message.answers[0]
    assert not sent["text"].startswith("Что умею"), \
        "пункт снова провалился в общую справку вместо выпуска кода"
    assert sent["parse_mode"] == "HTML"
    assert f"<code>{CODE}</code>" in sent["text"]
    assert "1. Зайди в бота" in sent["text"], "инструкция получателю идёт вместе с кодом"
    assert sent["markup"].inline_keyboard[0][0].copy_text.text == CODE
    assert calls == [{"path": "/admin/invite",
                      "payload": {"role": "troupe", "ttl_days": 7}, "chat_id": 1}], \
        "право, роль и TTL приглашения карта не меняет"


def test_подпись_пункта_совпадает_с_константой_меню(gw) -> None:
    """«Приглашения» в чате подписано `texts.TITLE_INVITES`, а не строкой по месту."""
    assert INVITES_LABEL == gw.TITLE_INVITES
    assert INVITES_LABEL in gw.MENU_ACTIONS
    assert INVITES_LABEL in dict(gw.menu_groups(ADMIN))["Управление"]


def test_каждая_подпись_меню_имеет_свою_ветку(gw) -> None:
    """Сторож класса ошибки: меню и обработчик не расходятся по подписям."""
    body = _menu_action_body()
    handled = {getattr(gw, name) for name in CONST_COMPARE_RE.findall(body)}
    declared = set(gw.MENU_ACTIONS)

    missing = sorted(declared - handled)
    assert not missing, f"пункты меню без ветки в run_menu_action: {missing}"


def test_в_обработчике_нет_сравнений_с_литералами(gw) -> None:
    """Вторая половина сторожа: недостижимых веток на выдуманные подписи нет."""
    literals = LITERAL_COMPARE_RE.findall(_menu_action_body())

    assert literals == [], f"сравнение подписи с литералом вместо texts.*: {literals}"
