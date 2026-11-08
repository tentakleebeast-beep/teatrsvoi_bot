"""db/init/021_uncertain_kind.sql

Revision ID: 021_uncertain_kind
Revises: 020_approvals
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "021_uncertain_kind"
down_revision = "020_approvals"
branch_labels = None
depends_on = None

SQL_FILE = "021_uncertain_kind.sql"
SQL_SHA256 = "fc6638b8eb7d015c006fa93d4a4cbf72584937fb3a92e8bec8b0a835239e2617"


def upgrade() -> None:
    """Накат db/init/021_uncertain_kind.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
