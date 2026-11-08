"""db/init/017_drop_student_role.sql

Revision ID: 017_drop_student_role
Revises: 016_availability_invert
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "017_drop_student_role"
down_revision = "016_availability_invert"
branch_labels = None
depends_on = None

SQL_FILE = "017_drop_student_role.sql"
SQL_SHA256 = "1bad3ca1d611c2b7a0d1a3d30a107c594c3089b4890120a3a7bfce5735fb926f"


def upgrade() -> None:
    """Накат db/init/017_drop_student_role.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
