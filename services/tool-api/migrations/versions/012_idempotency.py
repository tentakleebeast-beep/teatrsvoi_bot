"""db/init/012_idempotency.sql

Revision ID: 012_idempotency
Revises: 011_archive_people
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "012_idempotency"
down_revision = "011_archive_people"
branch_labels = None
depends_on = None

SQL_FILE = "012_idempotency.sql"
SQL_SHA256 = "955d28defc6ec24f6e93645d85334710f3514c8fdba33ebc4857eb19a46c3fe1"


def upgrade() -> None:
    """Накат db/init/012_idempotency.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
