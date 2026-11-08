"""db/init/031_feedback_and_manifest.sql

Revision ID: 031_feedback_and_manifest
Revises: 030_cleanup_roles_aliases
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "031_feedback_and_manifest"
down_revision = "030_cleanup_roles_aliases"
branch_labels = None
depends_on = None

SQL_FILE = "031_feedback_and_manifest.sql"
SQL_SHA256 = "f70c29d2825da7b96aa13feece96f390827ad9c161201ea49e2b09f21b669e8d"


def upgrade() -> None:
    """Накат db/init/031_feedback_and_manifest.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
