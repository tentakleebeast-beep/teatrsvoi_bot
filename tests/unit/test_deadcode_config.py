"""Конфиг vulture/deadcode: обработчики не шумят, новый мёртвый код виден (docs/reports/84).

Обоснование (карта t_b9c9674c): без конфига оба инструмента дают 289 сообщений на
репозитории, и почти все — про функции, которые регистрирует декоратор
(`@router.message`, `@app.get`). Шум съедает сигнал, поэтому проверяем три вещи:
  1. перечисленные в pyproject декораторы действительно покрывают все обработчики
     репозитория — новый `@router.something` не должен однажды всплыть шумом;
  2. объявление с таким декоратором отсеивается, а новая мёртвая функция — нет;
  3. на текущем дереве проверка зелёная (известный мёртвый код — в базовой линии).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

from scripts.check_deadcode import (
    BASELINE,
    PYPROJECT,
    ROOT,
    Finding,
    decorators_of,
    is_registered_handler,
    load_config,
    read_baseline,
)

PROJECT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT / ".github" / "workflows" / "tests.yml"
SCRIPT = PROJECT / "scripts" / "check_deadcode.py"

# Декораторы, которые ничего не регистрируют: `@dataclass` собирает класс, `@functools.cache`
# кэширует, но функцию всё равно зовут по имени. Их покрывать не нужно.
НЕ_РЕГИСТРИРУЮТ = {"@dataclass", "@functools.cache"}


def декораторы_репозитория() -> set[str]:
    """Все имена декораторов, реально встречающихся в боевом коде."""
    found: set[str] = set()
    for папка in ("services", "scripts", "deploy"):
        for файл in (PROJECT / папка).rglob("*.py"):
            for строка in файл.read_text(encoding="utf-8").splitlines():
                совпало = re.match(r"^\s*(@[A-Za-z_][A-Za-z0-9_.]*)", строка)
                if совпало:
                    found.add(совпало.group(1))
    return found


def test_конфиг_покрывает_все_регистрирующие_декораторы():
    """Новый обработчик не должен стать шумом только потому, что его декоратор забыли."""
    _, _, декораторы, _ = load_config()
    непокрытые = sorted(
        имя
        for имя in декораторы_репозитория() - НЕ_РЕГИСТРИРУЮТ
        if not any(fnmatch.fnmatchcase(имя, шаблон) for шаблон in декораторы)
    )
    assert not непокрытые, (
        "эти декораторы регистрируют функции, но не перечислены в ignore_decorators: "
        f"{непокрытые}"
    )


def test_декораторы_берутся_из_ast_включая_многострочные():
    """`@router.message(Command("x"))` и голый `@property` — оба видны как декораторы."""
    файл = PROJECT / "services" / "tg-gateway" / "app" / "routers" / "agent.py"
    # Строку ищем по имени: привязка к номеру ломается от любой правки соседних полей
    # (команда `/agent` удалена в Ш-12, и жёсткая строка 19 уже падала бы).
    строка = next(
        номер for номер, текст in enumerate(
            файл.read_text(encoding="utf-8").splitlines(), start=1)
        if "def agent_action_cb" in текст)
    assert "@router.callback_query" in decorators_of(файл, строка, "agent_action_cb")
    файл_свойств = PROJECT / "services" / "tool-api" / "app" / "settings.py"
    # Строку ищем по имени: привязка к номеру ломается от любой правки соседних полей.
    строка = next(
        номер for номер, текст in enumerate(
            файл_свойств.read_text(encoding="utf-8").splitlines(), start=1)
        if "def is_prod" in текст)
    assert "@property" in decorators_of(файл_свойств, строка, "is_prod")


def test_обработчик_отсеивается_а_мёртвая_функция_остаётся(tmp_path: Path):
    """Критерий приёмки: шум только по реально мёртвому коду."""
    файл = tmp_path / "handlers.py"
    файл.write_text(
        "router = object()\n"
        "\n"
        "@router.message\n"
        "async def on_text(message):\n"
        '    """Обработчик: зовёт фреймворк, а не код."""\n'
        "    return message\n"
        "\n"
        "def никем_не_званая():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    _, _, декораторы, _ = load_config()

    обработчик = Finding("deadcode", "handlers.py", 5, "on_text", "DC02 Function `on_text`")
    мёртвая = Finding("deadcode", "handlers.py", 9, "никем_не_званая", "DC02 Function")

    assert is_registered_handler(файл, обработчик, декораторы) is True
    assert is_registered_handler(файл, мёртвая, декораторы) is False


def test_vulture_с_конфигом_видит_новую_мёртвую_функцию(tmp_path: Path):
    """Критерий приёмки: новая мёртвая функция видна в отчёте, обработчик — нет."""
    пакет = tmp_path / "пакет"
    пакет.mkdir()
    (пакет / "модуль.py").write_text(
        "router = object()\n"
        "\n"
        "@router.get\n"
        "def обработчик():\n"
        "    return 1\n"
        "\n"
        "def забытая():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    _, _, декораторы, _ = load_config()
    сделано = subprocess.run(
        [
            sys.executable,
            "-m",
            "vulture",
            str(пакет),
            "--min-confidence",
            "60",
            "--ignore-decorators",
            ",".join(декораторы),
        ],
        capture_output=True,
        text=True,
    )
    assert "забытая" in сделано.stdout
    assert "обработчик" not in сделано.stdout


def test_базовая_линия_без_дублей_и_с_причиной():
    ключи = read_baseline()
    assert ключи, "базовая линия пуста: известный мёртвый код потерялся"
    assert len(ключи) == len(set(ключи)), "дубли в config/deadcode-baseline.txt"
    assert all("::" in ключ for ключ in ключи)
    assert "отдельной картой" in BASELINE.read_text(encoding="utf-8")


def test_проверка_зелёная_на_текущем_дереве():
    """Критерий приёмки: на чистом main проверка зелёная и ничего не блокирует."""
    сделано = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=PROJECT,
        capture_output=True,
        text=True,
    )
    assert сделано.returncode == 0, сделано.stdout + сделано.stderr
    assert "новых 0" in сделано.stdout
    # Отсев действительно работает: иначе «зелено» означало бы «нашлось мало».
    assert "Отсеяно обработчиков" in сделано.stdout


def test_pyproject_содержит_оба_раздела_конфига():
    конфиг = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]
    assert конфиг["vulture"]["paths"] and конфиг["deadcode"]["paths"]
    assert конфиг["vulture"]["min_confidence"] <= 60, (
        "порог выше 60 скрывает настоящие мёртвые функции: vulture оценивает их в 60%"
    )


def test_пути_конфига_существуют():
    vulture_paths, deadcode_paths, _, _ = load_config()
    for путь in set(vulture_paths) | set(deadcode_paths):
        assert (ROOT / путь).exists(), f"нет каталога из конфига: {путь}"


def test_ci_запускает_проверку_мёртвого_кода():
    работа = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deadcode"]
    шаги = "\n".join(шаг.get("run", "") for шаг in работа["steps"])
    assert "requirements-dev.txt" in шаги, "без зависимостей job упадёт на импорте"
    assert "scripts/check_deadcode.py" in шаги
