"""Квота личного места: проверка и запись — одно действие (аудит ponytail).

Раньше `save_committed` считал `used_bytes`, сравнивал с `QUOTA_BYTES` и только потом
вставлял строку. Два подтверждения загрузки у одного человека проходили проверку оба
и место превышалось на размер второго файла. Теперь сумма проверяется в том же
`insert ... select`, что пишет строку, а строка участника берётся `for update`:
второй коммит ждёт первый и считает уже с его файлом.

Здесь это проверяется на настоящем Postgres: последовательные коммиты и два
параллельных потока с разными соединениями.

Обоснование: FR-K14 (квота личного места — отказ при превышении с точной цифрой) и
FR-M6 (выгрузка папки целиком, потолок архива), см. docs/development/86-files-and-storage.md.
"""

from __future__ import annotations

import threading
import uuid

import psycopg
import pytest
from app import files_store
from psycopg.rows import dict_row

from tests.conftest import ADMIN_CHAT, TOKEN, _test_dsn

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _participant_id(client) -> int:
    """Участник из фикстуры `client`: он же владелец личного места."""
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute("select id from participant where tg_chat_id = %s",
                           (ADMIN_CHAT,)).fetchone()
    assert row is not None
    return int(row[0])


def _fill_quota(participant_id: int, used: int) -> None:
    """Занять место напрямую в БД: остаётся ровно `QUOTA_BYTES - used` свободных байт."""
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        conn.execute(
            "insert into file_object (participant_id, name, size_bytes, scope, uploaded_via,"
            " object_key, bucket, kind)"
            " values (%s,'занято',%s,'personal','webapp',%s,'svoi-private','image')",
            (participant_id, used, f"p{participant_id}/filler-{uuid.uuid4().hex}.png"))


def _used(participant_id: int) -> int:
    with psycopg.connect(_test_dsn()) as conn:
        row = conn.execute(
            "select coalesce(sum(size_bytes), 0) from file_object"
            " where participant_id = %s and deleted_at is null", (participant_id,)).fetchone()
    return int(row[0])


def test_второй_коммит_при_почти_полной_квоте_отклонён(client, monkeypatch) -> None:
    participant_id = _participant_id(client)
    _fill_quota(participant_id, files_store.QUOTA_BYTES - 1000)
    deleted: list[str] = []
    monkeypatch.setattr(files_store, "delete_object", deleted.append)

    with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
        first = files_store.save_committed(
            conn, participant_id, "раз.png", "image/png", 500, None,
            f"p{participant_id}/one.png", PNG)
        conn.commit()
        assert first.duplicate is False

        with pytest.raises(files_store.FileRejected) as caught:
            files_store.save_committed(
                conn, participant_id, "два.png", "image/png", 700, None,
                f"p{participant_id}/two.png", PNG)
        conn.commit()

    assert caught.value.code == "file_quota_exceeded"
    assert caught.value.status == 413
    assert "Не помещается в личное место" in caught.value.message
    assert deleted == [f"p{participant_id}/two.png"], "отказ снимает загруженный объект"
    assert _used(participant_id) == files_store.QUOTA_BYTES - 500


def test_параллельные_коммиты_не_превышают_квоту(client, monkeypatch) -> None:
    """Второй коммит ждёт первый и считает место уже с его файлом.

    Проверка детерминированная: первый коммит держит транзакцию открытой (строка уже
    вставлена, но не зафиксирована), второй в это время подтверждает свой файл. Без
    блокировки строки участника второй увидел бы старую сумму, вставил бы свой файл и
    место превысилось бы; с блокировкой он ждёт и после фиксации первого получает отказ.
    """
    participant_id = _participant_id(client)
    _fill_quota(participant_id, files_store.QUOTA_BYTES - 1_000_000)
    monkeypatch.setattr(files_store, "delete_object", lambda key: None)

    inserted = threading.Event()
    release = threading.Event()
    outcomes: list[tuple[str, object]] = []

    def first_commit() -> None:
        with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
            saved = files_store.save_committed(
                conn, participant_id, "первый.png", "image/png", 700_000, None,
                f"p{participant_id}/parallel-1.png", PNG)
            outcomes.append(("ok", saved.id))
            inserted.set()
            assert release.wait(timeout=15), "второй коммит не дошёл до блокировки"
            conn.commit()

    def second_commit() -> None:
        with psycopg.connect(_test_dsn(), row_factory=dict_row) as conn:
            try:
                saved = files_store.save_committed(
                    conn, participant_id, "второй.png", "image/png", 700_000, None,
                    f"p{participant_id}/parallel-2.png", PNG)
                conn.commit()
                outcomes.append(("ok", saved.id))
            except files_store.FileRejected as exc:
                conn.commit()
                outcomes.append(("rejected", exc.status))

    first = threading.Thread(target=first_commit)
    first.start()
    assert inserted.wait(timeout=15), "первый коммит не дошёл до вставки"
    second = threading.Thread(target=second_commit)
    second.start()
    second.join(timeout=1.0)
    assert second.is_alive(), "второй коммит не ждал первый — блокировки нет"
    release.set()
    first.join(timeout=15)
    second.join(timeout=15)
    assert not first.is_alive() and not second.is_alive(), "коммит завис на блокировке"

    assert [kind for kind, _ in outcomes] == ["ok", "rejected"], outcomes
    assert outcomes[1][1] == 413
    assert _used(participant_id) <= files_store.QUOTA_BYTES


def test_архив_большой_папки_отбивается_413_до_сборки(client) -> None:
    """Потолок ZIP виден клиенту: 413 и текст «скачивайте по файлам», без сборки архива.

    Хранилище здесь не нужно: отказ случается до первого обращения к нему, поэтому
    тест работает и без Garage.
    """
    chat_id = 559001
    with psycopg.connect(_test_dsn(), autocommit=True) as conn:
        participant_id = int(conn.execute(
            "insert into participant (tg_chat_id, display_name, person_key, role, status)"
            " values (%s,'Архив Тест','архив-тест','troupe','active')"
            " on conflict (tg_chat_id) do update set status = 'active' returning id",
            (chat_id,)).fetchone()[0])
        root_id = int(conn.execute(
            "insert into folder (scope, participant_id, name, path, depth)"
            " values ('personal', %s, 'Личное', 'Личное', 1) returning id",
            (participant_id,)).fetchone()[0])
        folder_id = int(conn.execute(
            "insert into folder (scope, participant_id, parent_id, name, path, depth)"
            " values ('personal', %s, %s, 'Мои документы', 'Личное/Мои документы', 2)"
            " returning id", (participant_id, root_id)).fetchone()[0])
        file_id = int(conn.execute(
            "insert into file_object (participant_id, name, size_bytes, scope, uploaded_via,"
            " object_key, bucket, kind)"
            " values (%s,'пьеса.pdf',%s,'personal','webapp',%s,'svoi-private','text')"
            " returning id",
            (participant_id, 200 * 1024 * 1024, f"p{participant_id}/big.pdf")).fetchone()[0])
        conn.execute("insert into file_folder (file_id, folder_id) values (%s,%s)",
                     (file_id, folder_id))

    try:
        response = client.get(f"/folders/{folder_id}/zip",
                              headers={"Authorization": f"Bearer {TOKEN}",
                                       "X-Actor-Chat-Id": str(chat_id)})

        assert response.status_code == 413
        body = response.json()
        assert body["code"] == "folder_zip_too_large"
        assert "скачивайте по файлам" in body["detail"]
        assert "100 МБ" in body["detail"]
    finally:
        # База тестов общая на процесс: после теста она должна остаться пустой,
        # иначе инвариант F9 в test_files_schema увидит чужую папку.
        with psycopg.connect(_test_dsn(), autocommit=True) as conn:
            conn.execute("delete from file_object where participant_id = %s", (participant_id,))
            conn.execute("delete from participant where id = %s", (participant_id,))

