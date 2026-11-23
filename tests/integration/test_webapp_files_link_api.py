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

import json

import psycopg
import pytest

from tests.conftest import _test_dsn
from tests.integration.webapp_files_support import file_row, headers, participant

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


def test_удалённая_своя_ссылка_воскрешается(client) -> None:
    """Удалил ссылку → добавил тот же адрес: 200, прежний `id`, одна живая строка.

    Уникальность `drive_file_id` в схеме переживает мягкое удаление, поэтому вставка второй
    строки падала `UniqueViolation` и ручка отвечала 500. Экрана восстановления удалённых
    файлов нет: строка поднимается (`deleted_at = null`), поля берутся из запроса.
    """
    url = "https://drive.google.com/file/d/reviveOWN01/view"
    auth = headers(client)
    first = client.post("/webapp/files/link", json={"url": url, "name": "Смета Пурги"},
                        headers=auth)
    assert first.status_code == 200, first.text
    row_id = first.json()["file"]["id"]
    assert client.delete(f"/webapp/files/{row_id}", headers=auth).status_code == 200
    assert file_row(row_id)["deleted_at"] is not None, "перед воскрешением строка должна быть удалена"

    again = client.post("/webapp/files/link",
                        json={"url": url, "name": "Смета Пурги — новая",
                              "description": "  версия после правок  "},
                        headers=auth)
    assert again.status_code == 200, again.text
    revived = again.json()["file"]
    assert revived["id"] == row_id, "воскрешение сохраняет id, а не заводит вторую строку"
    assert revived["name"] == "Смета Пурги — новая"
    assert "revived" not in revived, "служебный флаг аудита не уходит клиенту"

    stored = file_row(row_id)
    assert stored["deleted_at"] is None, "строка снова живая"
    assert stored["description"] == "версия после правок"
    assert stored["storage"] == "drive" and stored["drive_file_id"] == "reviveOWN01"

    listing = client.get("/webapp/files/list", headers=auth).json()["data"]
    assert [int(item["id"]) for item in listing].count(row_id) == 1
    assert _rows_for("reviveOWN01") == 1, "строка ровно одна — иначе уникальность съехала"


def test_воскрешение_пишет_событие_с_revived(client) -> None:
    """Аудит `file.drive_link` различает воскрешение: `revived: true` в записи события."""
    url = "https://drive.google.com/file/d/reviveAUD01/view"
    auth = headers(client)
    first = client.post("/webapp/files/link", json={"url": url, "name": "Афиша"}, headers=auth)
    row_id = first.json()["file"]["id"]
    client.delete(f"/webapp/files/{row_id}", headers=auth)
    assert client.post("/webapp/files/link", json={"url": url, "name": "Афиша"},
                       headers=auth).status_code == 200

    events = _drive_audit(row_id)
    assert len(events) == 2, "две регистрации — два события, удаление своим флагом не пишется"
    assert events[0]["after"]["revived"] is True, "воскрешение помечено в аудите"
    assert events[1]["after"]["revived"] is False, "первая регистрация — не воскрешение"


def test_удалённая_чужая_закрытая_ссылка_без_имени(client) -> None:
    """Чужую удалённую закрытую не воскрешаем: 409 `link_exists_other`, имени нет."""
    url = "https://drive.google.com/file/d/reviveOTH01/view"
    owner = headers(client, 770201)
    first = client.post("/webapp/files/link", json={"url": url, "name": "Чужая афиша"},
                        headers=owner)
    assert first.status_code == 200, first.text
    row_id = first.json()["file"]["id"]
    assert client.delete(f"/webapp/files/{row_id}", headers=owner).status_code == 200

    answer = client.post("/webapp/files/link", json={"url": url}, headers=headers(client, 770202))
    assert answer.status_code == 409, answer.text
    body = answer.json()
    assert body["code"] == "link_exists_other"
    assert "Чужая афиша" not in body["detail"], "имя чужой закрытой ссылки утекло"
    assert file_row(row_id)["deleted_at"] is not None, "чужую строку не воскрешаем"
    assert _rows_for("reviveOTH01") == 1


def test_гонка_мимо_проверки_дубля_даёт_409(client, monkeypatch) -> None:
    """Проверка дубля разошлась с вставкой: отказ словами, а не 500.

    Первое чтение подменено на «строки нет» — так выглядит окно между проверкой и вставкой.
    Вставка упирается в уникальный индекс (`on conflict (drive_file_id) do nothing`),
    строка перечитывается и человек получает 409 с именем своей живой ссылки.
    """
    from app import files_store

    url = "https://drive.google.com/file/d/raceWIND01/view"
    auth = headers(client)
    first = client.post("/webapp/files/link", json={"url": url, "name": "Гонка формы"},
                        headers=auth)
    assert first.status_code == 200, first.text
    row_id = first.json()["file"]["id"]

    real = files_store._drive_known
    missed = {"n": 0}

    def first_read_misses(conn, file_id):
        missed["n"] += 1
        return None if missed["n"] == 1 else real(conn, file_id)

    monkeypatch.setattr(files_store, "_drive_known", first_read_misses)
    second = client.post("/webapp/files/link", json={"url": url}, headers=auth)
    assert missed["n"] >= 2, "вставка должна была перечитать строку"
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == "link_exists" and "Гонка формы" in body["detail"]
    assert file_row(row_id)["deleted_at"] is None and _rows_for("raceWIND01") == 1


def test_гонка_двух_регистраций_без_500(client) -> None:
    """Два соединения на один адрес: второй `insert` ждёт первый и получает 409, не 500.

    Соперник держит незакоммиченную строку, поэтому проверка дубля в ручке её не видит;
    ручка встаёт на уникальном индексе и разбирает занятую строку после подтверждения.
    """
    import threading
    import time

    url = "https://drive.google.com/file/d/raceREAL01/view"
    auth = headers(client)
    owner = participant()
    rival = psycopg.connect(_test_dsn())
    answer: dict = {}
    try:
        rival.execute(
            "insert into file_object (participant_id, drive_file_id, name, scope,"
            " uploaded_via, storage, drive_url)"
            " values (%s, %s, 'Гонка соединений', 'personal', 'human_drive', 'drive', %s)",
            (owner, "raceREAL01", url))
        thread = threading.Thread(
            target=lambda: answer.update(r=client.post("/webapp/files/link", json={"url": url},
                                                       headers=auth)),
            daemon=True)
        thread.start()
        time.sleep(0.5)                 # ручка успела дойти до вставки и встала на индексе
        rival.commit()
        thread.join(30)
        assert not thread.is_alive(), "запрос не дождался освобождения индекса"
        result = answer["r"]
        assert result.status_code == 409, result.text
        assert result.json()["code"] == "link_exists"
        assert _rows_for("raceREAL01") == 1, "второй регистрации строки не появилось"
    finally:
        rival.close()


def _rows_for(drive_file_id: str) -> int:
    """Сколько строк в БД несут этот `drive_file_id` — включая мягко удалённые."""
    with psycopg.connect(_test_dsn()) as conn:
        return int(conn.execute("select count(*) from file_object where drive_file_id = %s",
                                (drive_file_id,)).fetchone()[0])


def _drive_audit(file_id: int) -> list[dict]:
    """События `file.drive_link` по файлу — последние сверху."""
    with psycopg.connect(_test_dsn()) as conn:
        rows = conn.execute(
            "select after from audit_event where action = 'file.drive_link'"
            " and entity_type = 'file_object' and entity_id = %s order by id desc",
            (str(file_id),)).fetchall()
    return [{"after": row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")}
            for row in rows]


def _shared(name: str) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select id from folder where scope = 'shared' and path = %s",
                           (name,)).fetchone()
        return int(row[0])
