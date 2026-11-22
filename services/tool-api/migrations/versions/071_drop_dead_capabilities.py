"""db/init/071_drop_dead_capabilities.sql

Revision ID: 071_drop_dead_capabilities
Revises: 070_personal_invite_code
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "071_drop_dead_capabilities"
down_revision = "070_personal_invite_code"
branch_labels = None
depends_on = None

SQL_FILE = "071_drop_dead_capabilities.sql"
SQL_SHA256 = "b6128d08843136698dc1d85cfc85f0ecab1b07f3a557ba02aed875169cfeb2cc"


def upgrade() -> None:
    """Накат db/init/071_drop_dead_capabilities.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
