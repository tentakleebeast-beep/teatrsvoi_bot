"""db/init/023_alias_scopes.sql

Revision ID: 023_alias_scopes
Revises: 022_roles_and_cast
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "023_alias_scopes"
down_revision = "022_roles_and_cast"
branch_labels = None
depends_on = None

SQL_FILE = "023_alias_scopes.sql"
SQL_SHA256 = "75ff1eca3bba9f4c200286932cdb8ffeca654d3759b0f67df4137348b0e70c98"


def upgrade() -> None:
    """Накат db/init/023_alias_scopes.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
