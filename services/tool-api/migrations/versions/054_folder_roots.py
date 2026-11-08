"""db/init/054_folder_roots.sql

Revision ID: 054_folder_roots
Revises: 053_income_misc_category
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "054_folder_roots"
down_revision = "053_income_misc_category"
branch_labels = None
depends_on = None

SQL_FILE = "054_folder_roots.sql"
SQL_SHA256 = "85c30af7751bc3178bc4ec7827c52d0da7787dbbbef83f5471f7bb94e37bfa0a"


def upgrade() -> None:
    """Накат db/init/054_folder_roots.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
