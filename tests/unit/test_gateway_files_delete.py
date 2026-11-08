"""Чат: удаление файла кнопкой с подтверждением (V-045, FR-K5, FR-L8).

Решение владельца 08.11.2026: удаление файла — действие человека, кнопкой и с
подтверждением. В чате два входа в одно и то же действие: кнопка «Удалить файл» под
ответом о сохранении и команда `/rm 12` (номер из `/files`). Оба зовут один маршрут
`DELETE /files/{id}`, что и кнопка Mini App — второго поведения удаления нет.

Здесь проверяется не текст ответа tool-api, а цепочка «вопрос → подтверждение →
маршрут»: до подтверждения удаления нет, отмена ничего не удаляет.

Обоснование: FR-F5, FR-K5, FR-M8, FR-L8, `docs/development/86-files-and-storage.md`.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest

FILES_ROUTER = gateway_modules.load("routers.files")


class FakeMessage:
    """Минимум от Message: чат, текст и журнал ответов с клавиатурами."""

    def __init__(self, text: str = "", chat_id: int = 6001):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[str] = []
        self.markups: list = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append(text)
        self.markups.append(reply_markup)

    async def edit_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)


class FakeCallback:
    """Минимум от CallbackQuery: данные кнопки, сообщение и журнал ответов."""

    def __init__(self, data: str, chat_id: int = 6001):
        self.data = data
        self.answers: list[tuple] = []
        self.message = FakeMessage(chat_id=chat_id)

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def _caps(codes: list[str]):
    async def caps_of(chat_id: int):
        return codes
    return caps_of


def _api(calls: list, meta: dict | None = None, deleted: dict | None = None):
    """Подмена `core.api`: GET отдаёт метаданные, DELETE — итог удаления."""

    async def api(path, payload, chat_id, method="POST", timeout=25):
        calls.append((method, path))
        if method == "GET":
            return meta if meta is not None else {"ok": False}
        return deleted if deleted is not None else {"ok": True, "id": 12, "name": "смета.pdf"}
    return api


def _labels(markup) -> list[str]:
    if markup is None:
        return []
    return [button.text for row in markup.inline_keyboard for button in row]


def _data(markup) -> list[str]:
    if markup is None:
        return []
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["admin.panel"]))


def test_команда_rm_спрашивает_подтверждение(monkeypatch):
    """/rm 12 не удаляет сразу: сначала карточка с «Удалить» и «Отмена» (FR-L8)."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "api",
                        _api(calls, meta={"ok": True, "id": 12, "name": "смета.pdf"}))

    message = FakeMessage("/rm 12")
    asyncio.run(FILES_ROUTER.cmd_rm(message))

    assert calls == [("GET", "/files/12")], "до подтверждения удаления не было"
    assert "Удалить файл «смета.pdf»?" in message.answers[0]
    assert _labels(message.markups[0]) == ["Удалить", "Отмена"]
    assert _data(message.markups[0]) == ["fdel-ok:12", "fdel-no"]


def test_команда_rm_без_номера_объясняет_формат():
    message = FakeMessage("/rm")
    asyncio.run(FILES_ROUTER.cmd_rm(message))
    assert "номер файла из /files" in message.answers[0]


def test_кнопка_под_загрузкой_ведёт_к_подтверждению(monkeypatch):
    """Кнопка «Удалить файл» под сохранением — второй вход, та же карточка (FR-K5)."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "api",
                        _api(calls, meta={"ok": True, "id": 12, "name": "смета.pdf"}))

    callback = FakeCallback("fdel:12")
    asyncio.run(FILES_ROUTER.file_delete_ask(callback))

    assert calls == [("GET", "/files/12")]
    assert _labels(callback.message.markups[0]) == ["Удалить", "Отмена"]
    assert "Удалить файл «смета.pdf»?" in callback.message.answers[0]


def test_подтверждение_удаляет_через_маршрут_tool_api(monkeypatch):
    """«Удалить» зовёт `DELETE /files/{id}` — тот же путь, что у экрана (один дом)."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    callback = FakeCallback("fdel-ok:12")
    asyncio.run(FILES_ROUTER.file_delete_confirm(callback))

    assert calls == [("DELETE", "/files/12")]
    assert callback.message.answers[-1] == "Файл удалён: смета.pdf"
    assert callback.message.markups[0] is None, "кнопки с карточки снимаются"


def test_отмена_ничего_не_удаляет(monkeypatch):
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    callback = FakeCallback("fdel-no")
    asyncio.run(FILES_ROUTER.file_delete_cancel(callback))

    assert not calls, "отмена не должна звать tool-api"
    assert callback.message.answers[-1] == "Удаление отменено."


def test_чужой_файл_карточку_не_получает(monkeypatch):
    """Недоступный файл — «Файл не найден.» и без кнопок: подтверждать нечего (FR-K8)."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls, meta={"ok": False}))

    message = FakeMessage("/rm 12")
    asyncio.run(FILES_ROUTER.cmd_rm(message))

    assert message.answers[0] == "Файл не найден."
    assert message.markups[0] is None
