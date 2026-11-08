#!/usr/bin/env python3
"""Конфиг читается только через settings: скан `services/**` и allowlist с причиной.

Зачем отдельная проверка (карта t_c8d875f6, R-4 из docs/reports/79):

  * `Settings` появился в карте A, но чтение окружения осталось вразброс —
    `os.getenv`/`os.environ` встречались в девяти модулях вне `settings.py`.
    Из-за этого конфиг нельзя проверить тестом и нельзя понять, откуда в проде
    взялось значение;
  * `tests/unit/test_settings_single_source.py` ловил только чтение на уровне
    модуля (умолчание, просочившееся на импорте), а `os.getenv` внутри функции
    проходил мимо. Здесь правило жёстче: в `services/**/*.py` кроме
    `settings.py` чтения окружения нет вообще.

Известные исключения живут в `config/settings-allowlist.txt`: путь, число
совпадений, причина. Список не гниёт: запись без совпадений (файл удалён,
чтений стало меньше) — такая же ошибка, как новое чтение вне списка.

Как устроено:
  1. Файлы `services/**/*.py` разбираются по AST, а не грепом: совпадение
     в докстринге или комментарии чтением не считается.
  2. Совпадения сравниваются с allowlist по числу; лишние или недостающие
     печатаются с файлом и строкой.
  3. Новая находка — код выхода 1, чисто — 0.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/check_settings.py
    env -u PYTHONPATH .venv/bin/python scripts/check_settings.py --list
    env -u PYTHONPATH .venv/bin/python scripts/check_settings.py --update-allowlist

Тест: tests/unit/test_settings_single_source.py.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services"
ALLOWLIST = ROOT / "config" / "settings-allowlist.txt"

#: Файл, которому чтение окружения разрешено по определению.
SETTINGS_FILENAME = "settings.py"

#: Заготовка причины: проверка её не принимает, человек обязан заменить.
PLACEHOLDER_REASON = "ПРИЧИНА НЕ УКАЗАНА"

HEADER = (
    "# Исключения из правила «конфиг только через settings» (R-4, docs/reports/79).\n"
    "#\n"
    "# Формат строки: <путь от корня репозитория> <число совпадений> # <причина>\n"
    "# Путь — файл в services/**, кроме settings.py. Число — сколько чтений окружения\n"
    "# (os.getenv / os.environ / os.environ.get) осталось в файле. Причина — почему\n"
    "# исключение живёт: без неё строка не принимается.\n"
    "#\n"
    "# Список должен только сокращаться: перевели модуль на settings — уменьшите число\n"
    "# или уберите строку. Мёртвая запись (файла нет или совпадений меньше) — ошибка.\n"
    "# Обновить числа: env -u PYTHONPATH .venv/bin/python scripts/check_settings.py --update-allowlist\n"
    "#\n"
    "# Пустые чтения здесь не значатся: core.py упоминает os.environ только в докстринге.\n"
)


@dataclass(frozen=True)
class Hit:
    """Одно чтение окружения: где и что именно прочитано."""

    path: str      # путь от корня репозитория, через прямые слэши
    line: int
    what: str      # os.getenv / os.environ / os.environ.get / os.environ[...]

    def message(self) -> str:
        return f"читай конфиг через settings, файл {self.path}, строка {self.line} ({self.what})"


@dataclass(frozen=True)
class Entry:
    """Строка allowlist: файл, разрешённое число совпадений и причина."""

    path: str
    count: int
    reason: str

    @property
    def has_reason(self) -> bool:
        return bool(self.reason.strip()) and self.reason.strip() != PLACEHOLDER_REASON


class AllowlistError(ValueError):
    """Allowlist не разбирается: строка не по формату."""


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_environ(node: ast.AST) -> bool:
    """`os.environ` целиком (передача в env=, `.get`, индексация)."""
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    return isinstance(node, ast.Name) and node.id == "environ"   # from os import environ


def _read_hits(tree: ast.AST) -> list[tuple[int, str]]:
    """Чтения окружения в дереве модуля.

    Считается каждое место, а не каждая переменная: `os.environ["A"]` и `.get("B")`
    в одной строке — два места, `os.environ`, переданный целиком в `env=` — одно.

    Обход через `ast.walk`, а не подкласс `ast.NodeVisitor`: методы-обработчики
    `visit_*` инструмент deadcode объявляет мёртвыми (их зовёт только обходчик),
    и проверка мёртвого кода краснела бы на живом файле.
    """
    hits: list[tuple[int, str]] = []
    counted: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getenv":           # from os import getenv
                hits.append((node.lineno, "os.getenv"))
            elif isinstance(func, ast.Attribute) and func.attr == "getenv":  # os.getenv
                hits.append((node.lineno, "os.getenv"))
            elif (isinstance(func, ast.Attribute) and func.attr == "get"
                  and _is_environ(func.value)):                              # os.environ.get
                hits.append((node.lineno, "os.environ.get"))
                counted.add(id(func.value))
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):    # os.environ["X"]
            hits.append((node.lineno, "os.environ[...]"))
            counted.add(id(node.value))
    # `os.environ` не под индексацией и не в `.get`: передали целиком (env={**os.environ}).
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "environ"
                and id(node) not in counted):
            hits.append((node.lineno, "os.environ"))
    return sorted(hits)


def _parse(path: pathlib.Path) -> ast.AST:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:                       # битый файл — ошибка проверки, не тишина
        raise AllowlistError(f"{path}: не разбирается как Python: {exc}") from None


def scan_file(path: pathlib.Path, root: pathlib.Path = ROOT) -> list[Hit]:
    """Все чтения окружения в файле."""
    rel = _rel(path, root)
    return [Hit(path=rel, line=line, what=what) for line, what in _read_hits(_parse(path))]


def service_files(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    """Модули сервисов, к которым правило применяется: всё, кроме settings.py."""
    services = root / "services"
    if not services.is_dir():
        raise AllowlistError(f"нет каталога {services}: проверка запускается из корня репозитория")
    return sorted(path for path in services.rglob("*.py")
                  if path.name != SETTINGS_FILENAME and "__pycache__" not in path.parts)


def scan(root: pathlib.Path = ROOT) -> dict[str, list[Hit]]:
    """Чтения по файлам: только файлы, где они есть."""
    found: dict[str, list[Hit]] = {}
    for path in service_files(root):
        hits = scan_file(path, root)
        if hits:
            found[_rel(path, root)] = hits
    return found


def read_allowlist(path: pathlib.Path = ALLOWLIST) -> list[Entry]:
    """Разобрать allowlist; формат строгий, причина обязательна."""
    if not path.is_file():
        return []
    entries: dict[str, Entry] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, reason = line.partition("#")
        parts = body.split()
        if len(parts) != 2 or not parts[1].isdigit():
            raise AllowlistError(
                f"{_rel(path, ROOT)}:{number}: ожидалось «<путь> <число> # <причина>», "
                f"а не «{raw.strip()}»")
        entry = Entry(path=parts[0], count=int(parts[1]), reason=reason.strip())
        if entry.path in entries:
            raise AllowlistError(f"{_rel(path, ROOT)}:{number}: {entry.path} уже есть в списке")
        if pathlib.PurePosixPath(entry.path).name == SETTINGS_FILENAME:
            raise AllowlistError(
                f"{_rel(path, ROOT)}:{number}: {entry.path} — это settings.py, "
                "он читает окружение по определению и в списке исключений не нужен")
        entries[entry.path] = entry
    return [entries[key] for key in sorted(entries)]


def problems(root: pathlib.Path = ROOT, allowlist: pathlib.Path = ALLOWLIST) -> list[str]:
    """Все расхождения с правилом: новые чтения, мёртвые записи, записи без причины."""
    found = scan(root)
    entries = {entry.path: entry for entry in read_allowlist(allowlist)}
    issues: list[str] = []

    for path, hits in found.items():
        entry = entries.get(path)
        allowed = entry.count if entry else 0
        if len(hits) > allowed:
            issues.extend(hit.message() for hit in hits[allowed:])
            if entry:
                issues.append(
                    f"{path}: совпадений {len(hits)}, в allowlist разрешено {entry.count} — "
                    "увеличивать список можно только с причиной")
        elif len(hits) < allowed:
            issues.append(
                f"мёртвая запись в allowlist: {path} — совпадений {len(hits)}, "
                f"а разрешено {allowed}; уменьшите число или уберите строку")

    for path, entry in entries.items():
        if path not in found:
            issues.append(
                f"мёртвая запись в allowlist: {path} — файла нет или в нём не осталось "
                "чтений окружения; уберите строку")
        if not entry.has_reason:
            issues.append(f"{path}: в allowlist нет причины исключения")

    return issues


def format_allowlist(root: pathlib.Path, previous: list[Entry]) -> str:
    """Собрать allowlist по текущему дереву, сохранив прежние причины."""
    reasons = {entry.path: entry.reason for entry in previous}
    lines = [HEADER]
    for path, hits in sorted(scan(root).items()):
        reason = reasons.get(path) or PLACEHOLDER_REASON
        lines.append(f"{path} {len(hits)}  # {reason}")
    return "\n".join(lines)


def _print_hits(found: dict[str, list[Hit]]) -> None:
    for path, hits in sorted(found.items()):
        for hit in hits:
            print(f"  {path}:{hit.line}: {hit.what}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Конфиг читается только через settings (allowlist — config/settings-allowlist.txt)")
    parser.add_argument("--list", action="store_true",
                        help="показать все чтения окружения, включая разрешённые")
    parser.add_argument("--update-allowlist", action="store_true",
                        help="переписать config/settings-allowlist.txt по текущему дереву")
    args = parser.parse_args(argv)

    try:
        found = scan(ROOT)
        previous = read_allowlist(ALLOWLIST)
    except AllowlistError as exc:
        print(f"ГЕЙТ КРАСНЫЙ: {exc}", file=sys.stderr)
        return 1

    if args.update_allowlist:
        ALLOWLIST.parent.mkdir(parents=True, exist_ok=True)
        ALLOWLIST.write_text(format_allowlist(ROOT, previous), encoding="utf-8")
        entries = read_allowlist(ALLOWLIST)
        no_reason = [entry.path for entry in entries if not entry.has_reason]
        print(f"Allowlist обновлён: {len(entries)} записей → {_rel(ALLOWLIST, ROOT)}")
        if no_reason:
            print("Заполните причину у новых записей (сейчас стоит заготовка):")
            for path in no_reason:
                print(f"  - {path}")
        return 0

    if args.list:
        print(f"Чтений окружения вне settings.py: {sum(len(h) for h in found.values())}")
        _print_hits(found)
        print(f"Записей в allowlist: {len(previous)}")
        for entry in previous:
            print(f"  {entry.path}: {entry.count} — {entry.reason or 'без причины'}")

    issues = problems(ROOT, ALLOWLIST)
    if issues:
        print("ГЕЙТ КРАСНЫЙ: конфиг читается в обход settings:", file=sys.stderr)
        for issue in issues:
            print(f"  {issue}", file=sys.stderr)
        return 1

    allowed = sum(entry.count for entry in previous)
    print(f"Конфиг только через settings: {len(found)} файлов вне правила, "
          f"{sum(len(h) for h in found.values())} чтений, все в allowlist ({allowed} разрешено)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
