#!/usr/bin/env python3
"""Генератор ревизий Alembic: один файл `db/init/*.sql` → одна ревизия.

Зачем генератор, а не `alembic revision` руками: ревизии — механическая обёртка
над SQL-файлами (документ + хэш + вызов `run_sql`). Руками в 57 файлах
появлялись бы расхождения в оформлении и забытые хэши, а хэш здесь — защита от
правки применённого файла.

    env -u PYTHONPATH .venv/bin/python scripts/gen_alembic_revisions.py
    env -u PYTHONPATH .venv/bin/python scripts/gen_alembic_revisions.py --check

`--check` ничего не пишет: сверяет существующие ревизии с db/init и возвращает
1, если чего-то не хватает (нужно тесту реестра и CI).

Правило эксплуатации: применимые файлы не правятся. Новая схема — новый файл с
новым номером; после него запускается этот скрипт, и появляется новая ревизия.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_DIR = ROOT / "db" / "init"
VERSIONS_DIR = ROOT / "services" / "tool-api" / "migrations" / "versions"

HEADER = '''"""db/init/{sql_file}

Revision ID: {revision}
Revises: {down_revision_display}
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "{revision}"
down_revision = {down_revision}
branch_labels = None
depends_on = None

SQL_FILE = "{sql_file}"
SQL_SHA256 = "{sha256}"


def upgrade() -> None:
    """Накат db/init/{sql_file} целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
'''


def revisions() -> list[tuple[str, str, str]]:
    """(stem, имя SQL-файла, sha256) по порядку имён файлов."""
    rows = []
    for path in sorted(INIT_DIR.glob("*.sql")):
        rows.append((path.stem, path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return rows


def render(revision: str, sql_file: str, sha256: str, down_revision: str | None) -> str:
    return HEADER.format(
        revision=revision,
        sql_file=sql_file,
        sha256=sha256,
        down_revision="None" if down_revision is None else f'"{down_revision}"',
        down_revision_display="—" if down_revision is None else down_revision,
    )


def expected_files() -> dict[str, str]:
    """Имя файла ревизии → её текст."""
    result: dict[str, str] = {}
    previous: str | None = None
    for stem, sql_file, sha256 in revisions():
        result[f"{stem}.py"] = render(stem, sql_file, sha256, previous)
        previous = stem
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ревизии Alembic из файлов db/init (dev-инструмент)")
    parser.add_argument("--check", action="store_true",
                        help="только сверить, ничего не писать")
    args = parser.parse_args(argv)

    wanted = expected_files()
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    stale = sorted(path.name for path in VERSIONS_DIR.glob("*.py")
                   if path.name not in wanted)
    mismatched, missing = [], []
    for name, text in wanted.items():
        path = VERSIONS_DIR / name
        if not path.exists():
            missing.append(name)
        elif path.read_text(encoding="utf-8") != text:
            mismatched.append(name)

    if args.check:
        for name in missing:
            print(f"нет ревизии: {name}")
        for name in mismatched:
            print(f"ревизия разошлась с db/init: {name}")
        for name in stale:
            print(f"лишняя ревизия: {name}")
        return 0 if not (missing or mismatched or stale) else 1

    for name, text in wanted.items():
        (VERSIONS_DIR / name).write_text(text, encoding="utf-8")
    for name in stale:
        (VERSIONS_DIR / name).unlink()

    chain = sorted(wanted)
    print(f"ревизий: {len(chain)}")
    print(f"head: {Path(chain[-1]).stem if chain else '—'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
