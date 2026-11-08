"""Матрица требований и отчёт смоука: scripts/test_audit.py --matrix, scripts/smoke_report.py.

Обоснование: FR-… связи «требование → доказательство» — docs/development/143 §3 (Э3),
критерии К1–К3, К9, К14. Проверяется машина сверки, а не прогон на стенде: строки
документа 19 читаются как есть, junit join'ится по имени теста, шаг без
ожидаемого/фактического результата валит отчёт.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import smoke_report, test_audit


def test_матрица_по_документу_113_строк():
    """К1: ровно те ID, что в документе 19, чужого не появляется."""
    rows = test_audit.requirement_rows()
    matrix = test_audit.matrix()
    assert len(rows) == len(matrix) == 113
    assert list(matrix) == [row["id"] for row in rows]
    assert all(isinstance(items, list) for items in matrix.values())
    # у требования без теста список пустой, а не выдуманный
    assert any(not items for items in matrix.values())
    for items in matrix.values():
        for entry in items:
            assert set(entry) == {"file", "test"}
            assert entry["file"].startswith("tests/")


def test_матрица_отдаёт_json_и_код_ноль(capsys):
    """К1: `--matrix --json` печатает объект и всегда возвращает 0."""
    assert test_audit.main(["--matrix", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 113


def test_строки_дополнений_без_приоритета(tmp_path):
    """К18: строки без колонки приоритета идут как «не задан», статус читается по ячейке."""
    doc = tmp_path / "19.md"
    doc.write_text(
        "| ID | Требование | Приоритет | Статус | Подтверждение |\n"
        "|---|---|---|---|---|\n"
        "| FR-A1 | Доступ владельца | P0 | готово | код |\n"
        "| FR-G9 | Гранулярность записи | готово (systemd) | код |\n",
        encoding="utf-8")
    rows = test_audit.requirement_rows(doc)
    assert [row["id"] for row in rows] == ["FR-A1", "FR-G9"]
    assert rows[0]["priority"] == "P0"
    assert rows[1]["priority"] == "не задан"
    assert rows[1]["status"] == "готово"


def _write_junit(path: Path, cases: list[tuple[str, str, str]]) -> Path:
    """Минимальный junit: (имя, исход, сообщение)."""
    body = []
    for name, result, message in cases:
        if result == "passed":
            body.append(f'<testcase classname="tests.smoke.test_cm01_login" '
                        f'file="tests/smoke/test_cm01_login.py" name="{name}"/>')
        elif result == "failed":
            body.append(f'<testcase classname="tests.smoke.test_cm01_login" '
                        f'file="tests/smoke/test_cm01_login.py" name="{name}">'
                        f'<failure message="boom"/></testcase>')
        else:
            body.append(f'<testcase classname="tests.smoke.test_cm01_login" '
                        f'file="tests/smoke/test_cm01_login.py" name="{name}">'
                        f'<skipped message="{message}"/></testcase>')
    path.write_text(f'<testsuites><testsuite>{"".join(body)}</testsuite></testsuites>',
                    encoding="utf-8")
    return path


def test_шаг_без_факта_валит_отчёт(tmp_path):
    """К9: шаг без `actual` — ошибка отчёта, а не «пройдено»."""
    report = {"version": 1, "commit": "x", "date": "2026-11-08", "env": "стенд",
              "dump_sha256": "x", "layers": {}, "requirements": [], "cleanup": {},
              "defects": [],
              "scenarios": [{"id": "СМ-1", "step": "1", "expected": "200", "actual": "",
                             "evidence": "GET /health", "result": "пройдено"}]}
    problems = smoke_report.validate(report)
    assert any("пустое поле actual" in item for item in problems)
    assert any("строк матрицы 0" in item for item in problems)


def test_причина_непроверки_только_из_списка():
    """К14: причина вне закрытого списка — ошибка отчёта."""
    report = {"version": 1, "commit": "x", "date": "2026-11-08", "env": "стенд",
              "dump_sha256": "x", "layers": {}, "scenarios": [], "cleanup": {}, "defects": [],
              "requirements": [{"id": "FR-A1", "result": "не проверено",
                                "cause": "не захотели", "priority": "P0"}]}
    problems = smoke_report.validate(report)
    assert any("вне закрытого списка" in item for item in problems)


def test_junit_даёт_провал_и_пропуск(tmp_path):
    """К3: исход берётся из junit, параметризация срезается."""
    xml = _write_junit(tmp_path / "j.xml", [
        ("test_ok", "passed", ""), ("test_bad[1]", "failed", ""),
        ("test_skip", "skipped", "нет данных в копии: стенд не включён")])
    outcomes = smoke_report.junit_outcomes(xml)
    assert outcomes[("tests/smoke/test_cm01_login.py", "test_ok")]["result"] == "пройдено"
    assert outcomes[("tests/smoke/test_cm01_login.py", "test_bad")]["result"] == "провал"
    skipped = outcomes[("tests/smoke/test_cm01_login.py", "test_skip")]
    assert skipped["result"] == "не проверено"
    assert smoke_report.skip_cause(skipped["message"]) in smoke_report.CAUSES


def test_дельта_видит_изменившиеся_строки():
    """К15: дельта считается по JSON, а не пересказом; без прошлого — явная строка."""
    current = [{"id": "FR-A1", "result": "пройдено", "cause": ""},
               {"id": "FR-A2", "result": "провал", "cause": ""}]
    previous = {"date": "2026-11-07",
                "requirements": [{"id": "FR-A1", "result": "не проверено", "cause": "нет автотеста"},
                                 {"id": "FR-A3", "result": "пройдено", "cause": ""}]}
    diff = smoke_report.delta(current, previous)
    assert diff["added"] == ["FR-A2"]
    assert diff["removed"] == ["FR-A3"]
    assert diff["changed"][0]["id"] == "FR-A1"
    assert smoke_report.delta(current, None)["note"].startswith("первый прогон")


@pytest.mark.parametrize("path", ["docs/development/143-smoke-plan.md",
                                  "docs/development/144-smoke-runbook.md"])
def test_документы_смоука_на_месте(path):
    """К20–К21: runbook и дизайн лежат в docs/development и связаны."""
    text = Path(path).read_text(encoding="utf-8")
    assert "smoke" in text.lower()
