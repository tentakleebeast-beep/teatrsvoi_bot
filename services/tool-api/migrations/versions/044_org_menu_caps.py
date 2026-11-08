"""db/init/044_org_menu_caps.sql

Revision ID: 044_org_menu_caps
Revises: 043_panel_for_admin_only
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "044_org_menu_caps"
down_revision = "043_panel_for_admin_only"
branch_labels = None
depends_on = None

SQL_FILE = "044_org_menu_caps.sql"
SQL_SHA256 = "3affd720b0733b484c3ce6b399cf899d07c7c4c4c62e5d8d158c32d6e6c208de"


def upgrade() -> None:
    """Накат db/init/044_org_menu_caps.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
