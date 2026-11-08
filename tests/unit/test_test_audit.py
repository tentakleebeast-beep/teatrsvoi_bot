"""Обоснованность тестов: scripts/test_audit.py (правило из docs/development/26, L-010)."""

import json
from pathlib import Path

import pytest

from scripts import test_audit

GOOD = '''
"""Регрессии по итогам смоука, дефекты S-1…S-3."""


def test_число_обосновано():
    """FR-B5: второе подтверждение крупной суммы."""
    assert True


def test_слишком_длинный_текст():
    """S-11: текст длиннее лимита отклоняется."""
    assert True
'''

BAD = '''
def test_без_обоснования():
    assert True


def test_тоже_без():
    assert True
'''

PARTIAL = '''
"""Разбор листа — docs/architecture/17."""


def test_через_файл():
    assert True
'''


def _make(tmp_path: Path, files: dict[str, str]) -> Path:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for name, body in files.items():
        (tests_dir / name).write_text(body, encoding="utf-8")
    return tests_dir


def test_файл_с_ид_считается_обоснованным(tmp_path):
    root = _make(tmp_path, {"test_good.py": GOOD})
    tests = test_audit.file_tests(root / "test_good.py")
    assert [t["justified"] for t in tests] == [True, True]
    assert "FR-B5" in tests[0]["ids"]


def test_тесты_без_ид_необоснованы(tmp_path):
    root = _make(tmp_path, {"test_bad.py": BAD})
    tests = test_audit.file_tests(root / "test_bad.py")
    assert [t["justified"] for t in tests] == [False, False]
    assert tests[0]["via"] == ""


def test_docstring_модуля_закрывает_все_тесты(tmp_path):
    root = _make(tmp_path, {"test_partial.py": PARTIAL})
    tests = test_audit.file_tests(root / "test_partial.py")
    assert tests[0]["justified"] is True
    assert tests[0]["via"] == "файл"


def test_аудит_считает_итоги(tmp_path):
    root = _make(tmp_path, {"test_good.py": GOOD, "test_bad.py": BAD,
                            "test_partial.py": PARTIAL})
    report = test_audit.audit(root)
    assert report["total"] == 5
    assert report["justified"] == 3
    assert report["own_justified"] == 2      # FR-B5 и S-11 названы в самих тестах
    assert report["file_only"] == 1          # PARTIAL закрыт docstring модуля
    assert report["unjustified"] == 2
    assert {f["file"].split("/")[-1] for f in report["files"]} == {
        "test_good.py", "test_bad.py", "test_partial.py"}


def test_строгий_режим_возвращает_единицу(tmp_path, monkeypatch, capsys):
    root = _make(tmp_path, {"test_bad.py": BAD})
    monkeypatch.setattr(test_audit, "TESTS", root)
    monkeypatch.setattr(test_audit, "PROJECT", tmp_path)
    assert test_audit.main(["--strict"]) == 1
    assert "ИТОГО" in capsys.readouterr().out


def test_без_строгого_режима_код_ноль(tmp_path, monkeypatch):
    root = _make(tmp_path, {"test_bad.py": BAD})
    monkeypatch.setattr(test_audit, "TESTS", root)
    monkeypatch.setattr(test_audit, "PROJECT", tmp_path)
    assert test_audit.main([]) == 0


def test_json_даёт_итоги_без_списка_тестов(tmp_path, monkeypatch, capsys):
    root = _make(tmp_path, {"test_good.py": GOOD})
    monkeypatch.setattr(test_audit, "TESTS", root)
    monkeypatch.setattr(test_audit, "PROJECT", tmp_path)
    assert test_audit.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 2 and data["own_justified"] == 2
    assert data["files"][0]["unjustified"] == []


def test_реальные_тесты_разбираются_и_есть_обоснованные():
    """LU-0: на реальном наборе скрипт находит и обоснованные, и нет — он не пустой."""
    report = test_audit.audit()
    assert report["total"] > 100
    assert report["justified"] > 0


@pytest.mark.parametrize("name", ["test_intro.py", "test_slots.py"])
def test_разбор_одного_файла_не_падает(name):
    tests = test_audit.file_tests(test_audit.TESTS / "unit" / name)
    assert tests
    assert all("line" in t and "name" in t for t in tests)


def test_номер_обращения_считается_ид():
    """Ш-51: обращение №15 — такое же обоснование, как C-3 или FR-B5."""
    assert test_audit.ID_RE.search("Обращение №15: панель показывала декабрь")
    assert test_audit.ID_RE.findall("S-3 и №22") == ["S-3", "№22"]
    assert test_audit.ID_RE.search("дефект №7")
    # Д-03 — так нумеруются дефекты в отчётах смоука (docs/reports/145)
    assert test_audit.ID_RE.search("Д-03: хранилище не развёрнуто")
