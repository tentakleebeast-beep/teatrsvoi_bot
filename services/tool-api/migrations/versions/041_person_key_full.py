"""db/init/041_person_key_full.sql

Revision ID: 041_person_key_full
Revises: 040_availability_template
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "041_person_key_full"
down_revision = "040_availability_template"
branch_labels = None
depends_on = None

SQL_FILE = "041_person_key_full.sql"
SQL_SHA256 = "3c94f97d9182b63142f20e591c940eb57600e921817585718c9a38fbb6d091b0"


def upgrade() -> None:
    """Накат db/init/041_person_key_full.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
