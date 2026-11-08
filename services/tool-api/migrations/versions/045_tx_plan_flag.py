"""db/init/045_tx_plan_flag.sql

Revision ID: 045_tx_plan_flag
Revises: 044_org_menu_caps
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "045_tx_plan_flag"
down_revision = "044_org_menu_caps"
branch_labels = None
depends_on = None

SQL_FILE = "045_tx_plan_flag.sql"
SQL_SHA256 = "58fe10666a51d9b76c7b444949ba657dae66ae1f7c4b48d1154442c1386b7186"


def upgrade() -> None:
    """Накат db/init/045_tx_plan_flag.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
