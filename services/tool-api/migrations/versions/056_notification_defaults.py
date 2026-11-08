"""db/init/056_notification_defaults.sql

Revision ID: 056_notification_defaults
Revises: 055_agent_observability_cost
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "056_notification_defaults"
down_revision = "055_agent_observability_cost"
branch_labels = None
depends_on = None

SQL_FILE = "056_notification_defaults.sql"
SQL_SHA256 = "7b7938ec0e67b227833d1893c91e1248bf1ef5bbcead926efa74c90b2fded572"


def upgrade() -> None:
    """Накат db/init/056_notification_defaults.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
