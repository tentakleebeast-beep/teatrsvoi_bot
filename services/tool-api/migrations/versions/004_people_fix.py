"""db/init/004_people_fix.sql

Revision ID: 004_people_fix
Revises: 003_categories_v2
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "004_people_fix"
down_revision = "003_categories_v2"
branch_labels = None
depends_on = None

SQL_FILE = "004_people_fix.sql"
SQL_SHA256 = "9d3e3ff2f63ef2e6bd4f4b8e39f5e82dc1c4b47e1f5414d21a5175bd6e3e7ed0"


def upgrade() -> None:
    """Накат db/init/004_people_fix.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
