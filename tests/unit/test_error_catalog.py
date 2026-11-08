"""Каталог ошибок (docs/architecture/85) и код не расходятся.

Инвариант: каждый код, который `tool-api` может вернуть из `raise`, обязан быть в каталоге,
а статус в каталоге — совпадать с фактическим. Новый `raise` без строки в каталоге валит тест.
"""

from __future__ import annotations

import ast
import pathlib
import re

from app.errors import code_of

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = ROOT / "services" / "tool-api" / "app" / "main.py"
CATALOG = ROOT / "docs" / "architecture" / "85-error-catalog.md"

CODE_IN_CELL = re.compile(r"`([a-z][a-z0-9_]+)`")
STATUS = re.compile(r"\b(\d{3})\b")


def catalog() -> dict[str, set[int]]:
    """code -> статусы из таблиц каталога."""
    out: dict[str, set[int]] = {}
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        codes = CODE_IN_CELL.findall(cells[0])
        statuses = {int(s) for s in STATUS.findall(cells[1])}
        for code in codes:
            assert code not in out, f"{code} встречается в каталоге дважды"
            out[code] = statuses
    return out


def raised() -> dict[str, set[int]]:
    """code -> статусы из `raise` в main.py."""
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    out: dict[str, set[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        name = ast.unparse(node.exc.func)
        if not (name.endswith("HTTPException") or name == "ApiError"):
            continue
        args = [ast.unparse(arg) for arg in node.exc.args]
        detail = args[1] if len(args) > 1 else ""
        explicit = next((ast.unparse(kw.value) for kw in node.exc.keywords if kw.arg == "code"), None)
        if explicit:
            code = explicit.strip("'\"")
        elif detail.startswith(("f'", 'f"')):
            code = code_of(detail[2:].split("{")[0].rstrip(":_ "))
        else:
            code = code_of(detail.strip("'\""))
        status = args[0].replace("status.HTTP_", "").replace("_", " ").split()[0]
        out.setdefault(code, set()).add(int(status))
    return out


def test_каждый_код_из_raise_есть_в_каталоге():
    known = catalog()
    missing = sorted(set(raised()) - set(known))
    assert not missing, f"нет в docs/architecture/85: {missing}"


def test_статусы_в_каталоге_совпадают_с_кодом():
    known = catalog()
    wrong = []
    for code, statuses in raised().items():
        catalog_statuses = known.get(code)
        if catalog_statuses and not statuses <= catalog_statuses:
            wrong.append(f"{code}: в коде {sorted(statuses)}, в каталоге {sorted(catalog_statuses)}")
    assert not wrong, "расхождение статусов:\n" + "\n".join(wrong)


def test_конверт_описан_в_каталоге():
    text = CATALOG.read_text(encoding="utf-8")
    for field in ("request_id", "retryable", "X-Request-ID", "application/problem+json"):
        assert field in text, f"в каталоге нет {field}"
