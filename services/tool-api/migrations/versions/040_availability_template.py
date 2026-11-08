"""db/init/040_availability_template.sql

Revision ID: 040_availability_template
Revises: 039_write_gender
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "040_availability_template"
down_revision = "039_write_gender"
branch_labels = None
depends_on = None

SQL_FILE = "040_availability_template.sql"
SQL_SHA256 = "c0903b15ac14e9d1c4cb81ddef4a73aa16c844524c2e3b17f4248a7392bc643d"


def upgrade() -> None:
    """Накат db/init/040_availability_template.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
