"""db/init/068_org_without_approve.sql

Revision ID: 068_org_without_approve
Revises: 067_poll_interview_draft
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "068_org_without_approve"
down_revision = "067_poll_interview_draft"
branch_labels = None
depends_on = None

SQL_FILE = "068_org_without_approve.sql"
SQL_SHA256 = "7145446a3dd5c57f7ef0dd5e8aa050514490bd85cf17984f36f342b9eab254dc"


def upgrade() -> None:
    """Накат db/init/068_org_without_approve.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
