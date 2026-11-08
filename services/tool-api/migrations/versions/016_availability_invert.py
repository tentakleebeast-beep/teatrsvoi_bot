"""db/init/016_availability_invert.sql

Revision ID: 016_availability_invert
Revises: 015_categories_seed
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "016_availability_invert"
down_revision = "015_categories_seed"
branch_labels = None
depends_on = None

SQL_FILE = "016_availability_invert.sql"
SQL_SHA256 = "30857c501eda4ef5bf9451564ff92618c427c85dda7597c309833d3a2bf6a911"


def upgrade() -> None:
    """Накат db/init/016_availability_invert.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
