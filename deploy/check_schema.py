#!/usr/bin/env python3
"""Сверяет живую базу с миграциями в db/init — без изменений данных.

Зачем: восстановленная из дампа база может быть позади кода. Так было на проде
30.10.2026 — не хватало `participant.write_gender` и `budget_tx.is_plan`, и Mini App
падал с 500 на неделе занятости и на записи операции. Полный прогон цепочки
`db --migrate` на такой базе не подходит: часть миграций рассчитана на порядок
«от чистой схемы» (004 упирается в уникальность алиаса, которую позже ослабили).

    DATABASE_URL=... python3 deploy/check_schema.py            # только отчёт
    DATABASE_URL=... python3 deploy/check_schema.py --files    # плюс список файлов
    DATABASE_URL=... python3 deploy/check_schema.py --expect-head  # версия = head

`--expect-head` — read-only сверка `alembic_version` с головой цепочки ревизий
(docs/reports/132). Ею baseline после восстановления из дампа: `alembic stamp head`
ставится только когда структура уже равна цепочке, иначе выкат останавливается.

Код возврата 1, если чего-то не хватает.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

INIT_DIR = Path(__file__).resolve().parents[1] / "db" / "init"
COLUMN_RE = re.compile(r"alter\s+table\s+(\w+)\s+add\s+column\s+if\s+not\s+exists\s+(\w+)", re.I)
TABLE_RE = re.compile(r"create\s+table\s+if\s+not\s+exists\s+(\w+)", re.I)


def expected() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Что требуют миграции: таблицы и колонки, с указанием файла."""
    tables: dict[str, set[str]] = {}
    columns: dict[str, set[str]] = {}
    for path in sorted(INIT_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for name in TABLE_RE.findall(text):
            tables.setdefault(name, set()).add(path.name)
        for table, column in COLUMN_RE.findall(text):
            columns.setdefault(f"{table}.{column}", set()).add(path.name)
    return tables, columns


def head_revision(root: Path) -> str:
    """Голова цепочки ревизий из файлов, без подключения к базе."""
    from alembic.config import Config  # noqa: PLC0415 — нужен только с --expect-head
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"цепочка ревизий разветвлена: {sorted(heads)}")
    return heads[0]


def current_version(conn: psycopg.Connection) -> str | None:
    """Версия схемы из alembic_version; None — таблицы нет."""
    with conn.cursor() as cur:
        cur.execute("select to_regclass('public.alembic_version')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("select version_num from alembic_version")
        rows = cur.fetchall()
    return rows[0][0] if len(rows) == 1 else None


def check_expect_head(url: str, root: Path) -> int:
    """0 — версия базы равна head цепочки; 1 — отстала или учёта нет."""
    with psycopg.connect(url) as conn:
        version = current_version(conn)
    head = head_revision(root)
    if version is None:
        print(f"схема не применена: в alembic_version нет версии (head={head})")
        return 1
    if version != head:
        print(f"версия схемы {version} не равна head {head}")
        return 1
    print(f"схема совпадает с Alembic: {head}")
    return 0


def live(conn: psycopg.Connection) -> tuple[set[str], set[str]]:
    with conn.cursor() as cur:
        cur.execute("select table_name from information_schema.tables where table_schema = 'public'")
        tables = {row[0] for row in cur.fetchall()}
        cur.execute(
            "select table_name || '.' || column_name from information_schema.columns "
            "where table_schema = 'public'")
        columns = {row[0] for row in cur.fetchall()}
    return tables, columns


def main(argv: list[str]) -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL не задан")
        return 2
    show_files = "--files" in argv

    if "--expect-head" in argv:
        return check_expect_head(url, INIT_DIR.parents[1])

    want_tables, want_columns = expected()
    with psycopg.connect(url) as conn:
        have_tables, have_columns = live(conn)

    missing_tables = sorted(set(want_tables) - have_tables)
    missing_columns = sorted(set(want_columns) - have_columns)

    if not missing_tables and not missing_columns:
        print(f"схема совпадает с миграциями: таблиц {len(want_tables)}, колонок {len(want_columns)}")
        return 0

    for name in missing_tables:
        files = ", ".join(sorted(want_tables[name])) if show_files else ""
        print(f"нет таблицы: {name} {files}".rstrip())
    for name in missing_columns:
        files = ", ".join(sorted(want_columns[name])) if show_files else ""
        print(f"нет колонки: {name} {files}".rstrip())
    print(f"\nвсего не хватает: таблиц {len(missing_tables)}, колонок {len(missing_columns)}")
    print("применить точечно: docker exec -i <pg> psql -U <user> -d <db> -v ON_ERROR_STOP=1 < db/init/<файл>")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
