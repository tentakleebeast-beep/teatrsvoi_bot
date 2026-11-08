"""db/init/${sql_file}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Обоснование: единственный источник схемы — db/init/*.sql (docs/reports/132).
Файл формируется scripts/gen_alembic_revisions.py; руками не правится.
"""

from __future__ import annotations

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

SQL_FILE = ${repr(sql_file)}
SQL_SHA256 = ${repr(sql_sha256)}


def upgrade() -> None:
    """Накат db/init/${sql_file} целиком."""
    from migrations.sql import run_sql  # noqa: PLC0415 — импорт на уровне модуля
    # ломает `alembic heads`: каталог migrations не в sys.path без env.py.

    run_sql(op, SQL_FILE, SQL_SHA256)


def downgrade() -> None:
    raise NotImplementedError(
        "откат схемы не поддерживается (решение 518): миграции аддитивные")
