"""db/init/066_polls_org_manage.sql

Revision ID: 066_polls_org_manage
Revises: 065_polls
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "066_polls_org_manage"
down_revision = "065_polls"
branch_labels = None
depends_on = None

SQL_FILE = "066_polls_org_manage.sql"
SQL_SHA256 = "9e4180c40535ab9aa0c0fc200840f22ff956612e125add673a263d5dd4f7dc94"


def upgrade() -> None:
    """Накат db/init/066_polls_org_manage.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
