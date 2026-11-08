"""Окружение Alembic: URL из `DATABASE_URL` или `config.attributes["url"]`.

Почему так: адрес базы уже задан приложению (compose, systemd, тесты). Второй
адрес в `alembic.ini` разъехался бы с первым, поэтому файл конфигурации пустой.

Блокировка: `pg_advisory_lock(7311, 1)` берётся на соединении до наката. Два
одновременных `upgrade head` (деплой и таймер) не должны накатывать одну
ревизию дважды — второй ждёт, затем видит готовую цепочку и ничего не делает.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

MIGRATIONS_DIR = Path(__file__).resolve().parent
TOOL_API = MIGRATIONS_DIR.parent
if str(TOOL_API) not in sys.path:
    sys.path.insert(0, str(TOOL_API))

config = context.config

# Логирование из alembic.ini: Alembic 1.20 сам его не поднимает, а без него
# «Running upgrade» не печатается — строка, по которой проверяют факт наката.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Первый ключ блокировки — постоянный для проекта: 7311 = «схема СВОИ».
LOCK_KEY = 7311
LOCK_NAMESPACE = 1


def database_url() -> str:
    """Адрес базы: сначала attributes (тесты), затем окружение."""
    url = config.attributes.get("url") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL не задан: alembic не знает, к какой базе применять схему"
        )
    # Alembic работает через SQLAlchemy: драйвер psycopg3 называется postgresql+psycopg.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: текст наката без подключения к базе."""
    context.configure(
        url=database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=NullPool)
    with engine.connect() as connection:
        # Сессионная блокировка: живёт до unlock или до закрытия соединения.
        connection.exec_driver_sql(f"select pg_advisory_lock({LOCK_KEY}, {LOCK_NAMESPACE})")
        connection.commit()               # блокировка сессионная, коммит её не снимает
        try:
            context.configure(
                connection=connection,
                target_metadata=None,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.exec_driver_sql(
                f"select pg_advisory_unlock({LOCK_KEY}, {LOCK_NAMESPACE})")
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
