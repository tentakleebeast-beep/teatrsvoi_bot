"""db/init/014_intent_log.sql

Revision ID: 014_intent_log
Revises: 013_audit_source
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "014_intent_log"
down_revision = "013_audit_source"
branch_labels = None
depends_on = None

SQL_FILE = "014_intent_log.sql"
SQL_SHA256 = "ba77dbd80f9bf41af567959cfb0d401456340dfe26735c8faffee4aeeab45616"


def upgrade() -> None:
    """Накат db/init/014_intent_log.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
