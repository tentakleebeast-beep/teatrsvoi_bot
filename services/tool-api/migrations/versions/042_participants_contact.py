"""db/init/042_participants_contact.sql

Revision ID: 042_participants_contact
Revises: 041_person_key_full
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "042_participants_contact"
down_revision = "041_person_key_full"
branch_labels = None
depends_on = None

SQL_FILE = "042_participants_contact.sql"
SQL_SHA256 = "3b7155a50fb97be4e67b3decb4ce37b434573dcb00e6ed7cede00293a00698c8"


def upgrade() -> None:
    """Накат db/init/042_participants_contact.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
