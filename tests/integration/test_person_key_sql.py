"""
Ключ участника: одно правило в двух местах — Python и миграция 041.

Если SQL и Python разойдутся, регистрация начнёт писать «иванов-виктор», а
миграция при накате — «иванов виктор», и один человек станет двумя.

Правила и требования раздела: docs/architecture/10-data-model-v2.md.
"""

from __future__ import annotations

import psycopg
from app.person_key import make_key

SQL_EXPR = (
    r"select trim(both '-' from regexp_replace(regexp_replace("
    r"lower(translate(%s, 'ёЁ', 'еЕ')), '\([^)]*\)', '', 'g'),"
    r" '[^a-zа-я0-9]+', '-', 'g'))"
)

NAMES = [
    "Иванов Виктор",
    "Тарасова Алёна",
    "Олег Викторович",
    "Попова Света",
    "  Смирнова   Дарья  ",
    "Иванов Виктор (дубль)",
    "Васильева-Смирнова Дарья",
    "Пётр Ёлкин",
]


def test_sql_и_python_дают_один_ключ(test_db) -> None:
    with psycopg.connect(test_db, autocommit=True) as conn:
        for name in NAMES:
            got = conn.execute(SQL_EXPR, (name,)).fetchone()[0]
            assert got == make_key(name), f"{name}: SQL {got!r} != Python {make_key(name)!r}"


def test_ключ_с_пробелом_не_проходит_ограничение(test_db) -> None:
    """Пробел в ключе — это имя файла с пробелом; ограничение ловит такие записи."""
    with psycopg.connect(test_db, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("savepoint probe")
            raised = False
            try:
                conn.execute(
                    "insert into participant (tg_chat_id, display_name, person_key, role, status) "
                    "values (999777, 'Дарья Васильева', 'дарья васильева', 'troupe', 'active')")
            except psycopg.errors.CheckViolation:
                raised = True
                conn.execute("rollback to savepoint probe")
        assert raised, "ограничение participant_person_key_format не сработало"
