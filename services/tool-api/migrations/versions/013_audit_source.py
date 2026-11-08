"""db/init/013_audit_source.sql

Revision ID: 013_audit_source
Revises: 012_idempotency
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "013_audit_source"
down_revision = "012_idempotency"
branch_labels = None
depends_on = None

SQL_FILE = "013_audit_source.sql"
SQL_SHA256 = "fde8edd24990277726ba096fe665bd5e5a52f0bcf81cb39676accec7aa317f95"


def upgrade() -> None:
    """Накат db/init/013_audit_source.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
