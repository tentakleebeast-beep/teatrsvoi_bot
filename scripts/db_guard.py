#!/usr/bin/env python3
"""Защита от записи в рабочую БД из локального контура.

Зачем: 30.10.2026 тестовый прогон писал не в `svoi_test_<pid>`, а в dev-базу `svoi`
и оставил 21 строку на 177 900 ₽ — баланс врал на эту сумму, а инвариант I2
показывал «дубли операций» (разбор: docs/reports/84, карта канбана t_a422101b).

Правило: пишущий скрипт работает по умолчанию только с тестовой базой
(`svoi_test_*`). Рабочая база (`svoi`, `svoi_migr`) требует явного разрешения:

    python scripts/import_finance.py --apply --allow-dev-write
    ALLOW_DEV_WRITE=1 python scripts/import_finance.py --apply

Читающие прогоны (`--dry-run`, план, отчёты) guard не трогает.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

WORK_DB_NAMES = ("svoi", "svoi_migr")
TEST_DB_PREFIX = "svoi_test_"
ALLOW_ENV = "ALLOW_DEV_WRITE"


def db_name(dsn: str) -> str:
    """Имя базы из DSN (без схемы, параметров и логина)."""
    path = urlsplit(dsn).path if "://" in dsn else dsn.rsplit("/", 1)[-1]
    return path.lstrip("/").split("?")[0].strip()


def is_test_db(dsn: str) -> bool:
    """Тестовая база — отдельная на процесс, pytest создаёт и удаляет её сам."""
    return db_name(dsn).startswith(TEST_DB_PREFIX)


def is_work_db(dsn: str) -> bool:
    return db_name(dsn) in WORK_DB_NAMES


def allowed_by_env() -> bool:
    return os.getenv(ALLOW_ENV, "").strip() in ("1", "true", "yes", "on")


def assert_writable(dsn: str, *, allow: bool = False, script: str = "") -> None:
    """Разрешить запись или остановить прогон с человеческим объяснением.

    ``allow`` — явный флаг CLI (``--allow-dev-write``), ``ALLOW_DEV_WRITE=1`` — то же
    через окружение. Тестовая база проходит всегда.
    """
    if is_test_db(dsn) or allow or allowed_by_env():
        return
    if not is_work_db(dsn):
        return  # незнакомая база: guard не угадывает, отвечает вызывающий код
    where = f"{script}: " if script else ""
    raise SystemExit(
        f"{where}запись в рабочую базу {db_name(dsn)!r} запрещена — это не тестовая база.\n"
        f"Тесты и ручные проверки идут в {TEST_DB_PREFIX}<pid> (pytest создаёт её сам).\n"
        f"Если запись в рабочую базу действительно нужна, повтори с явным разрешением:\n"
        f"    --allow-dev-write   или   {ALLOW_ENV}=1\n")


def add_argument(parser) -> None:
    """Общий флаг для argparse пишущих скриптов."""
    parser.add_argument("--allow-dev-write", action="store_true",
                        help="разрешить запись в рабочую базу svoi (по умолчанию запрещено)")


if __name__ == "__main__":  # маленькая самопроверка: python scripts/db_guard.py
    for dsn, allow in (
        ("postgresql://u:p@127.0.0.1:5433/svoi_test_123", False),
        ("postgresql://u:p@127.0.0.1:5433/svoi", True),
    ):
        assert_writable(dsn, allow=allow, script="self-check")
        print("ok:", db_name(dsn), "allow=", allow)
    try:
        assert_writable("postgresql://u:p@127.0.0.1:5433/svoi", script="self-check")
    except SystemExit as exc:
        print("отказ как ожидалось:", str(exc).splitlines()[0])
    else:
        raise SystemExit("guard не сработал")
