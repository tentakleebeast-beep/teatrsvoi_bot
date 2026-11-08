#!/usr/bin/env python3
"""
Запуск одного сервиса SVOI с окружением выбранной среды: `dev` или `prod`.

Зачем: launchd и cron не читают `.env`, а рецепт `sh -c 'set -a; . ./.env; ...'`
из тика планировщика блокируется политикой одобрений (Ш-29). Скрипт делает то же
самое, но без шелла: читает файл среды, режет значение по инлайн-комментарию (` #`),
убирает `PYTHONPATH` (иначе пакеты подменяются глобальными) и подменяет процесс
через `os.execvpe` — PID остаётся тем же, launchd видит живой процесс.

Две среды — две разные вещи (docs/infrastructure/08 §1a):

* `dev` (по умолчанию, всегда для локального запуска) — файл `.env.dev`, бот из
  `DEV_BOT_TOKEN`, БД только локальная. Прод-токен не читается вообще: основной
  `.env` в dev-режиме не открывается, а унаследованный `TELEGRAM_TOKEN`
  перекрывается dev-ботом. Нет `DEV_BOT_TOKEN` — запуск не начинается («среда
  разработки не настроена»), а не «тихо берёт прод-токен»: 30.10.2026 такой тихий
  переход стоил 4 часов молчания бота и 1457 ошибок 409 Conflict.
* `prod` — боевой конфиг сервиса `/opt/svoi/.env` (`SVOI_PROD_ENV_FILE`
  переопределяет путь), бот из `TELEGRAM_TOKEN`. Файл `deploy/svoi.env` — это
  настройки выката (`SVOI_*`), токенов в нём нет и быть не должно.

  python scripts/run_service.py tool-api                 # dev, по умолчанию
  python scripts/run_service.py gateway
  python scripts/run_service.py gateway --env prod       # только на сервере
  python scripts/run_service.py tool-api --port 8000 --check   # показать, не запускать
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from _cli import read_env

PROJECT = Path(__file__).resolve().parent.parent

DEV = "dev"
PROD = "prod"
ENVIRONMENTS = (DEV, PROD)
DEFAULT_ENVIRONMENT = DEV

# Файлы сред. Основной `.env` здесь не участвует намеренно: он принадлежит
# локальному боевому контуру, и один случайный запуск из него — это перехваченный
# getUpdates у прода (docs/infrastructure/08 §1a, инцидент 30.10.2026).
DEV_ENV_NAME = ".env.dev"
DEV_ENV_FILE = PROJECT / DEV_ENV_NAME
# Боевой сервисный конфиг на сервере (docs/infrastructure/75 §6.3: юниты systemd
# берут его же как EnvironmentFile). Путь переопределяется SVOI_PROD_ENV_FILE.
PROD_ENV_FILE = Path("/opt/svoi/.env")
PROD_ENV_VAR = "SVOI_PROD_ENV_FILE"
DEV_ENV_VAR = "SVOI_DEV_ENV_FILE"

DEFAULT_PORT = 8000

SERVICES = ("tool-api", "gateway")

# Имена переменных, которые скрипт сам пишет в окружение сервиса.
ENV_MARKER = "ENV"
BOT_TOKEN_VAR = "TELEGRAM_TOKEN"
DEV_BOT_TOKEN_VAR = "DEV_BOT_TOKEN"
DATABASE_URL_VAR = "DATABASE_URL"

# Хосты, на которых dev-окружение считается локальным. Всё остальное — повод
# отказаться: dev-прогон не должен ни читать, ни писать боевые данные.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})

DEV_NOT_CONFIGURED = (
    "среда разработки не настроена: нет "
    f"{DEV_BOT_TOKEN_VAR} в {DEV_ENV_NAME}.\n"
    "  Прод-токен здесь не используется намеренно: локальный поллер на боевом\n"
    "  токене перехватывает getUpdates у прода (409 Conflict).\n"
    f"  Что сделать: скопируйте {DEV_ENV_NAME}.example в {DEV_ENV_NAME} и впишите\n"
    f"  {DEV_BOT_TOKEN_VAR} отдельного бота из BotFather (docs/infrastructure/08 §1a)."
)

# launchd пишет stdout/stderr в файлы без предела; 5 МБ на файл — уже много для
# дневного объёма сервиса, а один прошлый файл (.1) сохраняет хвост разбора.
LOG_MAX_BYTES = 5 * 1024 * 1024


class DevEnvNotConfigured(Exception):
    """Отказ запускать dev-сервис без dev-бота. Текст — человеку, код выхода 2."""

    code = 2

    def __init__(self, message: str = DEV_NOT_CONFIGURED) -> None:
        super().__init__(message)


def log_dir() -> Path:
    """Куда launchd пишет логи сервисов (SVOI_LOG_DIR переопределяет)."""
    override = os.getenv("SVOI_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Logs" / "svoi"


def rotate_logs(service: str, logs: Path | None = None,
                max_bytes: int = LOG_MAX_BYTES) -> list[str]:
    """Сдвинуть слишком большой лог сервиса в .1 перед запуском (Ш-38).

    Ротация на старте процесса: файл никто не держит открытым, поэтому
    переименование безопасно, а прежний хвост остаётся в .1. Ошибки файловой
    системы не мешают запуску сервиса — это уборка, а не условие работы.
    """
    directory = logs or log_dir()
    actions: list[str] = []
    for path in sorted(directory.glob(f"{service}*.log")):
        try:
            if path.stat().st_size <= max_bytes:
                continue
            backup = path.with_suffix(path.suffix + ".1")
            backup.unlink(missing_ok=True)
            path.rename(backup)
            actions.append(f"{path.name} -> {backup.name}")
        except OSError:
            continue
    return actions


def load_env(env_file: Path) -> dict[str, str]:
    """Переменные из файла среды так, как их прочитал бы shell.

    Инлайн-комментарии режет python-dotenv внутри `_cli.read_env`: наивный
    `split("=")` на строке `TZ_OFFSET_HOURS=4    # Самара, UTC+4` дал бы
    `int()`-падение на старте (Ш-29).
    """
    return read_env(env_file)


def env_file_for(environment: str) -> Path:
    """Файл среды: `.env.dev` для dev, боевой конфиг сервиса для prod."""
    if environment == DEV:
        override = os.getenv(DEV_ENV_VAR, "").strip()
        return Path(override).expanduser() if override else DEV_ENV_FILE
    if environment == PROD:
        override = os.getenv(PROD_ENV_VAR, "").strip()
        return Path(override).expanduser() if override else PROD_ENV_FILE
    raise ValueError(f"неизвестная среда: {environment}")


def build_env(base: dict[str, str] | None = None,
              env_file: Path | None = None,
              environment: str = DEFAULT_ENVIRONMENT) -> dict[str, str]:
    """Окружение процесса сервиса: файл среды поверх системного, без `PYTHONPATH`.

    Правки среды (dev-бот вместо прод-бота) сюда не входят: их добавляет
    `service_env`, потому что guard — часть запуска, а не чтения файла.
    """
    env = dict(os.environ if base is None else base)
    env.update(load_env(env_file if env_file is not None else env_file_for(environment)))
    env.pop("PYTHONPATH", None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def dev_env(values: dict[str, str]) -> dict[str, str]:
    """Правки dev-окружения: dev-бот вместо прод-бота и признак среды.

    Возвращает только то, что меняется. Отказ вместо подстановки прод-токена —
    главная защита карты t_4aa28fe9.
    """
    token = (values.get(DEV_BOT_TOKEN_VAR) or "").strip()
    if not token:
        raise DevEnvNotConfigured()
    return {BOT_TOKEN_VAR: token, ENV_MARKER: DEV}


def prod_env(values: dict[str, str]) -> dict[str, str]:
    """Правки боевого окружения. Прод-токен читается только здесь."""
    token = (values.get(BOT_TOKEN_VAR) or "").strip()
    if not token:
        raise SystemExit(
            f"боевой конфиг без {BOT_TOKEN_VAR}: {PROD_ENV_FILE} "
            f"(путь переопределяется {PROD_ENV_VAR}). Запуск на проде — из юнита systemd."
        )
    return {BOT_TOKEN_VAR: token, ENV_MARKER: PROD}


def db_target(value: str) -> tuple[str, str]:
    """(хост, база) из строки подключения — для guard-а. Пароль не нужен."""
    parts = urlsplit((value or "").strip())
    if not parts.scheme:
        return "", ""
    return (parts.hostname or "").lower(), parts.path.lstrip("/")


def db_label(value: str) -> str:
    """Куда смотрит БД, без секретов: «127.0.0.1:5433/svoi» (для лога)."""
    parts = urlsplit((value or "").strip())
    host, name = db_target(value)
    if not host:
        return "локальная по умолчанию"
    port = parts.port or 5432
    return f"{host}:{port}/{name or 'svoi'}"


def assert_local_db(value: str, environment: str = DEV) -> None:
    """Dev-окружение смотрит только в локальную БД.

    Прогон на боевой базе — это не «почти прод», это запись в живые данные театра.
    Пустое значение допустимо: `scripts/_cli.dsn` подставит локальную базу (5433).
    """
    if environment != DEV or not (value or "").strip():
        return
    host, name = db_target(value)
    if host in LOCAL_HOSTS:
        return
    raise SystemExit(
        f"среда разработки смотрит на не-локальную БД: {host or '?'}/{name or '?'}. "
        f"В {DEV_ENV_NAME} должен быть локальный {DATABASE_URL_VAR} "
        "(127.0.0.1:5433/svoi), боевые данные из dev-контура недоступны."
    )


def mask_token(token: str) -> str:
    """Маска токена для лога: видно только хвост, по которому узнают бота."""
    token = (token or "").strip()
    if not token:
        return "нет"
    return f"***{token[-4:]}" if len(token) >= 8 else "***"


def describe(environment: str, env: dict[str, str], env_file: Path) -> str:
    """Одна строка в лог: какая среда, какой бот (маска), какая БД."""
    return (
        f"среда={environment} файл={env_file} "
        f"бот={mask_token(env.get(BOT_TOKEN_VAR, ''))} "
        f"бд={db_label(env.get(DATABASE_URL_VAR, ''))} "
        f"tool-api={env.get('TOOL_API_URL', 'http://127.0.0.1:8000')}"
    )


def service_env(service: str, environment: str = DEFAULT_ENVIRONMENT,
                base: dict[str, str] | None = None,
                env_file: Path | None = None) -> dict[str, str]:
    """Собрать окружение сервиса и проверить guard-ы среды.

    dev: обязателен `DEV_BOT_TOKEN`, БД должна быть локальной, прод-токен
    вытесняется dev-ботом. prod: обязателен `TELEGRAM_TOKEN` в боевом конфиге.
    """
    if service not in SERVICES:
        raise ValueError(f"неизвестный сервис: {service}")
    path = env_file if env_file is not None else env_file_for(environment)
    values = load_env(path)
    env = build_env(base, path, environment)
    env.update(dev_env(values) if environment == DEV else prod_env(values))
    assert_local_db(env.get(DATABASE_URL_VAR, ""), environment)
    return env


def command(service: str, python: str | None = None, port: int = DEFAULT_PORT) -> list[str]:
    """Команда запуска сервиса без шелла — та же, что в systemd-юнитах."""
    if service not in SERVICES:
        raise ValueError(f"неизвестный сервис: {service}")
    interpreter = python or sys.executable
    if service == "tool-api":
        return [
            interpreter, "-m", "uvicorn", "app.main:app",
            "--app-dir", "services/tool-api",
            "--host", "127.0.0.1", "--port", str(port),
        ]
    return [interpreter, str(PROJECT / "services" / "tg-gateway" / "app" / "main.py")]


def run(service: str, port: int = DEFAULT_PORT, check: bool = False,
        environment: str = DEFAULT_ENVIRONMENT,
        env_file: Path | None = None) -> int:
    os.chdir(PROJECT)
    path = env_file if env_file is not None else env_file_for(environment)
    env = service_env(service, environment, env_file=path)
    argv = command(service, port=port)
    if check:
        print(json.dumps(
            {"service": service, "environment": environment, "env_file": str(path),
             "bot": mask_token(env.get(BOT_TOKEN_VAR, "")),
             "db": db_target(env.get(DATABASE_URL_VAR, ""))[1] or "svoi",
             "argv": argv, "env_keys": len(env),
             "pythonpath": env.get("PYTHONPATH")},
            ensure_ascii=False, indent=2,
        ))
        return 0
    # Строка «какой бот и какая БД» — до exec: в логе launchd остаётся след,
    # по которому видно, dev это был или прод (карта t_4aa28fe9).
    print(f"run_service: {describe(environment, env, path)}", flush=True)
    rotate_logs(service)
    os.execvpe(argv[0], argv, env)
    return 1  # недостижимо: execvpe не возвращается


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Запуск сервиса SVOI с окружением выбранной среды")
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument("--env", dest="environment", choices=ENVIRONMENTS,
                        default=DEFAULT_ENVIRONMENT,
                        help=f"среда: {DEV} (локально, по умолчанию) или {PROD} (сервер)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--check", action="store_true", help="напечатать команду и выйти")
    args = parser.parse_args(argv)
    try:
        return run(args.service, port=args.port, check=args.check,
                   environment=args.environment)
    except DevEnvNotConfigured as exc:
        print(exc, file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
