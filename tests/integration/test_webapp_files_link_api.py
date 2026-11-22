"""Регистрация ссылки на Google Drive (V-123, docs/development/192 §6.3).

Ссылка — отдельная запись с `storage='drive'`: байтов у нас нет, размер и MIME не
запрашиваются (в Google не ходим). Папка Drive принимается как отдельная запись
(`kind='folder'`) — дизайн 189 §7 говорил «отказ», решение V-123 говорит «принимается»,
и спецификация 192 закрыла расхождение в пользу решения владельца.

Проверяется: семь форм адреса, отказы схемы/хоста/разбора словами, канонический адрес,
имя по умолчанию, `drive_path` из формы, пустые `size_bytes`/`sha256`/`drive_mime` и
дубль: свой — с именем, чужой закрытый — без имени (FR-K8).

Обоснование: V-123, FR-K8, docs/development/189 §D6, AC-13, AC-14.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.conftest import _test_dsn
from tests.integration.webapp_files_support import file_row, headers

ACCEPTED = [
    ("https://drive.google.com/file/d/abcdefghijklmnop/view", False),
    ("https://docs.google.com/document/d/abcdefghijklmnop/edit", False),
    ("https://docs.google.com/spreadsheets/d/abcdefghijklmnop/edit#gid=0", False),
    ("https://drive.google.com/drive/folders/abcdefghijklmnop", True),
    ("https://drive.google.com/drive/u/1/folders/abcdefghijklmnop", True),
    ("https://drive.google.com/open?id=abcdefghijklmnop", False),
    ("https://drive.google.com/file/d/abcdefghijklmnop/view?usp=sharing", False),
]


@pytest.mark.parametrize("url,is_folder", ACCEPTED)
def test_адрес_принимается_и_становится_каноническим(client, url, is_folder) -> None:
    """Семь форм адреса: и файл, и папка, и `open?id=`, и хвост `?usp=sharing`."""
    answer = client.post("/webapp/files/link", json={"url": url}, headers=headers(client))
    assert answer.status_code == 200, answer.text
    row = answer.json()["file"]
    row_id = row["id"]
    assert row["storage"] == "drive"
    assert row["size_bytes"] is None
    if is_folder:
        assert row["kind"] == "folder"
        assert row["drive_url"] == "https://drive.google.com/drive/folders/abcdefghijklmnop"
        assert row["name"] == "Папка в Drive"
    else:
        assert row["kind"] is None
        assert row["drive_url"] == "https://drive.google.com/file/d/abcdefghijklmnop/view"
        assert row["name"] == "Ссылка на Drive"

    stored = file_row(row_id)
    assert stored["storage"] == "drive"
    assert stored["object_key"] is None and stored["bucket"] is None
    assert stored["sha256"] is None and stored["drive_mime"] is None
    assert stored["uploaded_via"] == "human_drive"


def test_отказы_словами_и_без_строк(client) -> None:
    """http://, чужой хост и неразобранный адрес — 400 с человеческим текстом, строк 0."""
    before = _count()
    cases = [
        ("http://drive.google.com/file/d/abcdefghijklmnop/view", "link_not_https",
         "https://"),
        ("https://example.com/file/d/abcdefghijklmnop", "link_host_not_allowed", "Google Drive"),
        ("https://drive.google.com/", "link_not_parsed", "Не понял адрес"),
    ]
    for url, code, words in cases:
        answer = client.post("/webapp/files/link", json={"url": url}, headers=headers(client))
        assert answer.status_code == 400, answer.text
        body = answer.json()
        assert body["code"] == code
        assert words in body["detail"]
    assert _count() == before, "отказ не должен оставлять строк"


def test_имя_и_путь_берутся_из_формы(client) -> None:
    """Форма — единственный источник имени и `drive_path`: заголовок страницы не читаем."""
    answer = client.post(
        "/webapp/files/link",
        json={"url": "https://drive.google.com/drive/folders/abcdefghijklmnop",
              "name": "Пурга — материалы", "drive_path": "Спектакли/Пурга",
              "description": "  афиши и фото  "},
        headers=headers(client))
    assert answer.status_code == 200, answer.text
    row = answer.json()["file"]
    assert row["name"] == "Пурга — материалы"
    assert row["drive_path"] == "Спектакли/Пурга"
    assert row["description"] == "афиши и фото", "описание обрезается по краям"


def test_ссылка_ложится_в_выбранную_папку(client) -> None:
    """`folder_id` необязателен; с ним ссылка видна в папке, без него — в «Без папки»."""
    folder_id = _shared("Спектакли")
    answer = client.post("/webapp/files/link",
                         json={"url": "https://drive.google.com/file/d/abcdefghijklmnop/view",
                               "folder_id": folder_id},
                         headers=headers(client))
    assert answer.status_code == 200, answer.text
    row = answer.json()["file"]
    assert row["folder_id"] == folder_id and row["folder"] == "Спектакли"
    stored = file_row(row["id"])
    assert stored["scope"] == "shared"


def test_дубль_своей_ссылки_с_именем(client) -> None:
    """Своя ссылка: 409 `link_exists` с именем — человеку нужно знать, что уже есть."""
    url = "https://drive.google.com/file/d/dupeOWN0001/view"
    first = client.post("/webapp/files/link", json={"url": url, "name": "Смета Пурги"},
                        headers=headers(client))
    assert first.status_code == 200
    before = _count()

    second = client.post("/webapp/files/link", json={"url": url}, headers=headers(client))
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "link_exists"
    assert "Смета Пурги" in body["detail"]
    assert _count() == before


def test_дубль_чужой_закрытой_ссылки_без_имени(client) -> None:
    """Чужая закрытая: 409 `link_exists_other` **без имени** — иначе FR-K8 нарушен перебором."""
    url = "https://drive.google.com/file/d/dupeOTHER01/view"
    owner = headers(client, 770101)
    assert client.post("/webapp/files/link", json={"url": url, "name": "Чужая смета"},
                       headers=owner).status_code == 200

    stranger = headers(client, 770102)
    answer = client.post("/webapp/files/link", json={"url": url}, headers=stranger)
    assert answer.status_code == 409
    body = answer.json()
    assert body["code"] == "link_exists_other"
    assert "Чужая смета" not in body["detail"], "имя чужой закрытой ссылки утекло"


def _count() -> int:
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute("select count(*) from file_object").fetchone()[0])


def _shared(name: str) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select id from folder where scope = 'shared' and path = %s",
                           (name,)).fetchone()
        return int(row[0])
