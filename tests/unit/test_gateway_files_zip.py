"""Гейтвей: отказ архива папки (413) доходит до человека словами, а не «код 413».

Дефект: `api_bytes` глотал статус и возвращал None, поэтому `/zip` на папке больше
потолка отвечал «Не получилось собрать архив: папка пуста или недоступна» — неправда,
текст отказа tool-api «Архив больше N МБ — скачивайте по файлам» терялся.

Обоснование: FR-M6 (выгрузка папки целиком, docs/development/86-files-and-storage.md),
конверт ошибки — docs/architecture/85-error-catalog.md (`folder_zip_too_large`, 413).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import types

import gateway_modules
import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))

FILES_ROUTER = gateway_modules.load("routers.files")

TOO_LARGE = {"code": "folder_zip_too_large",
             "detail": "Архив больше 100 МБ — скачивайте по файлам.",
             "title": "Слишком большой архив"}


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    """Файлы в чате закрыты `admin.panel` (Ш-02, П-7), поэтому зовём как администратора.

    Здесь проверяется не право, а текст отказа tool-api: без этого фикстура права
    возвращала бы «доступно только администратору» вместо ответа сервиса.
    """
    async def caps_of(chat_id: int) -> list[str]:
        return ["admin.panel"]

    monkeypatch.setattr(FILES_ROUTER.core, "caps_of", caps_of)


class FakeMessage:
    """Минимум от Message: чат, текст команды и куда складывать ответы."""

    def __init__(self, text: str, chat_id: int = 555):
        self.text = text
        self.chat = types.SimpleNamespace(id=chat_id)
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)

    async def answer_document(self, document, caption=None):
        self.answers.append(caption or "")


class _Response:
    def __init__(self, status: int, payload=None, content: bytes = b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("не JSON")
        return self._payload


def _client(response: _Response):
    """Подменить httpx.AsyncClient одной заготовкой: сети в тесте нет."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            return response

    return types.SimpleNamespace(AsyncClient=FakeClient, HTTPError=httpx.HTTPError)


def _zip(message: FakeMessage) -> str:
    asyncio.run(FILES_ROUTER.cmd_zip(message))
    assert message.answers, "бот ничего не ответил"
    return message.answers[-1]


def test_413_от_апи_становится_исключением_с_конвертом(monkeypatch):
    """Статус больше не теряется: api_bytes отдаёт 413 и тело отказа вызывающему."""
    monkeypatch.setattr(FILES_ROUTER, "httpx", _client(_Response(413, TOO_LARGE)))

    with pytest.raises(FILES_ROUTER.ApiRefused) as caught:
        asyncio.run(FILES_ROUTER.api_bytes("/folders/12/zip", 555))

    assert caught.value.status == 413
    assert caught.value.body["code"] == "folder_zip_too_large"


def test_не_json_отказ_не_ломает_разбор(monkeypatch):
    """Прокси или шлюз может ответить без конверта: статус сохраняем, тело пустое."""
    monkeypatch.setattr(FILES_ROUTER, "httpx", _client(_Response(413)))

    with pytest.raises(FILES_ROUTER.ApiRefused) as caught:
        asyncio.run(FILES_ROUTER.api_bytes("/folders/12/zip", 555))

    assert caught.value.status == 413
    assert caught.value.body == {}


def test_zip_на_большой_папке_отвечает_текстом_отказа(monkeypatch):
    """/zip 12 на папке больше потолка: текст «скачивайте по файлам», а не «папка пуста»."""
    monkeypatch.setattr(FILES_ROUTER, "WEBAPP_BASE_URL", "https://svoi.example")

    async def refused(path, chat_id):
        raise FILES_ROUTER.ApiRefused(413, TOO_LARGE)

    monkeypatch.setattr(FILES_ROUTER, "api_bytes", refused)

    answer = _zip(FakeMessage("/zip 12"))

    assert "Архив больше 100 МБ" in answer
    assert "скачивайте по файлам" in answer
    assert "https://svoi.example/webapp/folder?id=12" in answer
    assert "папка пуста" not in answer


def test_zip_на_недоступной_папке_объясняет_отказ_сервера(monkeypatch):
    """404 папки — слова сервера, а не «пуста или недоступна»."""
    not_found = {"code": "folder_not_found", "detail": "Папка не найдена."}

    async def refused(path, chat_id):
        raise FILES_ROUTER.ApiRefused(404, not_found)

    monkeypatch.setattr(FILES_ROUTER, "api_bytes", refused)

    answer = _zip(FakeMessage("/zip 77"))

    assert answer == "Папка не найдена."
    assert "папка пуста" not in answer


def test_zip_без_связи_говорит_о_сервисе(monkeypatch):
    """Сеть упала — это «сервис не ответил», а не обвинение папке."""
    monkeypatch.setattr(FILES_ROUTER, "WEBAPP_BASE_URL", "")

    async def offline(path, chat_id):
        return None

    monkeypatch.setattr(FILES_ROUTER, "api_bytes", offline)

    answer = _zip(FakeMessage("/архив 12"))

    assert "сервис файлов не ответил" in answer
    assert "пуста" not in answer
