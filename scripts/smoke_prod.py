#!/usr/bin/env python3
"""Слой В смоука: проверки на проде — только чтение.

Зачем отдельный скрипт, а не «руками посмотреть». Прод — единственное место, где видно
доставку Telegram и запись в Google Sheets, но цена ошибки там другая: запись в боевые
данные необратима. Поэтому у скрипта ровно один режим — `--read-only`, и он не флаг
настроения, а жёсткая граница:

  * сессия Postgres открывается в режиме `read only` — любая запись падает на уровне БД,
    а не на честном слове автора;
  * попытка передать пишущий флаг (`--write`, `--apply`, `--marker`) — код 2 до подключения;
  * читаются только: `GET /health`, счётчики ключевых таблиц, sha256 коммита из ответа
    сервиса (сверяется с `git rev-parse` локальной ветки).

    PROD_DATABASE_URL=postgresql://… PROD_TOOL_API_URL=https://… \\
        python scripts/smoke_prod.py --read-only

До ответа на V-042 слой В в отчёте не выполняется: скрипт есть, прод не читается
(К12). Коды выхода: 0 — проверки прошли; 1 — прод не настроен или расхождение;
2 — попытка записи.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Ключевые таблицы: по ним видно, что прод жив и не пуст. Читаются только счётчики.
KEY_TABLES = ("participant", "budget_tx", "availability_hour", "event_plan", "feedback")
WRITE_FLAGS = ("write", "apply", "marker", "cleanup", "prepare")


def prod_dsn() -> str:
    """Строка подключения прода: только из окружения, из `.env` не читается."""
    return (os.getenv("PROD_DATABASE_URL") or "").strip()


def prod_api() -> str:
    return (os.getenv("PROD_TOOL_API_URL") or "").strip().rstrip("/")


def health(url: str) -> dict:
    """`GET /health` прода: код и поля ответа."""
    target = f"{url}/health"
    try:
        with urllib.request.urlopen(target, timeout=15) as response:  # noqa: S310 — явный адрес прода
            return {"url": target, "code": response.status,
                    "body": json.loads(response.read().decode("utf-8"))}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"url": target, "code": 0, "error": str(exc)[:200]}


def counts(dsn: str) -> dict[str, int]:
    """Счётчики ключевых таблиц в транзакции `read only`.

    Режим выставляется на уровне сессии, а не «мы обещаем не писать»: попытка записи
    из этого соединения получит `ReadOnlySqlTransaction` от Postgres.
    """
    import psycopg  # noqa: PLC0415

    result: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("set session characteristics as transaction read only")
        for table in KEY_TABLES:
            row = conn.execute(f"select count(*) from {table}").fetchone()
            result[table] = int(row[0])
    return result


def local_sha() -> str:
    """Полный sha локального HEAD: сверяется с полем `git_sha` ответа прода."""
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,  # noqa: S607
                          capture_output=True, text=True, check=False)
    return proc.stdout.strip()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Слой В: проверки на проде, только чтение")
    parser.add_argument("--read-only", action="store_true",
                        help="единственный режим: чтение (без флага скрипт откажется работать)")
    # Эти флаги существуют, чтобы отказ был явным: писать на прод нельзя (V-042).
    for flag in WRITE_FLAGS:
        parser.add_argument(f"--{flag}", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    used = [f"--{flag}" for flag in WRITE_FLAGS if getattr(args, flag, False)]
    if used:
        print(f"smoke_prod.py: запись на прод запрещена ({', '.join(used)}). "
              f"Единственный режим — --read-only (V-042).")
        return 2
    if not args.read_only:
        print("smoke_prod.py: укажите --read-only — других режимов у слоя В нет")
        return 2

    url, dsn = prod_api(), prod_dsn()
    if not url or not dsn:
        print("smoke_prod.py: прод не настроен (PROD_TOOL_API_URL, PROD_DATABASE_URL). "
              "Слой В не выполнялся: в отчёте колонка «не проверено», причина «нужен прод».")
        return 1

    report: dict = {"mode": "read-only", "health": health(url), "counts": {},
                    "commit": {}, "writes": 0}
    try:
        report["counts"] = counts(dsn)
    except Exception as exc:  # noqa: BLE001 — прод может не пускать чтение с этой машины
        report["counts_error"] = str(exc).splitlines()[0][:200]
    sha = local_sha()
    remote = (report["health"].get("body") or {}).get("git_sha", "")
    report["commit"] = {"local": sha, "prod": remote, "same": bool(remote) and remote == sha,
                        "local_digest": digest(sha)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"/health: код {report['health']['code']} ({report['health']['url']})")
        for table, count in report["counts"].items():
            print(f"{table:24} {count}")
        print(f"коммит прода: {remote or '—'}, локальный: {sha}, "
              f"{'совпадает' if report['commit']['same'] else 'расходится'}")
        if report.get("counts_error"):
            print(f"счётчики: {report['counts_error']}")
    ok = (report["health"]["code"] == 200 and report["counts"]
          and not report.get("counts_error"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
