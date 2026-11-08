"""db/init/055_agent_observability_cost.sql

Revision ID: 055_agent_observability_cost
Revises: 054_folder_roots
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "055_agent_observability_cost"
down_revision = "054_folder_roots"
branch_labels = None
depends_on = None

SQL_FILE = "055_agent_observability_cost.sql"
SQL_SHA256 = "ce950e1324784db217fe396796461ece9d02c3a6e7e94f327cd0f9ad3450514a"


def upgrade() -> None:
    """Накат db/init/055_agent_observability_cost.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
