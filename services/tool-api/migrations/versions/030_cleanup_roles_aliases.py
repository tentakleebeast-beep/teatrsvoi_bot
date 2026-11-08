"""db/init/030_cleanup_roles_aliases.sql

Revision ID: 030_cleanup_roles_aliases
Revises: 029_disciplines
Create Date: 2026-11-05

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл сформирован scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = "030_cleanup_roles_aliases"
down_revision = "029_disciplines"
branch_labels = None
depends_on = None

SQL_FILE = "030_cleanup_roles_aliases.sql"
SQL_SHA256 = "6cf9ad4303d1f810f758748166ae2d058aefc238c5be6ce1e283fe9f400cefac"


def upgrade() -> None:
    """Накат db/init/030_cleanup_roles_aliases.sql целиком."""
    # Импорт внутри функции: `alembic heads/history` читают ревизии без env.py,
    # а путь к пакету `migrations` добавляет env.py от своего __file__, не от cwd.
    from migrations.sql import run_sql  # noqa: PLC0415

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
