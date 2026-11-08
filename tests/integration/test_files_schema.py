"""Схема файлов (миграция 046, docs/development/86): правила держит база, а не код.

Проверяется то, что легко потерять при правке SQL: глубина папок считается сама, шестой
уровень невозможен, личная папка не может быть без владельца, две общие папки не могут
иметь один путь, поиск находит файл по тегу и по тексту, видео в `kind` не проходит.
"""

from __future__ import annotations

import psycopg
import pytest


def _person(conn, chat_id: int, name: str = "Тест Файлов") -> int:
    return conn.execute(
        "insert into participant (tg_chat_id, display_name, person_key, role, status) "
        "values (%s, %s, %s, 'troupe', 'active') returning id",
        (chat_id, name, f"test-{chat_id}")).fetchone()[0]


def _file(conn, pid: int, name: str = "Пурга — музыка.pdf") -> int:
    return conn.execute(
        "insert into file_object (participant_id, name, mime, size_bytes, scope, uploaded_via,"
        " object_key, bucket, sha256, kind) values (%s,%s,'application/pdf',1234,'personal',"
        "'webapp',%s,'svoi-private',repeat('a',64),'text') returning id",
        (pid, name, f"private/{pid}.pdf")).fetchone()[0]


def test_глубина_папки_считается_от_родителя(test_db: str) -> None:
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 777001)
        root = conn.execute(
            "insert into folder (scope, participant_id, name, path) "
            "values ('personal', %s, 'Мои документы', 'Мои документы') returning id",
            (pid,)).fetchone()[0]
        child = conn.execute(
            "insert into folder (scope, participant_id, parent_id, name, path) "
            "values ('personal', %s, %s, 'Аудио', 'Мои документы/Аудио') returning id",
            (pid, root)).fetchone()[0]
        depth = conn.execute("select depth from folder where id = %s", (child,)).fetchone()[0]
    assert depth == 2


def test_шестой_уровень_запрещён(test_db: str) -> None:
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 777002)
        parent: int | None = None
        path = ""
        for level, name in enumerate(("Мои документы", "Аудио", "Пурга", "музыка", "финал"), start=1):
            path = name if not path else f"{path}/{name}"
            parent = conn.execute(
                "insert into folder (scope, participant_id, parent_id, name, path) "
                "values ('personal', %s, %s, %s, %s) returning id",
                (pid, parent, name, path)).fetchone()[0]
            assert level <= 5
        with pytest.raises(psycopg.errors.RaiseException, match="5 уровней"):
            conn.execute(
                "insert into folder (scope, participant_id, parent_id, name, path) "
                "values ('personal', %s, %s, 'ещё', %s)",
                (pid, parent, path + "/ещё"))


def test_две_общие_папки_с_одним_путём_невозможны(test_db: str) -> None:
    """Ровно та ошибка, которую пропускал бы unique(scope, participant_id, path): NULL ≠ NULL."""
    with psycopg.connect(test_db) as conn:
        conn.execute("insert into folder (scope, name, path) values ('shared','Реквизит','Реквизит')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("insert into folder (scope, name, path)"
                         " values ('shared','Реквизит','Реквизит')")


def test_личная_папка_без_владельца_невозможна(test_db: str) -> None:
    with psycopg.connect(test_db) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("insert into folder (scope, name, path) values ('personal','Мои документы','Мои документы')")


def test_поиск_находит_по_тегу_и_по_тексту(test_db: str) -> None:
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 777003)
        file_id = _file(conn, pid)
        tag_id = conn.execute(
            "insert into tag (slug, title) values ('purga','Пурга') returning id").fetchone()[0]
        conn.execute("insert into file_tag (file_id, tag_id) values (%s,%s)", (file_id, tag_id))
        conn.execute("insert into file_text (file_id, body) values (%s,%s)",
                     (file_id, "Выход Пьеро после антракта"))
        row = conn.execute(
            "select search_tsv @@ to_tsquery('russian','пурга') as by_tag,"
            "       search_tsv @@ plainto_tsquery('russian','антракта') as by_text"
            "  from file_object where id = %s", (file_id,)).fetchone()
    assert row == (True, True)


def test_видео_не_принимается(test_db: str) -> None:
    with psycopg.connect(test_db) as conn:
        pid = _person(conn, 777004)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into file_object (participant_id, name, scope, uploaded_via, kind) "
                "values (%s,'x.mp4','personal','bot','video')", (pid,))


def test_инварианты_файлов_чисты_на_пустой_базе(test_db: str) -> None:
    with psycopg.connect(test_db) as conn:
        broken = conn.execute("select code, detail from v_file_checks where not ok").fetchall()
    assert broken == []
