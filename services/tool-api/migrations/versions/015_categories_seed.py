"""db/init/015_categories_seed.sql

Revision ID: 015_categories_seed
Revises: 014_intent_log
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "015_categories_seed"
down_revision = "014_intent_log"
branch_labels = None
depends_on = None

SQL_FILE = "015_categories_seed.sql"
SQL_SHA256 = "d645a66d4ba984a20b4d8675bdcfc32cbec47e50e5bac5b51f229f69f151fadc"


def upgrade() -> None:
    """Накат db/init/015_categories_seed.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
