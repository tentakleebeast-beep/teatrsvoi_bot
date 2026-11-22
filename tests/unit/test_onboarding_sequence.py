"""Онбординг: код → привязка → знакомство, без меню и без заявок.

Сценарий E13 (docs/development/182 §1): код принят — бот называет человека и
сразу начинает знакомство; меню приходит после него. Ошибки кода говорят
своими словами (§1.3), а не «Код не подошёл». Педагог сначала выбирает
дисциплину, потом привязка и знакомство (§1.2).

Обоснование: FR-G16 (первый вход), FR-A2 (код приглашения), FR-A5 (роли),
docs/development/182 §1.2, §1.3, §6.2 п. 1.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))

import gateway_modules  # noqa: E402

from tests.unit.test_gateway_registration import (  # noqa: E402
    FakeMessage,
    FakeState,
    run_code,
)


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def test_код_принят_и_сразу_знакомство(gw, monkeypatch):
    calls, message, state = run_code(
        gw, monkeypatch, {"ok": True, "role": "troupe"},
        attach={"ok": True,
                "participant": {"display_name": "Фомина Арина", "role": "troupe"}})

    texts = [text for text, _ in message.answers]
    assert any("Код принят: Фомина Арина." in text for text in texts)
    assert ("intro", 901) in calls, "знакомство — первое сообщение после привязки"
    assert not any(call[0] == "menu" for call in calls)


def test_ошибки_кода_говорят_своими_словами(gw, monkeypatch):
    """`code_not_personal`, `rate_limited`, `participant_blocked` — из §1.3."""
    cases = {
        "code_not_personal": "Этот код больше не действует — попросите администратора "
                             "выпустить новый.",
        "rate_limited": "Слишком много попыток ввода кода. Попробуйте через час.",
        "participant_blocked": "Доступ к боту закрыт. Напишите администратору студии.",
    }
    for error, expected in cases.items():
        _, message, state = run_code(gw, monkeypatch, {"ok": False, "error": error})
        texts = [text for text, _ in message.answers]
        assert expected in texts, f"нет текста для {error}: {texts}"
        assert "Код не подошёл." not in texts, f"{error} ушёл в общую фразу"
        assert state.state == gw.Reg.await_code.state, "после ошибки ждём новый код"


def _callback(gw, monkeypatch, attach: dict, data: str = "reg:disc:music"):
    calls: list[tuple] = []

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.append((path, payload))
        if path == "/tools/participants/attach":
            return attach
        raise AssertionError(f"неожиданный вызов {path}")

    async def fake_intro(message, chat_id, state):
        calls.append(("intro", chat_id))

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    monkeypatch.setattr(gateway_modules.load("routers.intro"), "intro_start", fake_intro)

    message = FakeMessage("")
    state = FakeState(gw.Reg.await_discipline.state)
    state.data["code"] = "PERSONA1"
    answered: list[str] = []

    class FakeCallback:
        def __init__(self) -> None:
            self.data = data
            self.message = message

        async def answer(self, text=None, **kwargs):
            answered.append(text or "")

    cb = FakeCallback()
    asyncio.run(gw.reg_discipline(cb, state))
    return calls, message, state, answered


def test_педагог_выбирает_дисциплину_и_идёт_в_знакомство(gw, monkeypatch):
    calls, message, state, _ = _callback(
        gw, monkeypatch,
        {"ok": True, "participant": {"display_name": "Педагог Музыки", "role": "master"}})

    attach_payload = next(payload for path, payload in calls
                          if path == "/tools/participants/attach")
    assert attach_payload["discipline_code"] == "music"
    assert attach_payload["code"] == "PERSONA1"
    texts = [text for text, _ in message.answers]
    assert any("Код принят: Педагог Музыки. Дисциплина: Музыка." in text for text in texts)
    assert ("intro", 901) in calls
    assert state.state is None


def test_педагог_без_кода_в_состоянии_просит_начать_заново(gw, monkeypatch):
    """Кнопка дисциплины без кода в состоянии — тупик, и он назван словами."""
    async def fake_api(path, payload, chat_id, method="POST"):
        raise AssertionError("без кода привязка не зовётся")

    monkeypatch.setattr(gateway_modules.load("core"), "api", fake_api)
    message = FakeMessage("")
    state = FakeState(gw.Reg.await_discipline.state)
    answered: list[str] = []

    class FakeCallback:
        data = "reg:disc:acting"

        def __init__(self) -> None:
            self.message = message

        async def answer(self, text=None, **kwargs):
            answered.append(text or "")

    asyncio.run(gw.reg_discipline(FakeCallback(), state))
    assert answered == ["Начните заново: /start"]


def test_педагогу_вопрос_о_дисциплине_задаётся_до_привязки(gw, monkeypatch):
    """`needs_discipline` из check_code — вопрос и кнопки, привязки ещё нет."""
    calls, message, state = run_code(
        gw, monkeypatch, {"ok": True, "role": "master", "needs_discipline": True})

    assert any("Какую дисциплину вы преподаёте?" in text for text, _ in message.answers)
    assert state.state == gw.Reg.await_discipline.state
    assert not any(path == "/tools/participants/attach" for path, _ in calls)


def test_дважды_привязанный_код_не_пускает_в_знакомство(gw, monkeypatch):
    """Повторная привязка — отказ с текстом, меню и знакомство не начинаются."""
    calls, message, state = run_code(
        gw, monkeypatch, {"ok": True, "role": "troupe"},
        attach={"ok": False, "error": "code_exhausted"})

    texts = [text for text, _ in message.answers]
    assert "Этот код уже использован." in texts
    assert not any(call[0] == "intro" for call in calls)
    assert state.state is not None
