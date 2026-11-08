"""Инвариант «локальный запуск не читает прод-токен» (карта t_4aa28fe9).

Проверка в `scripts/check_invariants.py` статическая: она должна ловить возврат к
старому поведению (читать `.env` и брать `TELEGRAM_TOKEN` оттуда) в тот же коммит,
а не через следующий инцидент с 409 Conflict.

Обоснование: docs/infrastructure/08 §1a (инвариант «локальный запуск не читает прод-токен»),
вопрос V-025.
"""

from __future__ import annotations

import ast
import shutil

from scripts import check_invariants

PROJECT = check_invariants.PROJECT
TEMPLATES = PROJECT / "deploy" / "launchd"


def test_репозиторий_проходит_проверку_сред():
    assert check_invariants.check_dev_env_isolation() == 0


def test_прод_токен_вне_прод_ветки_ловится():
    tree = ast.parse('TOKEN = "TELEGRAM_TOKEN"\n'
                     'import os\n'
                     'os.environ["TELEGRAM_TOKEN"]\n'
                     "def prod_env(values):\n"
                     '    return values["TELEGRAM_TOKEN"]\n'
                     "def dev_env(values):\n"
                     '    return values["TELEGRAM_TOKEN"]\n')
    # константа-имя токена чтением не считается, обращение к значению — считается
    functions = [function or "модуль" for function, _ in check_invariants._function_stack(tree)]
    assert functions == ["модуль", "prod_env", "dev_env"]
    # вне prod-ветки прод-токен запрещён — нарушители видны по имени функции
    offenders = [name for name in functions
                 if name.split(".")[-1] not in check_invariants.PROD_ALLOWED]
    assert offenders == ["модуль", "dev_env"]


def test_чтение_основного_env_ловится():
    tree = ast.parse('from _cli import ENV_FILE\n'
                     'PATH = ENV_FILE\n'
                     'OTHER = ".env"\n'
                     'PROD = "/opt/svoi/.env"\n'
                     'DEV = ".env.dev"\n')
    assert len(check_invariants._main_env_reads(tree)) == 2      # ENV_FILE и ".env"


def test_боевой_путь_и_dev_файл_не_считаются_нарушением():
    tree = ast.parse('PROD = "/opt/svoi/.env"\nDEV = ".env.dev"\n')
    assert check_invariants._main_env_reads(tree) == []


def test_шаблоны_агентов_запускают_dev(tmp_path, monkeypatch):
    """Агент на маке обязан поднимать сервис с `--env dev`, иначе он возьмёт прод."""
    copied = tmp_path / "deploy" / "launchd"
    copied.mkdir(parents=True)
    for template in TEMPLATES.glob("*.plist.template"):
        shutil.copy(template, copied / template.name)
    (tmp_path / ".env.dev.example").write_text("DEV_BOT_TOKEN=\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")   # без .env.dev
    (tmp_path / "scripts").mkdir()
    shutil.copy(PROJECT / "scripts" / "run_service.py", tmp_path / "scripts" / "run_service.py")

    monkeypatch.setattr(check_invariants, "PROJECT", tmp_path)
    # шаблоны в порядке (@ENV@ рендерится в dev), проблема одна: .env.dev не в .gitignore
    assert check_invariants.check_dev_env_isolation() == 1


def test_агент_с_прод_средой_это_провал(tmp_path, monkeypatch, capsys):
    copied = tmp_path / "deploy" / "launchd"
    copied.mkdir(parents=True)
    source = (TEMPLATES / "com.svoi.gateway.plist.template").read_text(encoding="utf-8")
    (copied / "com.svoi.gateway.plist.template").write_text(
        source.replace("@ENV@", "prod") if "@ENV@" in source
        else source.replace("<string>dev</string>", "<string>prod</string>"),
        encoding="utf-8")
    (tmp_path / ".env.dev.example").write_text("DEV_BOT_TOKEN=\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n.env.dev\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    shutil.copy(PROJECT / "scripts" / "run_service.py", tmp_path / "scripts" / "run_service.py")

    monkeypatch.setattr(check_invariants, "PROJECT", tmp_path)
    assert check_invariants.check_dev_env_isolation() == 1
    assert "не в dev" in capsys.readouterr().out


def test_dev_файл_игнорируется_git():
    gitignore = (PROJECT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.dev" in gitignore, "dev-токен не должен уезжать в git"
    assert (PROJECT / ".env.dev.example").is_file(), "нужен шаблон dev-окружения"
