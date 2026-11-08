"""db/init/057_broadcast_admin_only.sql

Revision ID: 057_broadcast_admin_only
Revises: 056_notification_defaults
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "057_broadcast_admin_only"
down_revision = "056_notification_defaults"
branch_labels = None
depends_on = None

SQL_FILE = "057_broadcast_admin_only.sql"
SQL_SHA256 = "1ddb5b1d8fd932f9963bcf295c3a982ff095f0ae23afeb54405c1027d08346eb"


def upgrade() -> None:
    """Накат db/init/057_broadcast_admin_only.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
