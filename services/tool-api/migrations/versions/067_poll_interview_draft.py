"""db/init/067_poll_interview_draft.sql

Revision ID: 067_poll_interview_draft
Revises: 066_polls_org_manage
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "067_poll_interview_draft"
down_revision = "066_polls_org_manage"
branch_labels = None
depends_on = None

SQL_FILE = "067_poll_interview_draft.sql"
SQL_SHA256 = "5d1422bf28676308f6b4f14cb9bebb1116c9415600845ea6ccf2a47409d383c2"


def upgrade() -> None:
    """Накат db/init/067_poll_interview_draft.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
