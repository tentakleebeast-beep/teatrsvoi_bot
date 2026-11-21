"""db/init/069_personal_invite_code.sql

Revision ID: 069_personal_invite_code
Revises: 068_org_without_approve
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "069_personal_invite_code"
down_revision = "068_org_without_approve"
branch_labels = None
depends_on = None

SQL_FILE = "069_personal_invite_code.sql"
SQL_SHA256 = "69087cce21367d77e91ff99efd645ab85f42edc8f8b773eee5581a9ca12f2268"


def upgrade() -> None:
    """Накат db/init/069_personal_invite_code.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
