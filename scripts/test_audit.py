#!/usr/bin/env python3
"""Инвентаризация тестов: у каждого должно быть обоснование — ID требования или дефекта.

Правило проекта (docs/development/26): тест без ID требования (`FR-…`, `UC-…`), дефекта
(`S-…`, `C-…`, `B-…`, `Д-…`), вопроса (`V-…`), шага (`Ш-…`), урока (`L-…`), препятствия
(`O-…`) или намерения (`INT-…`) не принимается — иначе непонятно, что он защищает.

Обоснование ищется в имени теста, в его docstring или в docstring модуля (тогда тест
считается обоснованным «на уровне файла»).

  env -u PYTHONPATH .venv/bin/python scripts/test_audit.py              # таблица по файлам
  ... --file tests/unit/test_intro.py       # один файл, по каждому тесту
  ... --json                                # машинный вывод
  ... --strict                              # код 1, если есть тесты без обоснования
  ... --matrix --json                       # матрица «требование → тесты» (Э3, docs/development/143)

`--matrix` — вход слоя А смоука: JSON вида `{"FR-A2": [{"file": "tests/…", "test": "имя"}]}`
ровно по тем ID, что стоят в таблицах `docs/development/19`; требование без теста получает
пустой список. Парсер ID и AST те же, что в обычном прогоне: второй parser требований
в проекте не появляется (`scripts/smoke_report.py` берёт строки отсюда же).
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
# Источник требований — документ 19. Он не переписывается этой картой: строки читаются
# как есть, в том числе приоритеты и статусы (docs/development/143 §3).
REQUIREMENTS_DOC = PROJECT / "docs" / "development" / "19-functional-requirements.md"
FR_ROW_RE = re.compile(r"^\|\s*(FR-[A-ZА-Я]?\d+)\s*\|(.*)$")
STATUSES = ("готово", "частично", "не начато", "отменено", "убрано")
# В документе встречается «выполнено» вместо «готово» и уточнения в скобках
# («готово (systemd …)», «частично (dry-run)») — сводим к закрытому набору статусов.
STATUS_ALIASES = {"выполнено": "готово"}
PRIORITIES = ("P0", "P1", "P2")

# ID требования, дефекта, обращения (№15), вопроса, шага, урока, препятствия, намерения
ID_RE = re.compile(r"№\s?\d+|FR-[A-ZА-Я]?\d+|UC-\d+|S-\d+|C-\d+|B-\d+|Д-\d+|V-\d+|Ш-\d+|L-\d+|O-\d+|INT-\d+")
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


def _cells(line: str) -> list[str]:
    """Ячейки строки markdown-таблицы без пустых краёв."""
    parts = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return parts


def _priority(cell: str) -> str:
    """Приоритет из ячейки: P0/P1/P2, иначе «не задан» (не додумываем, V-044)."""
    return cell if cell in PRIORITIES else "не задан"


def _status(cell: str) -> str:
    """Статус из ячейки, сведённый к закрытому набору документа 19."""
    text = cell.split("(")[0].strip().lower()
    text = STATUS_ALIASES.get(text, text)
    return text if text in STATUSES else ""


def requirement_rows(path: Path | None = None) -> list[dict]:
    """Строки требований из документа 19: id, текст, приоритет, статус, подтверждение.

    Читаются только строки таблиц, начинающиеся с `| FR-…`. Строки дополнений 27.10 бывают
    двух схем: с колонкой приоритета (`ID | текст | P | статус`, после V-044) и без неё
    (`ID | текст | статус`) — во втором случае `priority = "не задан"`, а колонка статуса
    берётся по номеру ячейки, а не по угадыванию текста.
    """
    doc = Path(path) if path is not None else REQUIREMENTS_DOC
    rows: list[dict] = []
    seen: set[str] = set()
    for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
        match = FR_ROW_RE.match(line)
        if not match:
            continue
        cells = _cells(line)
        req_id = cells[0]
        if req_id in seen:                  # повтор ID — ошибка источника, не матрицы
            continue
        seen.add(req_id)
        body = [cell for cell in cells[1:] if cell]
        title = body[0] if body else ""
        if len(body) >= 4:                  # полная таблица: ID | текст | P | статус | подтверждение
            priority, status, confirm = _priority(body[1]), _status(body[2]), body[3]
        elif len(body) == 3 and body[1] in PRIORITIES:
            # дополнения с колонкой приоритета: ID | текст | P | статус
            priority, status, confirm = _priority(body[1]), _status(body[2]), ""
        elif len(body) == 3:                # дополнения без приоритета, с подтверждением
            priority, status, confirm = "не задан", _status(body[1]), body[2]
        elif len(body) == 2:                # дополнения без приоритета: ID | текст | статус
            priority, status, confirm = "не задан", _status(body[1]), ""
        else:
            priority, status, confirm = "не задан", "", ""
        rows.append({"id": req_id, "text": title, "priority": priority,
                     "status": status, "confirm": confirm, "line": number})
    return rows


def matrix(root: Path | None = None, doc: Path | None = None) -> dict[str, list[dict]]:
    """Матрица слоя А: `FR-… → [{file, test}]` по факту ID в тестах, а не по документам.

    Требование без теста получает пустой список — это и есть честная точка отсчёта
    (docs/development/143 §4), а не повод выдумать доказательство.
    """
    mapping: dict[str, list[dict]] = {row["id"]: [] for row in requirement_rows(doc)}
    for item in audit(root)["files"]:
        for test in item["tests"]:
            for req_id in test["ids"]:
                if req_id in mapping and req_id.startswith("FR-"):
                    entry = {"file": item["file"], "test": test["name"]}
                    if entry not in mapping[req_id]:
                        mapping[req_id].append(entry)
    return mapping


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
    parser.add_argument("--matrix", action="store_true",
                        help="матрица «требование → тесты» из docs/development/19 (JSON)")
    args = parser.parse_args(argv)

    if args.matrix:
        # Всегда JSON и всегда код 0: это вход отчёта смоука, а не проверка.
        print(json.dumps(matrix(), ensure_ascii=False, indent=2))
        return 0

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
