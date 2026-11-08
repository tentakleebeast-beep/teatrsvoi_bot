#!/usr/bin/env python3
"""Проверка мёртвого кода: vulture и deadcode с отсевом зарегистрированных обработчиков.

Зачем отдельный запуск (docs/reports/84, приложение):
  * vulture и deadcode не понимают, что функции, помеченные `@router.message`,
    `@app.get` и т. п., регистрируются декоратором, и объявляют их мёртвыми:
    289 сообщений из 289 — обработчики и их соседи, сигнала в таком отчёте нет;
  * vulture фильтрует декораторы сам (ключ `ignore_decorators` в pyproject.toml),
    а deadcode 2.4.1 — нет: его флаги `--ignore-*-decorated-with` разбираются
    в `Argparse`, но в обходчике не применяются (`DeadCodeVisitor.ignore_decorators`
    остаётся пустым). Проверено на этом репозитории: 289 сообщений с флагами
    и 289 без них. Поэтому декораторы для deadcode отсеиваются здесь, по AST.

Как устроено:
  1. Оба инструмента запускаются из корня репозитория с параметрами из pyproject.toml
     (`[tool.vulture]` — родной конфиг vulture, `[tool.deadcode]` — наш, его читает
     именно этот скрипт).
  2. Сообщения deadcode о функциях и методах, объявленных с игнорируемым
     декоратором, отбрасываются.
  3. Остаток сравнивается с `config/deadcode-baseline.txt` (известный мёртвый код,
     разбор — отдельной картой). Новые находки — код выхода 1, известные — 0.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/check_deadcode.py
    env -u PYTHONPATH .venv/bin/python scripts/check_deadcode.py --update-baseline

Тест: tests/unit/test_deadcode_config.py.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BASELINE = ROOT / "config" / "deadcode-baseline.txt"

VULTURE_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<message>.+)$")
VULTURE_NAME = re.compile(r"'([^']+)'")
DEADCODE_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+): (?P<code>DC\d+) "
    r"(?P<kind>\S+) `(?P<name>[^`]+)` (?:is never used|is never used\b.*)$"
)


@dataclass(frozen=True)
class Finding:
    """Одно сообщение инструмента, приведённое к устойчивому ключу."""

    tool: str
    path: str
    line: int
    name: str
    message: str

    @property
    def key(self) -> str:
        # Строка меняется от любой правки выше, поэтому ключ — файл и имя.
        return f"{self.path}::{self.name}"


# Каталоги, которые инструменты читают как «мёртвый код», хотя вызов идёт по имени.
# Ревизии Alembic: фреймворк загружает файл по имени и вызывает upgrade()/downgrade()
# сам, как обработчики чата декоратором. 57 файлов дали бы 456 находок на пустом
# месте. Содержимое ревизий проверяет tests/unit/test_migrations_registry.py.
EXCLUDED_PREFIXES = ("services/tool-api/migrations/",)


def load_config() -> tuple[list[str], list[str], list[str], list[str]]:
    """Вернуть (пути для vulture, пути для deadcode, игнорируемые декораторы, имена)."""
    if not PYPROJECT.is_file():
        raise SystemExit(f"нет {PYPROJECT}: скрипт запускается из корня репозитория")
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    tool = data.get("tool", {})
    vulture = tool.get("vulture", {})
    deadcode = tool.get("deadcode", {})

    vulture_paths = list(vulture.get("paths", []))
    deadcode_paths = list(deadcode.get("paths", vulture_paths))
    decorators = list(vulture.get("ignore_decorators", [])) + [
        d for d in deadcode.get("ignore_decorators", []) if d not in vulture.get("ignore_decorators", [])
    ]
    # Имена, которые vulture уже игнорирует на своей стороне, deadcode тоже не должен
    # показывать: иначе один и тот же ложный «атрибут чужого объекта» снова в отчёте.
    names = list(deadcode.get("ignore_names", vulture.get("ignore_names", [])))
    if not vulture_paths:
        raise SystemExit("в pyproject.toml нет [tool.vulture].paths")
    if not decorators:
        raise SystemExit("в pyproject.toml нет ignore_decorators: обработчики снова станут «мёртвыми»")
    return vulture_paths, deadcode_paths, decorators, names


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return "@" + ".".join(reversed(parts))


def decorators_of(path: pathlib.Path, line: int, name: str) -> tuple[str, ...]:
    """Декораторы объявления `name`, в чьи строки попадает сообщение инструмента.

    deadcode указывает строку внутри объявления (тело, docstring или `def`),
    поэтому объявление ищется по AST и сверяется по диапазону строк.
    """
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError):
        return ()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name != name or not node.decorator_list:
            continue
        start = node.decorator_list[0].lineno
        end = node.end_lineno or node.lineno
        if start <= line <= end:
            return tuple(_decorator_name(d) for d in node.decorator_list)
    return ()


def is_registered_handler(path: pathlib.Path, finding: Finding, patterns: list[str]) -> bool:
    """Объявлен ли `finding` с декоратором из списка (то есть это живой обработчик)."""
    for decorator in decorators_of(path, finding.line, finding.name):
        if any(fnmatch.fnmatchcase(decorator, pattern) for pattern in patterns):
            return True
    return False


def _rel(path: str) -> str:
    try:
        return pathlib.Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return pathlib.Path(path).as_posix()


def run_vulture() -> list[Finding]:
    done = subprocess.run(
        [sys.executable, "-m", "vulture", "--config", str(PYPROJECT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # vulture: 0 — чисто, 3 — есть находки, 1/2 — ошибка входа или аргументов.
    if done.returncode not in (0, 3):
        raise SystemExit(f"vulture не отработал (код {done.returncode}):\n{done.stderr.strip()}")
    findings = []
    for raw in done.stdout.splitlines():
        match = VULTURE_LINE.match(raw.strip())
        if not match:
            continue
        name = VULTURE_NAME.search(match["message"])
        if not name:
            continue
        findings.append(
            Finding(
                tool="vulture",
                path=_rel(match["path"]),
                line=int(match["line"]),
                name=name.group(1),
                message=match["message"],
            )
        )
    return findings


def run_deadcode(paths: list[str], ignore_names: list[str]) -> list[Finding]:
    binary = pathlib.Path(sys.executable).with_name("deadcode")
    if not binary.is_file():
        binary = pathlib.Path(shutil.which("deadcode") or "")
    if not binary.is_file():
        raise SystemExit(
            "deadcode не установлен: uv pip install -r requirements-dev.txt"
        )
    command = [str(binary), *paths, "--no-color"]
    if ignore_names:
        # Работающий у deadcode флаг (в отличие от декораторов): имена-ложные срабатывания.
        command = [str(binary), *paths, "--no-color", "--ignore-names", *ignore_names]
    done = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # deadcode: 0 — чисто, 1 — есть находки.
    if done.returncode not in (0, 1):
        raise SystemExit(f"deadcode не отработал (код {done.returncode}):\n{done.stderr.strip()}")
    findings = []
    for raw in done.stdout.splitlines():
        match = DEADCODE_LINE.match(raw.strip())
        if not match:
            continue
        findings.append(
            Finding(
                tool="deadcode",
                path=_rel(match["path"]),
                line=int(match["line"]),
                name=match["name"],
                message=f"{match['code']} {match['kind']} `{match['name']}`",
            )
        )
    return findings


def read_baseline() -> list[str]:
    if not BASELINE.is_file():
        return []
    keys = []
    for raw in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            keys.append(line)
    return keys


def write_baseline(keys: list[str]) -> None:
    header = (
        "# Известный мёртвый код: карта t_b9c9674c, разбор — отдельной картой.\n"
        "# Ключ: путь::имя. Список должен только сокращаться; пополнять — осознанно,\n"
        "# с указанием причины в комментарии рядом.\n"
        "# Обновить: env -u PYTHONPATH .venv/bin/python scripts/check_deadcode.py --update-baseline\n"
    )
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(header + "\n".join(sorted(keys)) + "\n", encoding="utf-8")


def _excluded(path: str) -> bool:
    """Путь вне отчёта: каталог вызывается по имени (см. EXCLUDED_PREFIXES)."""
    return path.startswith(EXCLUDED_PREFIXES)


def collect(
    decorator_patterns: list[str], deadcode_paths: list[str], ignore_names: list[str]
) -> tuple[list[Finding], int]:
    """Все находки обоих инструментов и число отсеянных обработчиков."""
    findings = [f for f in run_vulture() if not _excluded(f.path)]
    dropped: set[str] = set()
    for finding in run_deadcode(deadcode_paths, ignore_names):
        if _excluded(finding.path):
            continue
        if is_registered_handler(ROOT / finding.path, finding, decorator_patterns):
            dropped.add(finding.key)
            continue
        findings.append(finding)

    # deadcode при выводе в трубу печатает каждое сообщение дважды (на терминале — один
    # раз), поэтому повторы схлопываются: ключ = инструмент + файл + имя.
    unique: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        unique.setdefault((finding.tool, finding.key), finding)
    return list(unique.values()), len(dropped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Мёртвый код без шума на декораторах")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="перезаписать config/deadcode-baseline.txt текущими находками",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="показать все находки, включая известные (для чистки отдельной картой)",
    )
    args = parser.parse_args(argv)

    _, deadcode_paths, decorator_patterns, ignore_names = load_config()
    findings, dropped = collect(decorator_patterns, deadcode_paths, ignore_names)

    if args.update_baseline:
        keys = sorted({f.key for f in findings})
        write_baseline(keys)
        print(f"Базовая линия обновлена: {len(keys)} записей → {BASELINE.relative_to(ROOT)}")
        return 0

    baseline = set(read_baseline())
    known = [f for f in findings if f.key in baseline]
    new = [f for f in findings if f.key not in baseline]
    found_keys = {f.key for f in findings}
    stale = sorted(baseline - found_keys)

    print(f"Отсеяно обработчиков, зарегистрированных декоратором: {dropped}")
    print(f"Находок: {len(findings)} (известных {len(known)}, новых {len(new)})")
    if args.list:
        for finding in sorted(findings, key=lambda f: (f.path, f.line)):
            метка = "известно" if finding.key in baseline else "НОВОЕ"
            print(f"  [{метка}] {finding.tool}: {finding.path}:{finding.line}: {finding.message}")
    if stale:
        print(f"Больше не воспроизводятся (можно убрать из базовой линии): {len(stale)}")
        for key in stale:
            print(f"  - {key}")
    if new:
        print("Новый мёртвый код:")
        for finding in sorted(new, key=lambda f: (f.path, f.line)):
            print(f"  {finding.tool}: {finding.path}:{finding.line}: {finding.message}")
        print("Если это осознанно — добавьте в config/deadcode-baseline.txt с причиной.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
