"""SQL `file_ext` и Python `file_kind.extension` — одно правило (docs/development/192 §4.3).

Расширение нужно в двух местах: в Python — чтобы решить вид файла, в SQL — чтобы фильтр
чипа (`kind_predicate`) не завёл второй список. Расхождение здесь означает, что чип и
слово вида спорят: файл виден под «Документами», а называется «текстом».

Возвращается расширение **с точкой** и `''` (никогда `null`): иначе `not in (...)` дал бы
`NULL`, и строка молча выпала бы из фильтра.

Обоснование: FR-K17, docs/development/189 §D4, AC-7.
"""

from __future__ import annotations

import psycopg
import pytest

from app import file_kind

# Имена из замера §4.3: регистр, ведущая точка, двойное расширение, имя без расширения.
NAMES = [
    "Смета.pdf",
    "Смета.PDF",
    "заметки.md",
    ".docx",
    ".hidden",
    "x.",
    "архив.tar.gz",
    "a.b.c",
    "без расширения",
    "Пурга 2025",
    "",
    "IMG_1.xyz",
]


def _sql_ext(conn, name: str) -> str:
    return conn.execute("select file_ext(%s)", (name,)).fetchone()[0]


@pytest.mark.parametrize("name", NAMES)
def test_sql_и_python_совпадают(test_db: str, name: str) -> None:
    """12 имён: SQL и Python отвечают одинаково — иначе чип и слово разойдутся."""
    with psycopg.connect(test_db) as conn:
        sql = _sql_ext(conn, name)
    assert sql == file_kind.extension(name), f"{name!r}: SQL {sql!r}, Python {file_kind.extension(name)!r}"


def test_пустая_строка_вместо_null(test_db: str) -> None:
    """Имя без расширения даёт `''`, а не `null`: `null` молча выкидывает строку из фильтра."""
    with psycopg.connect(test_db) as conn:
        for name in ("без расширения", "", "Пурга 2025", "x."):
            value = _sql_ext(conn, name)
            assert value is not None, f"{name!r}: функция вернула null"
        assert _sql_ext(conn, None) == ""


def test_expand_из_одних_точек_известное_расхождение(test_db: str) -> None:
    """`..` — единственное известное расхождение (SQL `.`, Python `..`), и оно безвредно.

    Оба значения не входят ни в один набор расширений: слово и чип у такой строки
    одинаковы («файл», только «Все»). Пишем это явно, чтобы свойство разбиения не
    краснело на выдуманном различии.
    """
    with psycopg.connect(test_db) as conn:
        assert _sql_ext(conn, "..") == "."
    assert file_kind.extension("..") == ".."
    for value in (".", ".."):
        assert value not in file_kind.DOC_EXT | file_kind.PLAIN_EXT
        assert value not in file_kind.IMAGE_EXT | file_kind.AUDIO_EXT
