"""db/init/026_schedule_write.sql

Revision ID: 026_schedule_write
Revises: 025_venues
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "026_schedule_write"
down_revision = "025_venues"
branch_labels = None
depends_on = None

SQL_FILE = "026_schedule_write.sql"
SQL_SHA256 = "aecefdb067f7d427de152fbab70b84af6532c99285437e36f5fa8f1080a58968"


def upgrade() -> None:
    """Накат db/init/026_schedule_write.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
