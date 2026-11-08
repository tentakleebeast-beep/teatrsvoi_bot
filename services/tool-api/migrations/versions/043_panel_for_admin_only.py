"""db/init/043_panel_for_admin_only.sql

Revision ID: 043_panel_for_admin_only
Revises: 042_participants_contact
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "043_panel_for_admin_only"
down_revision = "042_participants_contact"
branch_labels = None
depends_on = None

SQL_FILE = "043_panel_for_admin_only.sql"
SQL_SHA256 = "dabffd04378b3c1869f99513752542e1fe255f59d24bb97468c0dbc0a84f4f71"


def upgrade() -> None:
    """Накат db/init/043_panel_for_admin_only.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
