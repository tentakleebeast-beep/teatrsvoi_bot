"""Заметки в чате: текст → карточка → подтверждение → запись (FR-F7).

Обоснование: FR-F7 — «в чате — подтверждение разобранной заметки»; запись идёт тем же
путём, что у экрана (`POST /tools/notes`), поэтому проверяем клей gateway, а не Telegram:

* фраза разбирается детерминированно: «заметка: …» отделяется, теги `#…` вынимаются,
  заголовок — первая строка;
* до нажатия кнопки ничего не пишется, карточка показывает заголовок, текст, теги и
  кому заметка будет видна;
* обе кнопки («Сохранить личную» и «В общую») зовут один эндпоинт с разной областью;
* без права `notes.write` запись не идёт, а отказ объясняется словами.
"""

from __future__ import annotations

import asyncio
import pathlib
import types

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


class FakeMessage:
    def __init__(self, text: str = "", chat_id: int = 1):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[tuple[str, object]] = []
        self.edits: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append(text)


class FakeCallback:
    def __init__(self, data: str, chat_id: int = 1):
        self.data = data
        self.message = FakeMessage(chat_id=chat_id)
        self.alerts: list[str] = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)


class FakeState:
    """Мини-FSM: хранит данные черновика, как MemoryStorage."""

    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})
        self.cleared = False

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def clear(self) -> None:
        self.data.clear()
        self.cleared = True


def test_фраза_разбирается_в_заметку(gw) -> None:
    draft = gw.note_draft("заметка: взять реквизит из склада #реквизит #зал")

    assert draft["body"] == "взять реквизит из склада #реквизит #зал"
    assert draft["title"] == "взять реквизит из склада #реквизит #зал"
    assert draft["tags"] == ["реквизит", "зал"], draft["tags"]


def test_команда_и_префикс_снимаются_но_текст_цел(gw) -> None:
    for text in ("/заметка позвонить в зал", "заметка: позвонить в зал",
                 "запиши заметку позвонить в зал"):
        assert gw.note_draft(text)["body"] == "позвонить в зал", text


def test_карточка_показывает_что_запишется(gw, monkeypatch) -> None:
    state = FakeState()
    message = FakeMessage("заметка: купить лампы 2 штуки #реквизит")

    asyncio.run(gw.note_start(message, state))

    text, markup = message.answers[-1]
    assert "Записать заметку?" in text
    assert "купить лампы 2 штуки" in text and "реквизит" in text
    assert "личная" in text
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert labels == ["Сохранить личную", "В общую", "Отмена"], labels
    # черновик ждёт подтверждения; записи ещё не было
    assert state.data["note"]["body"].startswith("купить лампы")


def test_пустая_заметка_даёт_подсказку_а_не_карточку(gw) -> None:
    state = FakeState()
    message = FakeMessage("/заметка")

    asyncio.run(gw.note_start(message, state))

    text, _ = message.answers[-1]
    assert "Напишите заметку" in text
    assert "note" not in state.data


@pytest.mark.parametrize(("verb", "scope"), (("personal", "personal"), ("shared", "shared")))
def test_подтверждение_пишет_тем_же_путём(gw, monkeypatch, verb, scope) -> None:
    calls: dict = {}

    async def fake_caps(chat_id):
        return ["notes.write"]

    async def fake_api(path, payload, chat_id, method="POST"):
        calls.update(path=path, payload=payload)
        return {"ok": True, "note": {"id": 3, "title": payload["title"], "scope": payload["scope"]}}

    monkeypatch.setattr(gw, "caps_of", fake_caps)
    monkeypatch.setattr(gw, "api", fake_api)
    state = FakeState({"note": {"title": "Купить лампы", "body": "Купить лампы",
                                "tags": ["реквизит"]}})
    cb = FakeCallback(f"note:{verb}")

    asyncio.run(gw.note_confirm(cb, state))

    assert calls["path"] == "/tools/notes"
    assert calls["payload"]["scope"] == scope
    assert calls["payload"]["body"] == "Купить лампы"
    assert calls["payload"]["tags"] == ["реквизит"]
    assert cb.message.edits and "Заметка сохранена" in cb.message.edits[-1]
    assert state.cleared is True


def test_без_права_записи_в_сервис_не_идём(gw, monkeypatch) -> None:
    async def fake_caps(chat_id):
        return ["schedule.availability.self"]

    async def fake_api(*args, **kwargs):
        raise AssertionError("без права notes.write запись идти не должна")

    monkeypatch.setattr(gw, "caps_of", fake_caps)
    monkeypatch.setattr(gw, "api", fake_api)
    state = FakeState({"note": {"title": "x", "body": "x", "tags": []}})
    cb = FakeCallback("note:personal")

    asyncio.run(gw.note_confirm(cb, state))

    assert cb.message.edits[-1] == gw.NOTE_NO_WRITE


def test_отмена_ничего_не_пишет(gw, monkeypatch) -> None:
    async def fake_api(*args, **kwargs):
        raise AssertionError("отмена не должна звать сервис")

    monkeypatch.setattr(gw, "api", fake_api)
    state = FakeState({"note": {"title": "x", "body": "x", "tags": []}})
    cb = FakeCallback("note:no")

    asyncio.run(gw.note_confirm(cb, state))

    assert cb.message.edits[-1] == gw.CANCELLED
    assert state.cleared is True


def test_устаревший_черновик_объясняется_словами(gw, monkeypatch) -> None:
    async def fake_api(*args, **kwargs):
        raise AssertionError("без черновика записи нет")

    monkeypatch.setattr(gw, "api", fake_api)
    cb = FakeCallback("note:shared")

    asyncio.run(gw.note_confirm(cb, FakeState()))

    assert cb.alerts == [gw.NOTE_STALE]


def test_фраза_заметки_перехватывается_до_бюджета() -> None:
    """«заметка: купить лампы 2 штуки» — не трата: ветка стоит до черновика бюджета."""
    menu = (ROOT / "services" / "tg-gateway" / "app" / "routers" / "menu.py").read_text("utf-8")
    note_at = menu.index("notes_router.note_start")
    draft_at = menu.index("draft = parsers.text_to_draft")
    assert note_at < draft_at, "заметка разбирается позже черновика бюджета"
