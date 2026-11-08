#!/usr/bin/env python3
"""Ротация файлов на холодный том (Ш-11, docs/development/86).

    python scripts/cold_rotate.py                 # перенести всё, что не открывали 180 дней
    python scripts/cold_rotate.py --days 90
    python scripts/cold_rotate.py --dry-run       # показать, что поедет, ничего не двигая

Ставится таймером раз в сутки: холод — не срочная операция, но и не ручная.
Проверка фактом: `python scripts/cold_rotate.py --status` показывает, сколько лежит
горячим и холодным и сколько осталось места на холодном томе.
"""

from __future__ import annotations

import argparse
import sys

import psycopg
from _cli import add_tool_api_path, apply_env, dsn
from psycopg.rows import dict_row

apply_env()
add_tool_api_path()

from app import cold  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Холод и ротация файлов")
    parser.add_argument("--days", type=int, default=cold.DEFAULT_DAYS)
    parser.add_argument("--limit", type=int, default=cold.DEFAULT_LIMIT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true", help="показать состояние и выйти")
    args = parser.parse_args()

    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        if args.status:
            data = cold.status(conn)
            space = cold.free_space()
            print(f"горячих файлов: {data['hot_files']} ({data['hot_bytes'] // (1024 ** 2)} МБ)")
            print(f"в холоде:      {data['cold_files']} ({data['cold_bytes'] // (1024 ** 2)} МБ)")
            print(f"холодный том:  {data['cold_dir']} "
                  f"свободно {space['free'] // (1024 ** 3)} ГБ")
            return 0

        if args.dry_run:
            rows = conn.execute(
                "select count(*) as n, coalesce(sum(size_bytes), 0) as bytes from file_object"
                " where deleted_at is null and cold_at is null and object_key is not null"
                "   and coalesce(last_access_at, created_at) < now() - make_interval(days => %s)",
                (args.days,)).fetchone()
            print(f"поедет в холод: {rows['n']} файлов, {rows['bytes'] // (1024 ** 2)} МБ")
            return 0

        result = cold.move_to_cold(conn, args.days, args.limit)
        conn.commit()
    print(f"перенесено: {result['moved']} из {result['checked']}, "
          f"{result['bytes'] // (1024 ** 2)} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
