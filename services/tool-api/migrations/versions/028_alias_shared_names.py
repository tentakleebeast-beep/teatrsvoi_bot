"""db/init/028_alias_shared_names.sql

Revision ID: 028_alias_shared_names
Revises: 027_staff_roles
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "028_alias_shared_names"
down_revision = "027_staff_roles"
branch_labels = None
depends_on = None

SQL_FILE = "028_alias_shared_names.sql"
SQL_SHA256 = "3d8d9764b96e17f0bb8f84c5483a43dc8d695541c6a2fcf781d66dd4841b7e36"


def upgrade() -> None:
    """Накат db/init/028_alias_shared_names.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
