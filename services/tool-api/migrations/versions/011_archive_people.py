"""db/init/011_archive_people.sql

Revision ID: 011_archive_people
Revises: 010_fixes
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "011_archive_people"
down_revision = "010_fixes"
branch_labels = None
depends_on = None

SQL_FILE = "011_archive_people.sql"
SQL_SHA256 = "a61b938acf27c3564dd540a0cf51e93d0bd91905d952a37cfc0e4de40e085c78"


def upgrade() -> None:
    """Накат db/init/011_archive_people.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
