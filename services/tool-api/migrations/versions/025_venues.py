"""db/init/025_venues.sql

Revision ID: 025_venues
Revises: 024_travel_and_theatre
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "025_venues"
down_revision = "024_travel_and_theatre"
branch_labels = None
depends_on = None

SQL_FILE = "025_venues.sql"
SQL_SHA256 = "c82098acddfa7ea2eddf0bc129ca979253228e25bf64cc6eab38dcd558bc3a30"


def upgrade() -> None:
    """Накат db/init/025_venues.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
