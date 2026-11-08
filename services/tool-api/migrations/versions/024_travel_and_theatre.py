"""db/init/024_travel_and_theatre.sql

Revision ID: 024_travel_and_theatre
Revises: 023_alias_scopes
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "024_travel_and_theatre"
down_revision = "023_alias_scopes"
branch_labels = None
depends_on = None

SQL_FILE = "024_travel_and_theatre.sql"
SQL_SHA256 = "7ae8b070fae6bb4ac3619ed97d32d27d73f47af189050c85d497c18a89d1e6e6"


def upgrade() -> None:
    """Накат db/init/024_travel_and_theatre.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
