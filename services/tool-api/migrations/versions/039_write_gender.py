"""db/init/039_write_gender.sql

Revision ID: 039_write_gender
Revises: 038_howto_read
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "039_write_gender"
down_revision = "038_howto_read"
branch_labels = None
depends_on = None

SQL_FILE = "039_write_gender.sql"
SQL_SHA256 = "9818e99e7beaa045f8fc2bd792544510fd1ebb3d73323202726640eb13993440"


def upgrade() -> None:
    """Накат db/init/039_write_gender.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
