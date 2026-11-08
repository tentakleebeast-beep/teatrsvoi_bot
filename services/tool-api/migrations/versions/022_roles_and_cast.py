"""db/init/022_roles_and_cast.sql

Revision ID: 022_roles_and_cast
Revises: 021_uncertain_kind
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "022_roles_and_cast"
down_revision = "021_uncertain_kind"
branch_labels = None
depends_on = None

SQL_FILE = "022_roles_and_cast.sql"
SQL_SHA256 = "fe367dc5fbef105c3a9d06beef45be8f05c01b0203647e01f1af8e3bd9a0dfa4"


def upgrade() -> None:
    """Накат db/init/022_roles_and_cast.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
