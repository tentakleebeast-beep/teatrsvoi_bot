"""db/init/029_disciplines.sql

Revision ID: 029_disciplines
Revises: 028_alias_shared_names
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "029_disciplines"
down_revision = "028_alias_shared_names"
branch_labels = None
depends_on = None

SQL_FILE = "029_disciplines.sql"
SQL_SHA256 = "3612e6226e78092e872f250e2fd1a09595f2038a9336514c7732fc16decc61ec"


def upgrade() -> None:
    """Накат db/init/029_disciplines.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
