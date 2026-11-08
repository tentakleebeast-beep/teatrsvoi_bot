"""Один путь загрузки файла: бот и Mini App пишут запись одной функцией (Ф1, Ш-08).

До этого шага в `files_store` было два пути записи: `save()` (бот: поток → put_object →
insert) и `save_committed()` (браузер: presigned PUT → commit). Два пути — два поведения:
дедупликация, квота, личная папка и разбор аудио могли разъехаться, и любой новый признак
приходилось добавлять дважды (`docs/reports/124` §3, Ф1).

Тест не читает исходник «на глаз»: он подменяет хранилище и `save_committed`, зовёт
`upload_stream` (то, что делает `/files/upload`) и проверяет, что запись ушла именно
в общий коммит — с тем же ключом, что лежит в хранилище. Заодно проверяется, что второго
пути (`save`) в модуле больше нет: удалённая функция не может вернуться незамеченной.

Обоснование: FR-F5, `docs/development/86-files-and-storage.md` §2, `docs/reports/124` §3 (Ф1).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import files_store  # noqa: E402


class FakeStorage:
    """Хранилище, которое только запоминает, что в него положили."""

    def __init__(self) -> None:
        self.put: list[dict] = []

    def put_object(self, **kwargs) -> None:
        self.put.append(kwargs)


@pytest.fixture()
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(files_store, "storage", lambda: fake)
    monkeypatch.setattr(files_store, "bucket_private", lambda: "svoi-private")
    return fake


def test_второго_пути_записи_нет() -> None:
    assert not hasattr(files_store, "save"), "старый путь записи вернулся"
    assert hasattr(files_store, "save_committed")
    assert hasattr(files_store, "upload_stream")


def test_поток_бота_идёт_общим_коммитом(fake_storage, monkeypatch) -> None:
    """`upload_stream` кладёт байты в хранилище и зовёт `save_committed` с тем же ключом."""
    calls: list[dict] = []
    marker = files_store.SavedFile(7, "пьеса.pdf", "document", 3, "Личное/Мои документы",
                                   False, files_store.QUOTA_BYTES)

    def fake_commit(conn, participant_id, name, mime, size, sha256, key, head, via="webapp"):
        calls.append({"participant_id": participant_id, "name": name, "size": size,
                      "key": key, "sha256": sha256, "via": via})
        return marker

    monkeypatch.setattr(files_store, "save_committed", fake_commit)

    saved = files_store.upload_stream(object(), 42, "пьеса.pdf", "application/pdf",
                                      object(), 3, "a" * 64, b"%PD")

    assert saved is marker
    assert len(fake_storage.put) == 1, "байты должны попасть в хранилище один раз"
    key = fake_storage.put[0]["Key"]
    assert key.startswith("p42/"), "ключ обязан принадлежать человеку"
    assert calls == [{"participant_id": 42, "name": "пьеса.pdf", "size": 3, "key": key,
                      "sha256": "a" * 64, "via": "human_telegram"}]


def test_маршрут_загрузки_зовёт_общий_путь() -> None:
    """`/files/upload` — обёртка: своей записи в БД у него нет."""
    source = (ROOT / "services" / "tool-api" / "app" / "api" / "files.py").read_text(
        encoding="utf-8")
    start = source.index("async def files_upload(")
    block = source[start:source.index("@router.get(\"/files\")")]
    assert "files_store.upload_stream(" in block
    assert "files_store.save(" not in block
