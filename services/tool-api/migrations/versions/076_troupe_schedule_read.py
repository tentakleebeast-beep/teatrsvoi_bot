"""db/init/076_troupe_schedule_read.sql

Revision ID: 076_troupe_schedule_read
Revises: 075_projects_money
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "076_troupe_schedule_read"
down_revision = "075_projects_money"
branch_labels = None
depends_on = None

SQL_FILE = "076_troupe_schedule_read.sql"
SQL_SHA256 = "e82e3c33e0f3ae7d99407c315b245514d787b11d9141bfe628f40cd30dc8c113"


def upgrade() -> None:
    """Накат db/init/076_troupe_schedule_read.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
