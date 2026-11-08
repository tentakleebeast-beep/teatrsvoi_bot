"""Файлы в чате закрыты правом экрана: без `admin.panel` команда отвечает отказом.

П-7 (`docs/reports/124` §1): файлы пока только у администратора. Экран и данные это
держали (`api/files.py` — `_webapp_require(who, "admin.panel")`), а команды чата
`/files`, `/folders`, `/mkdir`, `/tag`, `/zip`, `/access` звали tool-api без проверки —
то есть были вторым, открытым входом в тот же дом. Здесь проверяется отказ оболочки
и то, что до tool-api запрос не доходит; положительный случай — админ проходит.

Исключение одно — `/revoke`: это не доступ к файлу, а закрытие чужого доступа к своему.
Право «владелец или админ» уже проверяет `file_access.revoke`, поэтому оболочка запрос
пропускает, а отказ приходит словами tool-api (FR-K9: владельцу обещано «Отозвать: /revoke N»).

Обоснование: FR-F5, FR-K9, П-7, `docs/development/86-files-and-storage.md`,
`docs/development/92` §9.1.
"""

from __future__ import annotations

import asyncio
import types

import gateway_modules
import pytest

FILES_ROUTER = gateway_modules.load("routers.files")

#: Команды файлов и текст, с которым их зовут (Ш-02, docs/plans/126).
#: `/revoke` здесь нет намеренно: его право решает tool-api (владелец или админ).
COMMANDS = (
    ("cmd_files", "/files"),
    ("cmd_folders", "/folders"),
    ("cmd_mkdir", "/mkdir Реквизит"),
    ("cmd_tag", "/tag 42 реквизит"),
    ("cmd_zip", "/zip 12"),
    ("cmd_access", "/access 42 сверка сметы"),
    # Ш-08: входы, которые раньше оставались без права или без входа вовсе
    ("cmd_tags", "/tags"),
    ("cmd_tag_ok", "/tag-ok 7"),
    ("cmd_search", "/search антракт"),
    ("cmd_rename", "/rename 12 Новое имя"),
    ("cmd_rmfolder", "/rmfolder 12"),
    ("cmd_order", "/order 12 3 1 2"),
)


class FakeMessage:
    """Минимум от Message: чат, текст команды и куда складывать ответы."""

    def __init__(self, text: str, chat_id: int = 4242):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append(text)

    async def answer_document(self, document, caption=None):
        self.answers.append(caption or "")


def _caps(codes: list[str]):
    async def caps_of(chat_id: int) -> list[str]:
        return codes
    return caps_of


def _api(calls: list, answer: dict | None = None):
    """Подмена `core.api`: запоминаем вызовы, чтобы доказать «до tool-api не дошли»."""

    async def api(path, payload, chat_id, method="POST", timeout=25):
        calls.append((method, path))
        return answer or {"ok": True, "data": []}
    return api


@pytest.mark.parametrize("handler,text", COMMANDS)
def test_без_admin_panel_команда_отказывает(handler, text, monkeypatch):
    """Организатор и участник файлов не видят — отказ до вызова tool-api."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["profile.read"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    message = FakeMessage(text)
    asyncio.run(getattr(FILES_ROUTER, handler)(message))

    assert message.answers == [FILES_ROUTER.texts.FILES_ADMIN_ONLY]
    assert not calls, f"{handler}: без права запрос в tool-api не идёт"


def test_админ_проходит_к_файлам(monkeypatch):
    """С `admin.panel` команда работает как раньше: запрос уходит, ответ человеку есть."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["admin.panel"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    message = FakeMessage("/files")
    asyncio.run(FILES_ROUTER.cmd_files(message))

    assert calls == [("GET", "/files?limit=10")]
    assert message.answers and message.answers[-1].startswith("Файлов пока нет")


def test_революция_владельца_проходит_без_admin_panel(monkeypatch):
    """/revoke — не доступ к файлу: владелец без `admin.panel` доходит до tool-api (FR-K9).

    Право «владелец или админ» проверяет `file_access.revoke`; оболочка не дублирует его,
    иначе владельцу обещано в уведомлении то, чего он сделать не может.
    """
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["profile.read"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    message = FakeMessage("/revoke 42")
    asyncio.run(FILES_ROUTER.cmd_revoke(message))

    assert calls == [("POST", "/files/42/access/revoke")], "запрос доходит до tool-api"
    assert message.answers == ["Доступ закрыт."]


def test_отказ_по_отзыву_приходит_словами_tool_api(monkeypatch):
    """Не владельцу и не админу отказывает сервер — человек читает его текст, а не «только админ»."""
    calls: list = []
    detail = "Отозвать доступ может владелец файла."
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["profile.read"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls, {"ok": False, "detail": detail}))

    message = FakeMessage("/revoke 42")
    asyncio.run(FILES_ROUTER.cmd_revoke(message))

    assert calls == [("POST", "/files/42/access/revoke")]
    assert message.answers == [detail]


def test_приём_файла_из_чата_закрыт_правом(monkeypatch):
    """Файл, присланный в чат, — тот же дом: без `admin.panel` он не принимается (П-7)."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["profile.read"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    message = FakeMessage("")
    asyncio.run(FILES_ROUTER.on_file(message))

    assert message.answers == [FILES_ROUTER.texts.FILES_ADMIN_ONLY]
    assert not calls


@pytest.mark.parametrize("handler,text,path", (
    ("cmd_rename", "/rename 12 Новое имя", "/folders/12/rename"),
    ("cmd_rmfolder", "/rmfolder 12", "/folders/12/delete"),
    ("cmd_order", "/order 12 3 1 2", "/folders/12/order"),
))
def test_команды_папок_идут_в_те_же_маршруты_что_экран(handler, text, path, monkeypatch):
    """У rename, delete и order появился вход в чате — тот же маршрут, что у экрана."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["admin.panel"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    message = FakeMessage(text)
    asyncio.run(getattr(FILES_ROUTER, handler)(message))

    assert calls == [("POST", path)]
    assert message.answers, "человек должен получить ответ, а не тишину"


class FakeCallback:
    """Минимум от CallbackQuery: данные кнопки, сообщение и журнал ответов."""

    def __init__(self, data: str, chat_id: int = 4242):
        self.data = data
        self.answers: list[tuple] = []
        self.message = types.SimpleNamespace(chat=types.SimpleNamespace(id=chat_id),
                                             answer=self._silent)

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def _silent(self, *args, **kwargs):
        return None


def test_кнопка_файла_закрыта_правом(monkeypatch):
    """Кнопка `fl:` — тоже вход в дом файлов: без права она не отдаёт файл."""
    calls: list = []
    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", _caps(["profile.read"]))
    monkeypatch.setattr(FILES_ROUTER.core, "api", _api(calls))

    callback = FakeCallback("fl:42")
    asyncio.run(FILES_ROUTER.file_send(callback))

    assert callback.answers and callback.answers[0][0] == FILES_ROUTER.texts.FILES_ADMIN_ONLY
    assert not calls, "без права файл не запрашивается у tool-api"
