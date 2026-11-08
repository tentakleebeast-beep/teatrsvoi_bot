#!/usr/bin/env python3
"""Объектное хранилище файлов бота: поднять Garage и настроить его (Ш-1, docs/development/86).

    python scripts/storage_up.py            # идемпотентно: поднять, layout, ключ, бакеты
    python scripts/storage_up.py --status   # только показать состояние, ничего не менять

Скрипт делает ровно то, что руками делал бы человек, и не больше:
  1. если в .env нет GARAGE_RPC_SECRET / STORAGE_ACCESS_KEY / STORAGE_SECRET_KEY —
     генерирует их и дописывает в .env (файл в .gitignore); адреса, переданные
     окружением (GARAGE_CONTAINER, STORAGE_PUBLIC_ENDPOINT, STORAGE_CORS_ORIGINS),
     тоже дописывает, если их там ещё нет;
  2. поднимает контейнер из docker-compose.yml (имя — GARAGE_CONTAINER) и ждёт готовности;
  3. назначает узлу роль (layout), если она ещё не назначена;
  4. импортирует S3-ключ бота и выдаёт ему права на бакеты `svoi-private` / `svoi-shared`.

Почему Garage, а не MinIO: MinIO удалил образы с Docker Hub (сентябрь 2026), а quay.io
закрыл анонимные pull'ы; комьюнити-версия осталась только исходниками. Подробности —
docs/development/86 §3.3.

Проверка фактом — рядом: `python scripts/storage_smoke.py`.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from _cli import ENV_FILE
from _cli import read_env as _read_env

PROJECT = Path(__file__).resolve().parent.parent
BINARY = "/garage"

# Ёмкость узла в layout. Реальный объём задаёт диск; число нужно Garage для раскладки.
CAPACITY = os.getenv("GARAGE_CAPACITY", "40G")
BUCKETS = ("svoi-private", "svoi-shared")

# Форматы Garage: ключ = «GK» + 12 байт hex, секрет = 32 байта hex.
KEY_ID_PREFIX = "GK"
NODE_LINE = re.compile(r"^(?P<id>[0-9a-f]{16})\s+\S+\s+\S+\s", re.MULTILINE)
VERSION_LINE = re.compile(r"cluster layout version:\s*(?P<n>\d+)")


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def container_name(env: dict[str, str]) -> str:
    """Имя контейнера: окружение → .env → локальное умолчание из docker-compose.yml."""
    return (os.getenv("GARAGE_CONTAINER") or env.get("GARAGE_CONTAINER")
            or "svoi-garage-dev")


CONTAINER = container_name({})


def garage(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Команда внутри контейнера: docker exec svoi-garage /garage ..."""
    return run(["docker", "exec", CONTAINER, BINARY, *args], check=check)


# ------------------------------------------------------------------ .env
def read_env() -> dict[str, str]:
    """Переменные из .env: инлайн-комментарии режет python-dotenv, а не `split("=")`."""
    return _read_env(ENV_FILE)


def append_env(pairs: dict[str, str]) -> None:
    """Дописать в .env только отсутствующие ключи, сохранив остальное."""
    if not pairs:
        return
    block = ["", "# объектное хранилище файлов (Ш-1, scripts/storage_up.py)"]
    block += [f"{key}={value}" for key, value in pairs.items()]
    with ENV_FILE.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(block) + "\n")
    os.chmod(ENV_FILE, 0o600)


def ensure_secrets() -> dict[str, str]:
    """Вернуть настройки хранилища, догенерировав недостающее в .env.

    Адреса, которых скрипт не может знать сам (имя контейнера на проде, публичный
    адрес для браузера, origin Mini App), приходят окружением от выката
    (`deploy/server-deploy.sh storage`) и дописываются в .env рядом с ключами.
    """
    env = read_env()
    wanted = {
        "GARAGE_RPC_SECRET": secrets.token_hex(32),
        "STORAGE_ACCESS_KEY": KEY_ID_PREFIX + secrets.token_hex(12),
        "STORAGE_SECRET_KEY": secrets.token_hex(32),
        "STORAGE_ENDPOINT": "http://127.0.0.1:3900",
        "STORAGE_REGION": "svoi",
        "STORAGE_BUCKET_PRIVATE": BUCKETS[0],
        "STORAGE_BUCKET_SHARED": BUCKETS[1],
    }
    for key in ("GARAGE_CONTAINER", "STORAGE_PUBLIC_ENDPOINT", "STORAGE_CORS_ORIGINS"):
        value = os.getenv(key, "").strip()
        if value:
            wanted[key] = value
    missing = {key: value for key, value in wanted.items() if not env.get(key)}
    if missing:
        append_env(missing)
        env.update(missing)
        print(f"дописано в .env: {', '.join(sorted(missing))}")
    return env


# ------------------------------------------------------------------ docker
def container_running() -> bool:
    return run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER]).stdout.strip() == "true"


def compose_up() -> None:
    """Поднять контейнер, если он ещё не работает: повторный запуск скрипта не должен падать.

    Каталог проекта задан явно: compose берёт из него `.env` при подстановке
    `${GARAGE_CONTAINER}` в docker-compose.yml, и от текущего каталога это не зависит.
    """
    if container_running():
        return
    result = subprocess.run(["docker", "compose", "up", "-d", "garage"], cwd=PROJECT,
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"docker compose up garage: {result.stderr.strip() or result.stdout.strip()}")


def wait_ready(timeout: float = 60.0) -> str:
    """Дождаться, пока узел покажется в статусе, и вернуть его ID."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        status = garage("status")
        last = (status.stdout or "") + (status.stderr or "")
        match = NODE_LINE.search(last)
        if match:
            return match.group("id")
        time.sleep(2)
    raise SystemExit(f"узел не поднялся за {timeout:.0f} c; последний вывод:\n{last}")


def has_role(node_id: str) -> bool:
    return "NO ROLE ASSIGNED" not in (garage("status").stdout or "")


def layout_version() -> int:
    text = (garage("layout", "show").stdout or "") + (garage("layout", "history").stdout or "")
    match = VERSION_LINE.search(text)
    return int(match.group("n")) if match else 0


def assign_layout(node_id: str) -> None:
    staged = garage("layout", "assign", "-z", "dc1", "-c", CAPACITY, node_id)
    if staged.returncode != 0:
        raise SystemExit(f"layout assign: {staged.stderr.strip()}")
    applied = garage("layout", "apply", "--version", str(layout_version() + 1))
    if applied.returncode != 0:
        raise SystemExit(f"layout apply: {applied.stderr.strip()}")
    print(f"роль узла назначена: {node_id} (зона dc1, {CAPACITY})")


# ------------------------------------------------------------------ s3
def ensure_key(access_key: str, secret_key: str) -> None:
    if garage("key", "info", access_key).returncode == 0:
        return
    imported = garage("key", "import", access_key, secret_key, "--yes", "-n", "svoi-bot")
    if imported.returncode != 0:
        raise SystemExit(f"key import: {imported.stderr.strip()}")
    print(f"ключ создан: {access_key}")


def ensure_buckets(access_key: str) -> None:
    for bucket in BUCKETS:
        garage("bucket", "create", bucket)          # «уже есть» — не ошибка, цель идемпотентна
        allowed = garage("bucket", "allow", bucket, "--read", "--write", "--owner",
                         "--key", access_key)
        if allowed.returncode != 0:
            raise SystemExit(f"bucket allow {bucket}: {allowed.stderr.strip()}")
    print(f"бакеты готовы: {', '.join(BUCKETS)}")


def ensure_cors(env: dict[str, str]) -> None:
    """Разрешить браузеру Mini App класть файлы прямо в хранилище.

    Без CORS браузер даже не покажет ответ на presigned PUT: страница живёт на одном
    домене, хранилище — на другом. Ошибка здесь не валит настройку: файл из бота работает
    и без CORS, а Mini App скажет о проблеме прямо в консоли.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("boto3 не установлен — CORS не настроен (Mini App не сможет грузить файлы)")
        return

    endpoint = env.get("STORAGE_ENDPOINT", "").strip()
    if not endpoint:
        return
    origins = [item.strip() for item in env.get(
        "STORAGE_CORS_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000").split(",") if item.strip()]
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=env["STORAGE_ACCESS_KEY"],
        aws_secret_access_key=env["STORAGE_SECRET_KEY"],
        region_name=env.get("STORAGE_REGION", "svoi"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
    # По правилу на каждый origin. Одно правило со списком Garage отдаёт одной строкой
    # «origin1, origin2» — а браузер такой Access-Control-Allow-Origin считает невалидным.
    rules = {"CORSRules": [{
        "AllowedOrigins": [origin],
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    } for origin in (origins or ["*"])]}
    for bucket in BUCKETS:
        try:
            client.put_bucket_cors(Bucket=bucket, CORSConfiguration=rules)
        except Exception as exc:                  # noqa: BLE001 — хранилище может не уметь CORS
            print(f"CORS для {bucket} не настроен: {type(exc).__name__}: {exc}")
            return
    print(f"CORS включён для {', '.join(BUCKETS)}: {', '.join(origins)}")


def buckets_present() -> list[str]:
    text = garage("bucket", "list").stdout or ""
    return [name for name in BUCKETS if name in text]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Garage: поднять и настроить хранилище файлов")
    parser.add_argument("--status", action="store_true", help="только показать состояние")
    args = parser.parse_args(argv)

    env = read_env()
    # Имя контейнера из .env главнее умолчания: на проде он `svoi-garage`, локально `-dev`.
    global CONTAINER
    CONTAINER = container_name(env)
    if args.status:
        status = garage("status")
        print(status.stdout.strip() or status.stderr.strip())
        print(f"\nбакеты: {', '.join(buckets_present()) or 'нет'}")
        print(f"endpoint: {env.get('STORAGE_ENDPOINT', '— (нет в .env)')}")
        return 0

    env = ensure_secrets()
    compose_up()
    node_id = wait_ready()
    if not has_role(node_id):
        assign_layout(node_id)
    ensure_key(env["STORAGE_ACCESS_KEY"], env["STORAGE_SECRET_KEY"])
    ensure_buckets(env["STORAGE_ACCESS_KEY"])
    ensure_cors(env)
    print(f"готово: {env['STORAGE_ENDPOINT']}, узел {node_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
