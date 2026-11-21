"""db/init/069_poll_interview_drafts.sql

Revision ID: 069_poll_interview_drafts
Revises: 068_org_without_approve
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "069_poll_interview_drafts"
down_revision = "068_org_without_approve"
branch_labels = None
depends_on = None

SQL_FILE = "069_poll_interview_drafts.sql"
SQL_SHA256 = "963487440ff3e874db64468621f206ae4211c44ac0bbb1f95d2a1211dabf107a"


def upgrade() -> None:
    """Накат db/init/069_poll_interview_drafts.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
