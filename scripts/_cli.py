#!/usr/bin/env python3
"""Общий вход скриптов: корень проекта, `.env`, пути, аргументы (R-8).

Зачем один модуль. До него каждый скрипт сам решал три одинаковые задачи, и решал
по-своему: 15 файлов звали `load_dotenv` (часть внутри функции, часть с
`override=False`), ещё несколько читали `.env` руками, 38 — `os.getenv` без
загрузки вообще, а `sys.argv` разбирали поиском подстроки (`"--apply" in sys.argv`),
из-за чего `--apply-что-нибудь` тоже считалось записью. Здесь один ответ:

    from _cli import ROOT, TOOL_API, apply_env, dsn, parser, add_write_mode, writes

    apply_env()                  # переменные из .env, окружение приоритетнее файла
    args = parser(__doc__)       # argparse с общими флагами
    add_write_mode(args)         # --dry-run / --apply
    if writes(args): ...

Правила, которые держит модуль:
  * `.env` читает только python-dotenv: наивный `split("=")` на строке
    `TZ_OFFSET_HOURS=4    # Самара, UTC+4` даёт `int()`-падение на старте (Ш-29);
  * уже выставленное окружение не перебивается — так скрипт в контейнере и в cron
    ведёт себя так же, как локально;
  * по умолчанию скрипт ничего не пишет: запись — явный `--apply`;
  * корень проекта — от файла, а не от текущего каталога: скрипт зовут и из cron.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT                      # прежнее имя того же каталога
ENV_FILE = ROOT / ".env"
TOOL_API = ROOT / "services" / "tool-api"
# Строка подключения по умолчанию — только локальная база разработки (порт 5433,
# как в docker-compose). Пароль — дефолт контейнера postgres
# (`POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-svoi_dev_password}` в docker-compose.yml),
# не боевой: на сервере DATABASE_URL приходит из окружения, и эта ветка не работает.
# Заглушку вида `svoi:***` держать нельзя: `dsn()` без переменной и без `.env`
# падал бы на аутентификации (t_cfdc544f).
DEFAULT_DSN = "postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi"
DEFAULT_API_URL = "http://127.0.0.1:8000"


# --- окружение -----------------------------------------------------------------

def read_env(env_file: Path | None = None) -> dict[str, str]:
    """Переменные из `.env` так, как их прочитал бы shell (инлайн-комментарии режет dotenv)."""
    path = Path(env_file) if env_file is not None else ENV_FILE
    if not path.is_file():
        return {}
    from dotenv import dotenv_values

    values = dotenv_values(path, encoding="utf-8", interpolate=False)
    return {key: (value or "") for key, value in values.items() if key}


def apply_env(env_file: Path | None = None, override: bool = False) -> dict[str, str]:
    """Положить `.env` в `os.environ` и вернуть то, что прочитано.

    По умолчанию уже заданные переменные не трогаем: файл — запасной источник,
    а не приказ (так же вёл себя `load_dotenv`).
    """
    values = read_env(env_file)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def build_env(base: dict[str, str] | None = None,
              env_file: Path | None = None) -> dict[str, str]:
    """Окружение процесса сервиса: `.env` поверх системного, без `PYTHONPATH`.

    `PYTHONPATH` убираем намеренно: пакеты проекта должны приходить из venv,
    а не из окружения агента (иначе подменяются глобальными).
    """
    env = dict(os.environ if base is None else base)
    env.update(read_env(env_file))
    env.pop("PYTHONPATH", None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def env(name: str, default: str = "") -> str:
    """Значение переменной без пробелов по краям: в `.env` они попадают из копипаста."""
    return os.getenv(name, default).strip()


def dsn(name: str = "DATABASE_URL") -> str:
    """Строка подключения к Postgres: одна на все скрипты.

    Источники по старшинству: переменная окружения → значение из `.env` →
    `DEFAULT_DSN` (локальная база разработки). `.env` читается здесь же, поэтому
    `dsn()` даёт рабочую строку и без `apply_env()`: в cron и в контейнере
    переменная уже в окружении, у нового разработчика она есть только в файле.
    Пустая или пробельная переменная значением не считается.
    """
    value = env(name)
    if not value:
        value = (read_env().get(name) or "").strip()
    return value or DEFAULT_DSN


def api_url() -> str:
    """Адрес tool-api без хвостового слэша."""
    return env("TOOL_API_URL", DEFAULT_API_URL).rstrip("/")


# --- время ---------------------------------------------------------------------

def now_local(*, naive: bool = False) -> datetime:
    """Текущее время Самары: один сдвиг `TZ_OFFSET_HOURS` (по умолчанию UTC+4).

    Раньше каждая из четырёх копий (`feedback_digest`, `feedback_queue`,
    `plan_reminders`, `send_notifications`) считала время сама, и копии
    разошлись: `feedback_queue` отдавала naive-значение, остальные — aware.
    Первое сравнение naive с aware дало бы `TypeError` (класс INT-012).
    Теперь правило сдвига живёт здесь, а не в четырёх местах.

    `naive=True` — без `tzinfo`: так дата попадает в файл вопросов
    `docs/reports/74-questions.md`, который читает человек. Это поведение
    прежнего `feedback_queue.now_local`, включённое явным аргументом, а не
    второй копией функции. Доли секунды обрезаются в обоих режимах: даты в
    ключах дедупликации и в файлах от этого не меняются.
    """
    offset = float(env("TZ_OFFSET_HOURS", "4"))
    moment = datetime.now(timezone(timedelta(hours=offset))).replace(microsecond=0)
    return moment.replace(tzinfo=None) if naive else moment


# --- пути ----------------------------------------------------------------------

def add_tool_api_path() -> None:
    """Каталог tool-api и корень проекта в `sys.path`, чтобы `import app...` работал.

    Нужно до первого импорта `app`: при запуске файлом в `sys.path` только `scripts/`.
    Корень нужен рядом: код tool-api берёт общие форматы из `services.shared` (R-6).
    """
    for path in (str(TOOL_API), str(ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)


# --- аргументы -----------------------------------------------------------------

def parser(description: str, epilog: str | None = None) -> argparse.ArgumentParser:
    """Разбор аргументов по общим правилам: `--help` показывает докстроку как есть."""
    return argparse.ArgumentParser(
        description=description, epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter)


def add_write_mode(ap: argparse.ArgumentParser, *,
                   dry_help: str = "показать, что будет сделано (по умолчанию)",
                   apply_help: str = "выполнить запись") -> None:
    """Флаги записи: молча ничего не пишем, запись — явный `--apply`."""
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help=dry_help)
    group.add_argument("--apply", action="store_true", help=apply_help)


def writes(args: argparse.Namespace) -> bool:
    """Пишем ли изменения: только по явному `--apply`."""
    return bool(getattr(args, "apply", False))


def add_json_flag(ap: argparse.ArgumentParser,
                  help_text: str = "вывести результат в JSON") -> None:
    ap.add_argument("--json", action="store_true", help=help_text)


def add_allow_dev_write(ap: argparse.ArgumentParser) -> None:
    """Разрешение писать в рабочую базу (см. scripts/db_guard.py)."""
    ap.add_argument("--allow-dev-write", action="store_true",
                    help="разрешить запись в рабочую базу (иначе только тестовая)")
