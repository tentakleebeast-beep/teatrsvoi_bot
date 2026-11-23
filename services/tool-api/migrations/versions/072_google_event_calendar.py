"""db/init/072_google_event_calendar.sql

Revision ID: 072_google_event_calendar
Revises: 071_drop_dead_capabilities
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "072_google_event_calendar"
down_revision = "071_drop_dead_capabilities"
branch_labels = None
depends_on = None

SQL_FILE = "072_google_event_calendar.sql"
SQL_SHA256 = "213b6055fcd351989c1c443a03f1f254546301e64da6edf5647eebbed8c64501"


def upgrade() -> None:
    """Накат db/init/072_google_event_calendar.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
