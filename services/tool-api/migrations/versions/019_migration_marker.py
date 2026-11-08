"""db/init/019_migration_marker.sql

Revision ID: 019_migration_marker
Revises: 017_drop_student_role
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "019_migration_marker"
down_revision = "017_drop_student_role"
branch_labels = None
depends_on = None

SQL_FILE = "019_migration_marker.sql"
SQL_SHA256 = "4df9cbe854e643ee3d7197096ee61c0305df6aced45dc3cac86cfc7b9cceaa25"


def upgrade() -> None:
    """Накат db/init/019_migration_marker.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
