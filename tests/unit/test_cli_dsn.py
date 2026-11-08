"""Строка подключения скриптов: `scripts/_cli.dsn()` (карта t_cfdc544f).

До правки в `DEFAULT_DSN` лежала заглушка `svoi:***` (три символа вместо пароля),
а `.env` функция не читала. Любой скрипт без переменной `DATABASE_URL` в
окружении падал на аутентификации, хотя в `.env` рабочая строка была.

Решение: один источник (`_cli.dsn`), порядок — окружение → `.env` → локальная
база разработки с паролем контейнера postgres из `docker-compose.yml`.

Правила и требования раздела: docs/reports/99-shared-helpers.md §2.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import _cli  # noqa: E402 — путь к scripts/ добавляется выше

FROM_FILE = "postgresql://svoi:from_env_file@127.0.0.1:5433/svoi"
FROM_OS = "postgresql://svoi:from_os@127.0.0.1:5433/svoi"


@pytest.fixture()
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`.env` с рабочей строкой вместо настоящего файла проекта."""
    path = tmp_path / ".env"
    path.write_text(f"DATABASE_URL={FROM_FILE}\n", encoding="utf-8")
    monkeypatch.setattr(_cli, "ENV_FILE", path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return path


def test_dsn_читает_env_без_apply_env(env_file: Path) -> None:
    """Главный дефект карты: `.env` есть, но `apply_env()` никто не звал."""
    assert _cli.dsn() == FROM_FILE


def test_окружение_приоритетнее_env(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """В cron и контейнере переменная приходит из окружения — файл её не перебивает."""
    monkeypatch.setenv("DATABASE_URL", FROM_OS)
    assert _cli.dsn() == FROM_OS


def test_пустая_переменная_не_считается_значением(env_file: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустая строка в окружении не должна уводить подключение в никуда."""
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert _cli.dsn() == FROM_FILE


def test_без_окружения_и_env_остаётся_локальная_база(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """У cron без `.env` остаётся рабочая строка, а не пустота."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_cli, "ENV_FILE", tmp_path / "нет-такого.env")
    assert _cli.dsn() == _cli.DEFAULT_DSN


def test_умолчание_не_заглушка_и_только_локальная_база() -> None:
    """`*` вместо пароля — регрессия карты; чужой хост — риск писать в боевую базу."""
    dsn = _cli.DEFAULT_DSN
    assert "***" not in dsn, "заглушка вместо пароля: dsn() без .env не подключится"
    assert re.fullmatch(r"postgresql://svoi:[^:@/]+@127\.0\.0\.1:5433/svoi", dsn), dsn


def test_умолчание_совпадает_с_паролем_контейнера() -> None:
    """Пароль умолчания — дефолт postgres из docker-compose, а не отдельный секрет."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    found = re.search(r"POSTGRES_PASSWORD:\s*\$\{POSTGRES_PASSWORD:-([^}]+)\}", compose)
    assert found, "в docker-compose.yml нет умолчания POSTGRES_PASSWORD"
    assert f":{found.group(1)}@" in _cli.DEFAULT_DSN


def test_скрипты_не_держат_своей_строки_подключения() -> None:
    """Свои копии DSN расходились: у `_cli` была заглушка, у соседей — пароль."""
    offenders = []
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        if path.name == "_cli.py":
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"postgresql://svoi:[^@\s]", line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == [], f"строка подключения должна приходить из _cli.dsn(): {offenders}"
