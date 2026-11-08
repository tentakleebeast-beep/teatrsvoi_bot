"""Выполнение файлов `db/init/*.sql` как ревизий Alembic.

Схема описана один раз — в `db/init`. Ревизия называет файл и его sha256: правка
применённого файла меняла бы историю молча, поэтому ACL наката — сверка хэша.

`BEGIN;`/`COMMIT;` из файлов вырезаются. Alembic ведёт транзакцию сам
(`transaction_per_migration=True`), и COMMIT внутри файла закрыл бы её вместе с
записью версии: накат «прошёл», а учёт остался на предыдущей ревизии.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# migrations/sql.py → migrations → tool-api → services → корень репозитория
INIT_DIR = Path(__file__).resolve().parents[3] / "db" / "init"

_TRANSACTION_LINE = re.compile(r"^\s*(?:begin|commit)\s*;\s*$", re.IGNORECASE | re.MULTILINE)


def sql_path(filename: str) -> Path:
    """Путь к файлу схемы. Только имя файла: подкаталоги не поддерживаются."""
    if "/" in filename or "\\" in filename:
        raise ValueError(f"ожидается имя файла db/init, получено {filename!r}")
    return INIT_DIR / filename


def read_sql(filename: str, sha256: str) -> str:
    """Текст файла без строк BEGIN/COMMIT после сверки хэша."""
    data = sql_path(filename).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise RuntimeError(
            f"db/init/{filename}: файл изменился после ревизии "
            f"(sha256 {actual}, в ревизии {sha256}). Применённый файл не правится — "
            f"добавьте новый db/init/NNN_*.sql и новую ревизию"
        )
    return _TRANSACTION_LINE.sub("", data.decode("utf-8"))


def run_sql(op, filename: str, sha256: str) -> None:
    """Выполнить файл целиком одним вызовом драйвера.

    Курсор psycopg берётся напрямую, а не через `exec_driver_sql`: SQLAlchemy
    передаёт в драйвер пустой кортеж параметров, psycopg3 разбирает запрос как
    шаблон и падает на одиночном `%` в SQL-тексте («incomplete placeholder»).
    Без аргументов параметров psycopg шлёт текст простым протоколом, поэтому
    несколько операторов в одном файле выполняются как есть (проверено на 001
    и 016). Транзакция остаётся транзакцией Alembic — соединение то же.
    """
    sql = read_sql(filename, sha256)
    driver_connection = op.get_bind().connection.driver_connection
    with driver_connection.cursor() as cursor:
        cursor.execute(sql)
