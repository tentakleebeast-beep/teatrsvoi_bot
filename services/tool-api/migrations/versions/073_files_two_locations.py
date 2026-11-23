"""db/init/073_files_two_locations.sql

Revision ID: 073_files_two_locations
Revises: 072_google_event_calendar
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "073_files_two_locations"
down_revision = "072_google_event_calendar"
branch_labels = None
depends_on = None

SQL_FILE = "073_files_two_locations.sql"
SQL_SHA256 = "6fde1cee195d6d3cef5a1232f76ca2b03b2496fcf68438b38ff115c2849f373c"


def upgrade() -> None:
    """Накат db/init/073_files_two_locations.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
