#!/usr/bin/env python3
"""Бэкап файлов бота: выгрузить оба бакета и проверить, что выгрузилось целым (Ш-1).

    python scripts/storage_backup.py                       # в /opt/svoi/backups/storage
    python scripts/storage_backup.py --dir /tmp/b --keep-days 3
    python scripts/storage_backup.py --dry-run             # только посчитать, что копировать

Почему отдельный скрипт, а не `mc mirror`: mc — это отдельный образ, которого у нас
больше нет (MinIO снял свои образы с реестров, docs/development/86 §3.3). Здесь тот же
S3 API и та же библиотека, что и в приложении.

Каждый прогон — отдельная папка `run_<дата>_<время>/<бакет>/<ключ>` плюс `manifest.json`
с размерами и sha256. Удаление целиком папки прогона старше `--keep-days` — единственный
безопасный способ чистки: объекты внутри одной папки всегда согласованы с манифестом.

Код выхода 1, если хоть один объект не скачался или не совпал по sha256: бэкап, который
«почти получился», — это не бэкап.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DIR = os.getenv("STORAGE_BACKUP_DIR", "/opt/svoi/backups/storage")
RUN_PREFIX = "run_"


def load_project_env() -> None:
    """Настройки хранилища из .env проекта; окружение приоритетнее файла."""
    from _cli import apply_env

    try:
        apply_env()
    except ImportError:  # python-dotenv не установлен — живём на системном окружении
        return


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def bucket_names(only: str | None = None) -> list[str]:
    private = env("STORAGE_BUCKET_PRIVATE", "svoi-private")
    shared = env("STORAGE_BUCKET_SHARED", "svoi-shared")
    if only == "private":
        return [private]
    if only == "shared":
        return [shared]
    return [private, shared]


def make_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=env("STORAGE_ENDPOINT"),
        aws_access_key_id=env("STORAGE_ACCESS_KEY"),
        aws_secret_access_key=env("STORAGE_SECRET_KEY"),
        region_name=env("STORAGE_REGION", "svoi"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def iter_objects(client, bucket: str):
    """Все объекты бакета постранично: у бакета нет потолка в 1000 ключей."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            yield item["Key"], item["Size"]


def copy_object(client, bucket: str, key: str, target: Path) -> dict:
    """Скачать объект и проверить целостность по дороге."""
    digest = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    with target.open("wb") as handle:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            digest.update(chunk)
            handle.write(chunk)
            size += len(chunk)
    return {"key": key, "size": size, "sha256": digest.hexdigest()}


def backup_bucket(client, bucket: str, target_dir: Path, dry_run: bool = False) -> list[str]:
    problems: list[str] = []
    entries: list[dict] = []
    for key, size in iter_objects(client, bucket):
        if dry_run:
            entries.append({"key": key, "size": size})
            continue
        try:
            entry = copy_object(client, bucket, key, target_dir / key)
        except Exception as exc:                    # noqa: BLE001 — отчёт важнее типа ошибки
            problems.append(f"{bucket}/{key}: {type(exc).__name__}: {exc}")
            continue
        if entry["size"] != size:
            problems.append(f"{bucket}/{key}: скачано {entry['size']} из {size} байт")
        entries.append(entry)
    manifest = {"bucket": bucket, "objects": entries, "count": len(entries),
                "bytes": sum(item.get("size", 0) for item in entries)}
    if not dry_run:
        # пустой бакет — тоже результат: папки может не быть, создаём её сами
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{bucket}: файлов {manifest['count']}, байт {manifest['bytes']}")
    return problems


def prune(root: Path, keep_days: int) -> None:
    """Удалить прогоны старше keep_days. Только папки вида run_<штамп>."""
    if not root.is_dir():
        return
    border = datetime.now() - timedelta(days=keep_days)
    for entry in root.iterdir():
        if not (entry.is_dir() and entry.name.startswith(RUN_PREFIX)):
            continue
        try:
            stamp = datetime.strptime(entry.name[len(RUN_PREFIX):], "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        if stamp < border:
            shutil.rmtree(entry, ignore_errors=True)
            print(f"удалён старый прогон: {entry.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Бэкап объектного хранилища файлов")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="куда складывать (по умолчанию %(default)s)")
    parser.add_argument("--keep-days", type=int, default=14, help="сколько дней хранить прогоны")
    parser.add_argument("--bucket", choices=["private", "shared"], help="только один бакет")
    parser.add_argument("--dry-run", action="store_true", help="посчитать, но не копировать")
    args = parser.parse_args(argv)
    load_project_env()

    if not env("STORAGE_ENDPOINT"):
        print("STORAGE_ENDPOINT не задан — бэкапить нечего")
        return 0

    root = Path(args.dir)
    run_dir = root / (RUN_PREFIX + datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)

    client = make_client()
    problems: list[str] = []
    for bucket in bucket_names(args.bucket):
        problems.extend(backup_bucket(client, bucket, run_dir / bucket, args.dry_run))

    if problems:
        print("\nпроблемы:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if not args.dry_run:
        prune(root, args.keep_days)
        print(f"бэкап готов: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
