"""Матрица требований и отчёт смоука: scripts/test_audit.py --matrix, scripts/smoke_report.py.

Обоснование: FR-… связи «требование → доказательство» — docs/development/143 §3 (Э3),
критерии К1–К3, К5, К7, К9, К14, К17. Проверяется машина сверки, а не прогон на стенде: строки
документа 19 читаются как есть, junit join'ится по имени теста, шаг без
ожидаемого/фактического результата валит отчёт, порог P1 считается по К17 (80 %),
а тест в матрице без исхода в junit валит отчёт, а не превращается в «нет автотеста»;
проверки прогона К5/К7 попадают в отчёт из `--checks` и не могут промолчать.
"""

from __future__ import annotations

import json
import subprocess
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


def test_строки_дополнений_с_приоритетом(tmp_path):
    """V-044/FR-D18: строка дополнений с колонкой приоритета даёт и класс, и статус.

    После V-044 третья колонка двух таблиц «Дополнения от 27.10.2026» — приоритет
    (`ID | текст | P | статус`, 24 строки). Если парсер считает такую строку «короткой»,
    класс молча уходит в «не задан», и требование выпадает из процентного критерия §7.
    """
    doc = tmp_path / "19.md"
    doc.write_text(
        "| ID | Требование | Приоритет | Статус |\n"
        "|---|---|---|---|\n"
        "| FR-D18 | Отмена события | P1 | готово |\n"
        "| FR-H12 | Супервизор gateway | P0 | готово (systemd) |\n",
        encoding="utf-8")
    rows = test_audit.requirement_rows(doc)
    assert [(row["id"], row["priority"], row["status"]) for row in rows] == [
        ("FR-D18", "P1", "готово"), ("FR-H12", "P0", "готово")]


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


def test_слои_названы_и_пропуск_не_отменяет_исполненный_исход(tmp_path):
    """Д1/146 §2.3: junit принимается с именем слоя; пропуск в core уступает webapp.

    Три junit — не удобство: webapp-тесты в `core`-прогоне пропущены по
    `SVOI_WEBAPP_TESTS`, и без второго слоя требование выглядело бы непроверенным.
    """
    core = _write_junit(tmp_path / "core.xml", [("test_ok", "skipped", "webapp не включён"),
                                                ("test_only_core", "passed", "")])
    webapp = _write_junit(tmp_path / "webapp.xml", [("test_ok", "passed", "")])
    outcomes = smoke_report.junit_outcomes([f"core={core}", f"webapp={webapp}"])
    assert outcomes[("tests/smoke/test_cm01_login.py", "test_ok")]["result"] == "пройдено"
    assert outcomes[("tests/smoke/test_cm01_login.py", "test_only_core")]["result"] == "пройдено"
    layers = smoke_report.junit_layers([f"core={core}", f"webapp={webapp}"])
    assert [layer["layer"] for layer in layers] == ["core", "webapp"]
    assert [layer["tests"] for layer in layers] == [2, 1]
    assert layers[0]["skipped"] == 1


def test_исполненный_тест_дважды_с_разными_исходами_валит_сборку(tmp_path):
    """146 §2.3 п.3: два исполненных исхода одного теста — разные деревья, код 2."""
    first = _write_junit(tmp_path / "a.xml", [("test_x", "passed", "")])
    second = _write_junit(tmp_path / "b.xml", [("test_x", "failed", "")])
    with pytest.raises(smoke_report.InputError):
        smoke_report.junit_outcomes([f"core={first}", f"smoke={second}"])


def test_нет_файла_слоя_это_ошибка_входа(tmp_path):
    """146 §2.2: пропавший junit слоя — ошибка входа (код 2), а не «ноль тестов»."""
    missing = tmp_path / "нет.xml"
    with pytest.raises(smoke_report.InputError):
        smoke_report.junit_layers([f"core={missing}"])
    with pytest.raises(smoke_report.InputError):
        smoke_report.junit_outcomes([f"webapp={missing}"])
    with pytest.raises(smoke_report.InputError):
        smoke_report.junit_layers([f"unknown={missing}"])


def test_сборка_с_несуществующим_слоем_даёт_код_2(tmp_path):
    """146 §2.2: вход негоден — код 2 и ни .json, ни .md не пишутся."""
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"FR-A1": []}), encoding="utf-8")
    out = tmp_path / "report"
    code = smoke_report.main(["--junit", f"core={tmp_path / 'нет.xml'}", "--matrix", str(matrix),
                              "--out", str(out), "--no-gates", "--commit", "HEAD"])
    assert code == 2
    assert not out.with_suffix(".json").exists()
    assert not out.with_suffix(".md").exists()


def test_гейт_полноты_считает_пары_и_валит_отчёт():
    """Д1/146 §2.4: 3 пары матрицы, 1 исход — отчёт не собирается и называет счёт."""
    matrix = {"FR-A1": [{"file": "tests/unit/a.py", "test": "test_one"},
                        {"file": "tests/unit/a.py", "test": "test_two"}],
              "FR-A2": [{"file": "tests/unit/a.py", "test": "test_two"},
                        {"file": "tests/unit/b.py", "test": "test_three"}]}
    outcomes = {("tests/unit/a.py", "test_one"): {"result": "пройдено", "message": ""}}
    cov = smoke_report.coverage(matrix, outcomes)
    assert (cov["pairs"], cov["covered"], len(cov["missing"])) == (3, 1, 2)
    problems = smoke_report.validate(_minimal_report(coverage=cov))
    assert any("1/3 пар" in item and "пропало 2" in item for item in problems)


def test_тест_в_матрице_без_исхода_валит_отчёт():
    """Д1/К3: тест в матрице есть, исхода в junit нет — отчёт не собирается."""
    rows = [{"id": "FR-A1", "text": "Доступ владельца", "priority": "P0",
             "status": "готово", "confirm": "код", "line": 1}]
    matrix = {"FR-A1": [{"file": "tests/unit/test_files.py", "test": "test_access"}]}
    requirements, unmatched = smoke_report.build_requirements(
        rows, matrix, {}, [], [], "2026-11-08")
    assert requirements[0]["matrix_tests"] == 1
    assert requirements[0]["matrix_measured"] == 0
    assert unmatched == [{"id": "FR-A1", "file": "tests/unit/test_files.py",
                          "test": "test_access"}]
    report = {"version": 1, "commit": "x", "date": "2026-11-08", "env": "стенд",
              "dump_sha256": "x", "layers": {}, "scenarios": [], "cleanup": {}, "defects": [],
              "requirements": requirements, "unmatched_tests": unmatched}
    problems = smoke_report.validate(report)
    assert any("исхода в junit нет" in item for item in problems)
    assert any("«нет автотеста» при наличии теста" in item for item in problems)


def test_провал_теста_сценария_не_красит_строку_но_виден():
    """146 §2.5/§5: провал теста сценария виден в доказательстве, строка остаётся доказанной.

    Тест `tests/smoke/test_cm02_availability.py` падает на шаге разбора даты (дефект FR-C2,
    карта `t_fd553b1b`), но требования файла проверены шагами сценария. Если выкинуть
    доказательство `тест:` совсем, требование с тестом в матрице выглядит как «нет теста».
    """
    rows = [{"id": "FR-C4", "text": "Перезапись с аудитом", "priority": "P1",
             "status": "частично", "confirm": "код", "line": 70}]
    matrix = {"FR-C4": [{"file": "tests/smoke/test_cm02_availability.py",
                         "test": "test_cm02_availability"}]}
    outcomes = {("tests/smoke/test_cm02_availability.py", "test_cm02_availability"):
                {"result": "провал", "message": "boom"}}
    steps = [{"scenario": "СМ-2", "title": "", "roles": [], "domain": "", "step": "4. commit",
              "expected": "200", "actual": "200", "evidence": "x", "result": "пройдено",
              "cause": "", "requirements": ["FR-C4"], "severity": "важно"}]
    requirements, _ = smoke_report.build_requirements(rows, matrix, outcomes, steps, [],
                                                      "2026-11-08")
    assert requirements[0]["result"] == "пройдено"
    assert any("провал сценария" in item for item in requirements[0]["evidence"])


def test_дефект_по_тесту_не_размножается_на_строки_файла():
    """Д-02/146 §5: провал теста сценария даёт дефект шага, а не восемь дефектов FR.

    Провал `test_cm02_availability` относится к своему шагу (FR-C2, карта `t_fd553b1b`);
    остальные требования файла, доказанные шагами, не становятся дефектами.
    """
    rows = [{"id": "FR-C4", "text": "Перезапись с аудитом", "priority": "P1",
             "status": "частично", "confirm": "код", "line": 70},
            {"id": "FR-A1", "text": "Доступ владельца", "priority": "P0",
             "status": "готово", "confirm": "код", "line": 1},
            {"id": "FR-B1", "text": "Имя теста со словом провал", "priority": "P0",
             "status": "готово", "confirm": "код", "line": 2}]
    matrix = {"FR-C4": [{"file": "tests/smoke/test_cm02_availability.py",
                         "test": "test_cm02_availability"}],
              "FR-A1": [{"file": "tests/unit/test_rules.py", "test": "test_access"}],
              "FR-B1": [{"file": "tests/unit/test_rules.py", "test": "test_провал_не_страшен"}]}
    outcomes = {("tests/smoke/test_cm02_availability.py", "test_cm02_availability"):
                {"result": "провал", "message": "boom"},
                ("tests/unit/test_rules.py", "test_access"):
                {"result": "провал", "message": "boom"},
                ("tests/unit/test_rules.py", "test_провал_не_страшен"):
                {"result": "пройдено", "message": ""}}
    steps = [{"scenario": "СМ-2", "title": "", "roles": [], "domain": "", "step": "4. commit",
              "expected": "200", "actual": "200", "evidence": "x", "result": "пройдено",
              "cause": "", "requirements": ["FR-C4"], "severity": "важно"}]
    requirements, _ = smoke_report.build_requirements(rows, matrix, outcomes, steps, [],
                                                      "2026-11-08")
    defects = smoke_report.build_defects(steps, requirements, outcomes, matrix)
    assert [defect["requirement"] for defect in defects] == ["FR-A1"]


def _cm07_rows() -> list[dict]:
    """Строка FR-F5 документа 19: удаление в ней есть, но правила — документа 86."""
    return [{"id": "FR-F5", "text": "Файлы студии: загрузка, удаление, доступ по праву",
             "priority": "P1", "status": "частично", "confirm": "код", "line": 122}]


def _cm07_steps(result: str = "провал") -> list[dict]:
    """Шаги СМ-7: загрузка и доступ по праву доказаны, удаление — требование документа 86."""
    base = {"scenario": "СМ-7", "title": "", "roles": [], "domain": "", "cause": "",
            "severity": "важно"}
    return [
        {**base, "step": "3. POST /files/upload", "expected": "200", "actual": "200",
         "evidence": "x", "result": "пройдено", "requirements": ["FR-F5"]},
        {**base, "step": "6. удаление файла (DELETE /files/{id})",
         "expected": "код 200 и файл больше не отдаётся",
         "actual": "удаление 405, повторное чтение 200", "evidence": "x", "result": result,
         "requirements": ["FR-M8", "FR-K5", "FR-L8"]},
    ]


def _cm07_report(**overrides) -> dict:
    """Отчёт по СМ-7: провал удаления — требование документа 86, а не строка FR-F5."""
    rows = _cm07_rows()
    matrix = {"FR-F5": [{"file": "tests/smoke/test_cm07_files.py",
                         "test": "test_cm07_files"}]}
    outcomes = {("tests/smoke/test_cm07_files.py", "test_cm07_files"):
                {"result": "провал", "message": "boom"}}
    steps = _cm07_steps()
    requirements, _ = smoke_report.build_requirements(rows, matrix, outcomes, steps, [],
                                                      "2026-11-08")
    defects = smoke_report.build_defects(steps, requirements, outcomes, matrix)
    return _minimal_report(requirements=requirements, scenarios=steps, defects=defects,
                           **overrides)


def test_провал_требования_документа_86_не_красит_строку_19():
    """146 §5/Д-02: провал удаления (FR-M8/FR-K5/FR-L8, `docs/86`) — дефект с картой.

    Строка `FR-F5` документа 19 больше не провал: удаление — правило документа 86, в силе
    по ответу V-045, и закрывается картой `t_d4350819`. Провал по чужому документу в
    `defects[]` называет полный ID, документ-источник и карту, а не красит строку 19.
    """
    report = _cm07_report()
    assert [row["result"] for row in report["requirements"]] == ["пройдено"]
    assert len(report["defects"]) == 1, "один проваленный шаг — один дефект, а не три"
    defect = report["defects"][0]
    assert defect["requirement"] == "FR-M8, FR-K5, FR-L8"
    assert defect["document"] == "docs/development/86"
    assert defect["card"] == "t_d4350819"
    assert defect["scenario"] == "СМ-7"
    problems = smoke_report.validate(report)
    assert not any("карты-держателя" in item for item in problems)
    md = smoke_report.render_markdown(report)
    assert (f"| {defect['id']} | FR-M8, FR-K5, FR-L8 | docs/development/86 | СМ-7 |"
            in md)
    assert "закрывается картой `t_d4350819`" in md
    assert "требование в силе (V-045, ответ владельца 08.11.2026)" in md


def test_дефект_вне_документа_19_без_карты_валит_проверку():
    """146 §5/Д-02: `--check` на дефекте вне документа 19 без карты — код 1.

    Дефект по требованию другого документа без карты-держателя повисает без ответственного,
    поэтому это ошибка отчёта, а не свободная строка. Неперечисленное требование вне
    документа 19 — такая же ошибка: у него нет ни источника, ни карты.
    """
    defect = {"id": "Д-02", "requirement": "FR-M8", "requirement_ids": ["FR-M8"],
              "document": "docs/development/86", "card": "", "scenario": "СМ-7",
              "step": "6. удаление файла", "expected": "код 200", "actual": "405",
              "evidence": "x", "severity": "важно", "decision": ""}
    problems = smoke_report.validate(_minimal_report(defects=[defect]))
    assert any("Д-02: требование FR-M8 вне документа 19 без карты-держателя" in item
               for item in problems)
    undeclared = {**defect, "requirement": "FR-Z9", "requirement_ids": ["FR-Z9"],
                  "card": "t_x", "document": ""}
    problems = smoke_report.validate(_minimal_report(defects=[undeclared]))
    assert any("FR-Z9 нет ни в документе 19" in item for item in problems)
    # требование документа 19 карты не требует: его держит строка матрицы
    inside = {**defect, "requirement": "FR-C2", "requirement_ids": ["FR-C2"],
              "document": "docs/development/19-functional-requirements.md"}
    problems = smoke_report.validate(_minimal_report(defects=[inside]))
    assert not any("карты-держателя" in item for item in problems)


def _p1_rows(passed: int) -> list[dict]:
    """34 строки P1: `passed` из них с доказательством, остальные — без автотеста."""
    return [{"id": f"FR-P{i}", "priority": "P1",
             "result": "пройдено" if i < passed else "не проверено",
             "cause": "" if i < passed else "нет автотеста"} for i in range(34)]


def test_порог_p1_27_из_34_по_к17():
    """К17: порог P1 — 80 % от числа строк (27 из 34); 27 проходит, 26 — нет."""
    steps = [{"scenario": f"СМ-{number}", "result": "пройдено"} for number in range(1, 13)]
    cleanup = {"counts": {}, "month_total_restored": True,
               "messages_to_people": 0, "prod_writes": 0}

    def p1_check(passed: int) -> dict:
        block = smoke_report.thresholds(_p1_rows(passed), steps, [], cleanup)
        return next(item for item in block["checks"] if item["criterion"].startswith("P1"))

    enough, not_enough = p1_check(27), p1_check(26)
    assert enough["ok"] is True
    assert enough["value"] == "27/34"
    assert enough["target"] == ">= 27/34"
    assert not_enough["ok"] is False
    # знаменатель порога и факта один и тот же (Д3)
    assert not_enough["target"] == ">= 27/34"
    assert not_enough["value"] == "26/34"
    # порог считается из числа строк, а не из литерала в дизайне
    assert smoke_report.share_target(_p1_rows(0), smoke_report.P1_SHARE) == 27
    # формула ⌊0.8 × N⌋ едет за знаменателем: 40 P1 → 32 (146 §3.4)
    assert smoke_report.share_target([{}] * 40, smoke_report.P1_SHARE) == 32


def test_источник_чисел_из_документа_19():
    """Д3/146 §4.1: числа §7 печатаются из парсера и пересчитываются тем же парсером.

    Отчёт 145 печатал «P0 47/48»: знаменатель порога и знаменатель факта были разные,
    потому что 47 жило в дизайне прозой. Теперь числа берутся из `requirement_rows`
    и появляются в отчёте строкой «источник», а `--check` сверяет её повторным разбором.
    """
    rows = test_audit.requirement_rows()
    matrix = test_audit.matrix()
    block = smoke_report.source_block(rows, matrix)
    assert block["rows"] == 113
    assert (block["p0"], block["p1"], block["p2"], block["unset"]) == (50, 49, 14, 0)
    assert block["with_tests"] == 59
    assert block["unique_pairs"] == len(smoke_report.test_pairs(matrix))
    assert smoke_report.requirements_revision() == "04.11.2026"
    report = _minimal_report(source=block)
    assert smoke_report.source_problems(report) == []
    report["source"]["p0"] = 49
    assert any("p0" in item and "50" in item for item in smoke_report.source_problems(report))
    # счётчики матрицы — факт дерева прогона: новый тест в main их меняет, и исторический
    # отчёт не обязан совпадать с текущим деревом (сведение: 783 → 795 ссылок)
    report = _minimal_report(source=smoke_report.source_block(rows, matrix))
    report["source"]["test_refs"] = -1
    assert smoke_report.source_problems(report) == []
    assert smoke_report.source_problems(_minimal_report(source={}))


def test_коммит_без_генератора_валит_сборку():
    """Ш-06/Д4: отчёт не воспроизводится из коммита без генератора — ошибка входа.

    Коммит без генератора — пустое дерево git (id постоянен и есть в любом репозитории).
    Корневой коммит для этого не годится: в CI клон поверхностный, корень = HEAD, а в нём
    генератор уже есть, и проверка не срабатывала (`DID NOT RAISE InputError` в job tests).
    """
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    with pytest.raises(smoke_report.InputError):
        smoke_report.ensure_generator(empty_tree)
    with pytest.raises(smoke_report.InputError):
        smoke_report.ensure_generator("")


def test_слои_печатаются_строкой_на_слой():
    """146 §2.2: в отчёте видно каждый junit — файл и объём, а не общий счётчик."""
    md = smoke_report.render_markdown(_minimal_report())
    assert "| А. Авто (core: tests/ без браузера и стенда) |" in md
    assert "`junit-core.xml`: 2300 тестов" in md
    assert "Слой А: 3/3 пар «файл::тест» матрицы имеют исход в junit." in md
    assert "Источник чисел §7:" in md


def test_знаменатель_p0_один_в_факте_и_пороге():
    """Д3: у P0 знаменатель «Факт» = «Порог» = числу строк документа 19."""
    rows = [{"id": "FR-A1", "priority": "P0", "result": "пройдено", "cause": ""},
            {"id": "FR-A2", "priority": "P0", "result": "не проверено", "cause": "нет данных в копии"},
            {"id": "FR-A3", "priority": "не задан", "result": "не проверено", "cause": "нет автотеста"}]
    block = smoke_report.thresholds(rows, [], [], {"counts": {}, "month_total_restored": True})
    p0 = next(item for item in block["checks"] if item["criterion"].startswith("P0"))
    assert p0["value"] == "2/2"
    assert p0["target"] == "2/2"
    assert p0["ok"] is True


def _minimal_report(**overrides) -> dict:
    """Каркас отчёта для проверки раздела и валидации: строки матрицы здесь не нужны."""
    report = {
        "version": 1, "number": "145", "commit": "x", "date": "2026-11-08",
        "env": "стенд", "marker": "SMOKE-20261108-01", "dump_sha256": "x",
        "source": {"document": "docs/development/19-functional-requirements.md",
                   "revision": "04.11.2026", "rows": 113, "p0": 48, "p1": 34, "p2": 7,
                   "unset": 24, "with_tests": 59, "test_refs": 764, "unique_pairs": 477,
                   "command": "…"},
        "layers": {"junit": [{"layer": "core", "file": "junit-core.xml", "tests": 2300,
                              "passed": 2200, "failed": 0, "skipped": 100}],
                   "gates": [], "prod": {"executed": False}},
        "coverage": {"pairs": 3, "covered": 3, "missing": []},
        "requirements": [], "scenarios": [], "cleanup": {}, "defects": [], "risk": [],
        "delta": {"note": "первый прогон, дельты нет", "added": [], "changed": [], "removed": []},
        "thresholds": {"checks": [], "ok": True, "not_measured": []},
        "summary": "…", "run_checks": [], "unmatched_tests": [],
    }
    report.update(overrides)
    return report


def _k5_k7() -> list[dict]:
    """Проверки прогона в том виде, в каком их отдаёт `run_checks`: К5 пройдена, К7 — провал."""
    return smoke_report.run_checks({"checks": [
        {"id": "К5", "title": "prepare при остатках прошлого прогона", "expected": "код 1",
         "actual": "код 1, остаток перечислен, строка на месте", "evidence": "k5-prepare.log",
         "result": "пройдено"},
        {"id": "К7", "title": "два прогона в один день", "expected": "оба прогона зелены",
         "actual": "1 failed, 11 passed; итоги и счётчики совпали", "evidence": "marker-02/pytest.log",
         "result": "провал", "defect": "FR-F5 (удаление файла), карта t_61ec7e55"}]})


def test_проверки_прогона_к5_к7_в_отчёте():
    """Д5/К5/К7: проверки попадают в отчёт машинно, провал К7 остаётся провалом."""
    checks = _k5_k7()
    assert [check["id"] for check in checks] == ["К5", "К7"]
    md = smoke_report.render_markdown(_minimal_report(run_checks=checks))
    assert "## 5. Проверки прогона (К5, К7)" in md
    assert "| К5 |" in md and "| К7 |" in md
    assert "К7: FR-F5 (удаление файла), карта t_61ec7e55" in md
    assert "Проверки прогона: К5 пройдено; К7 провал." in smoke_report.summary(
        [], {"checks": [], "ok": True}, checks)


def test_проверки_прогона_обязательны_в_отчёте():
    """Д5: отчёт без проверок прогона не собирается — молчание вместо факта недопустимо."""
    empty = smoke_report.validate(_minimal_report())
    assert any("run_checks пуст" in item for item in empty)
    missing = _minimal_report()
    del missing["run_checks"]
    assert any("нет ключа run_checks" in item for item in smoke_report.validate(missing))
    # «не проверено» с причиной из §3.3 — допустимый исход, а не ошибка
    unchecked = smoke_report.run_checks({"checks": [
        {"id": "К7", "title": "два прогона в один день", "expected": "оба зелены",
         "actual": "не снимали", "evidence": "—", "result": "не проверено",
         "cause": "нет данных в копии"}]})
    problems = smoke_report.validate(_minimal_report(run_checks=unchecked))
    assert not any(item.startswith("проверка К7") for item in problems)


def test_провал_проверки_без_дефекта_валит_отчёт():
    """Д5: провал проверки без требования и карты — ошибка отчёта, а не свободный текст."""
    checks = smoke_report.run_checks({"checks": [
        {"id": "К7", "title": "два прогона в один день", "expected": "оба зелены",
         "actual": "1 failed", "evidence": "marker-02/pytest.log", "result": "провал"}]})
    problems = smoke_report.validate(_minimal_report(run_checks=checks))
    assert any("проверка К7: провал без ссылки на дефект" in item for item in problems)
