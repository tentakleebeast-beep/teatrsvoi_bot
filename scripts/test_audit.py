#!/usr/bin/env python3
"""Инвентаризация тестов: у каждого должно быть обоснование — ID требования или дефекта.

Правило проекта (docs/development/26): тест без ID требования (`FR-…`, `UC-…`), дефекта
(`S-…`, `C-…`, `B-…`), вопроса (`V-…`), шага (`Ш-…`), урока (`L-…`), препятствия (`O-…`)
или намерения (`INT-…`) не принимается — иначе непонятно, что он защищает.

Обоснование ищется в имени теста, в его docstring или в docstring модуля (тогда тест
считается обоснованным «на уровне файла»).

  env -u PYTHONPATH .venv/bin/python scripts/test_audit.py              # таблица по файлам
  ... --file tests/unit/test_intro.py       # один файл, по каждому тесту
  ... --json                                # машинный вывод
  ... --strict                              # код 1, если есть тесты без обоснования
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TESTS = PROJECT / "tests"

# ID требования, дефекта, обращения (№15), вопроса, шага, урока, препятствия, намерения
ID_RE = re.compile(r"№\s?\d+|FR-[A-ZА-Я]?\d+|UC-\d+|S-\d+|C-\d+|B-\d+|V-\d+|Ш-\d+|L-\d+|O-\d+|INT-\d+")
# Ссылка на документ тоже засчитывается: «docs/reports/62»
DOC_RE = re.compile(r"\bdocs/[a-z]+/")


def _text(node: ast.AST) -> str:
    """Docstring узла: модуля, класса или функции (None -> пустая строка)."""
    return ast.get_docstring(node) or ""  # type: ignore[arg-type]


def file_tests(path: Path) -> list[dict]:
    """Список тестов файла: имя, обоснование (свой или файловый), строка."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = _text(tree)
    module_ok = bool(ID_RE.search(module_doc) or DOC_RE.search(module_doc))

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        own = f"{node.name}\n{_text(node)}"
        own_ok = bool(ID_RE.search(own))
        found.append({
            "name": node.name,
            "line": node.lineno,
            "own": own_ok,
            "justified": own_ok or module_ok,
            "via": "тест" if own_ok else ("файл" if module_ok else ""),
            "ids": sorted(set(ID_RE.findall(own)) | (set(ID_RE.findall(module_doc)) if module_ok else set())),
        })
    found.sort(key=lambda item: item["line"])
    return found


def audit(root: Path | None = None) -> dict:
    """Собирает статистику по всем файлам тестов (root по умолчанию — tests/)."""
    root = root or TESTS
    per_file = []
    for path in sorted(root.rglob("test_*.py")):
        tests = file_tests(path)
        if not tests:
            continue
        justified = sum(1 for t in tests if t["justified"])
        own = sum(1 for t in tests if t["own"])
        try:                       # путь для отчёта: сначала от корня проекта
            shown = path.relative_to(PROJECT).as_posix()
        except ValueError:         # чужой корень (тесты самого скрипта)
            shown = path.relative_to(root.parent).as_posix()
        per_file.append({
            "file": shown,
            "total": len(tests),
            "justified": justified,
            "own": own,
            "unjustified": [t["name"] for t in tests if not t["justified"]],
            "tests": tests,
        })

    total = sum(f["total"] for f in per_file)
    justified = sum(f["justified"] for f in per_file)
    own = sum(f["own"] for f in per_file)
    return {
        "total": total,
        "justified": justified,
        "own_justified": own,
        "file_only": justified - own,
        "unjustified": total - justified,
        "files": per_file,
    }


def table(report: dict) -> str:
    """Текстовая таблица: файл, всего, обосновано, без обоснования."""
    lines = [f"{'файл':54} {'всего':>6} {'своих':>6} {'файлом':>7} {'нет':>5}"]
    for item in sorted(report["files"], key=lambda f: -len(f["unjustified"])):
        name = item["file"].replace("tests/", "")
        lines.append(f"{name:54} {item['total']:>6} {item['own']:>6} "
                     f"{item['justified'] - item['own']:>7} "
                     f"{len(item['unjustified']):>5}")
    lines.append("-" * 82)
    lines.append(f"{'ИТОГО':54} {report['total']:>6} {report['own_justified']:>6} "
                 f"{report['file_only']:>7} {report['unjustified']:>5}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Обоснованность тестов")
    parser.add_argument("--file", default=None, help="разобрать один файл по тестам")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="код 1 при тестах без обоснования")
    args = parser.parse_args(argv)

    if args.file:
        path = (PROJECT / args.file) if not Path(args.file).is_absolute() else Path(args.file)
        tests = file_tests(path)
        if args.json:
            print(json.dumps(tests, ensure_ascii=False, indent=2))
        else:
            for test in tests:
                mark = "ok" if test["justified"] else "нет"
                ids = ", ".join(test["ids"]) or "—"
                print(f"{mark:3} {test['name']:60} {ids}")
        bad = sum(1 for t in tests if not t["justified"])
        return 1 if (args.strict and bad) else 0

    report = audit()
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "files"} |
                         {"files": [{k: v for k, v in f.items() if k != "tests"}
                                    for f in report["files"]]}, ensure_ascii=False, indent=2))
    else:
        print(table(report))
    return 1 if (args.strict and report["unjustified"]) else 0


if __name__ == "__main__":
    sys.exit(main())
