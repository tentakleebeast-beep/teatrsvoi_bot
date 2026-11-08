"""db/init/038_howto_read.sql

Revision ID: 038_howto_read
Revises: 037_participant_contacts
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "038_howto_read"
down_revision = "037_participant_contacts"
branch_labels = None
depends_on = None

SQL_FILE = "038_howto_read.sql"
SQL_SHA256 = "915fa778a2769ae3864031b2abafe05ef0cd4e8947723e939e4b0a3f2cdecdbc"


def upgrade() -> None:
    """Накат db/init/038_howto_read.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
