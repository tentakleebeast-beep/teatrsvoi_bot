"""db/init/027_staff_roles.sql

Revision ID: 027_staff_roles
Revises: 026_schedule_write
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "027_staff_roles"
down_revision = "026_schedule_write"
branch_labels = None
depends_on = None

SQL_FILE = "027_staff_roles.sql"
SQL_SHA256 = "e064760032f84ee0c563264d2daad7961cd01927bdcf54fde7fa3f7ddac0ee37"


def upgrade() -> None:
    """Накат db/init/027_staff_roles.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
