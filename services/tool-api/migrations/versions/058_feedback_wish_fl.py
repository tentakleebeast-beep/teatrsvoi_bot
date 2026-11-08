"""db/init/058_feedback_wish_fl.sql

Revision ID: 058_feedback_wish_fl
Revises: 057_broadcast_admin_only
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "058_feedback_wish_fl"
down_revision = "057_broadcast_admin_only"
branch_labels = None
depends_on = None

SQL_FILE = "058_feedback_wish_fl.sql"
SQL_SHA256 = "b8892cb93e3cbc2e9d7acf1349053b442a0aa7e6ea587cb420e51cbadcd0eefd"


def upgrade() -> None:
    """Накат db/init/058_feedback_wish_fl.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
