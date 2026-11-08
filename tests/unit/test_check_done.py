"""Гейт одной командой (docs/development/26 §4, scripts/check_done.py).

Проверяем главное свойство гейта: его нельзя «набрать по частям». Состав шагов
фиксирован, единственное сокращение — браузерные тесты по `--quick`, падение
останавливает прогон и называет шаг, а машинный отчёт несёт шаг/код/секунды.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_done as mod
    yield mod


def make_step(mod, key: str, code: int = 0, text: str = "ok", lines: int = 0) -> object:
    """Шаг-заглушка: печатает текст (и lines строк) и выходит с нужным кодом."""
    body = f"print({text!r})\n"
    if lines:
        body += f"for i in range({lines}): print('строка', i)\n"
    body += f"raise SystemExit({code})"
    return mod.Step(key=key, title=f"заглушка {key}",
                    command=(sys.executable, "-c", body))


def test_полный_гейт_идёт_фиксированным_порядком(gate):
    keys = [step.key for step in gate.gate_steps(python_path=sys.executable)]
    assert keys == ["ruff", "pytest", "webapp", "invariants", "registers", "deadcode",
                    "settings"]


def test_браузерный_шаг_включается_переменной_окружения(gate):
    webapp = gate.gate_steps(python_path=sys.executable)[2]
    assert dict(webapp.env)["SVOI_WEBAPP_TESTS"] == "1"
    assert webapp.command[-3:] == ("tests/webapp", "--browser", "chromium")


def test_quick_убирает_только_браузерный_шаг(gate):
    keys = [step.key for step in gate.gate_steps(quick=True, python_path=sys.executable)]
    assert "webapp" not in keys
    assert keys == ["ruff", "pytest", "invariants", "registers", "deadcode", "settings"]


def test_флага_только_unit_не_существует(gate):
    with pytest.raises(SystemExit) as exc:
        gate.main(["--unit"], steps=[])
    assert exc.value.code == 2


def test_шаг_карт_канбана_только_по_окружению_или_борду(gate, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert "kanban" not in [s.key for s in gate.gate_steps(python_path=sys.executable)]
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_test")
    assert "kanban" in [s.key for s in gate.gate_steps(python_path=sys.executable)]
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    by_board = gate.gate_steps(board="teatrsvoi", python_path=sys.executable)
    assert by_board[-1].key == "kanban"
    assert by_board[-1].command[-2:] == ("--board", "teatrsvoi")


def test_зелёный_прогон_даёт_код_0_и_итог(gate, capsys):
    steps = [make_step(gate, "ruff", text="All checks passed!"),
             make_step(gate, "pytest", text="1475 passed, 287 skipped in 91.20s"),
             make_step(gate, "invariants", text="Все проверки пройдены."),
             make_step(gate, "registers", text="Все проверки пройдены."),
             make_step(gate, "deadcode", text="Находок: 5 (известных 5, новых 0)")]
    assert gate.main([], steps=steps) == 0
    out = capsys.readouterr().out
    assert "ГЕЙТ ЗЕЛЁНЫЙ: ruff чисто, pytest 1475 passed/287 skipped" in out
    assert "инварианты OK" in out and "реестры OK" in out
    assert "deadcode новых 0" in out


def test_падение_останавливает_прогон_и_называет_шаг(gate, capsys):
    steps = [make_step(gate, "ruff"),
             make_step(gate, "pytest", code=1, text="4 failed, 1489 passed"),
             make_step(gate, "invariants")]
    assert gate.main([], steps=steps) == 1
    out = capsys.readouterr().out
    assert "ГЕЙТ КРАСНЫЙ: шаг «pytest» упал, код 1" in out
    assert "invariants" in out and "не запускался" in out


def test_отчёт_несёт_только_выполненные_шаги(gate):
    steps = [make_step(gate, "ruff"),
             make_step(gate, "pytest", code=3),
             make_step(gate, "deadcode")]
    report = gate.run_gate(steps)
    assert [r.key for r in report.steps] == ["ruff", "pytest"]
    assert report.skipped == ["deadcode"]
    assert not report.ok


def test_при_падении_печатается_хвост_вывода(gate, capsys):
    steps = [make_step(gate, "pytest", code=1, text="начало", lines=40)]
    assert gate.main([], steps=steps) == 1
    out = capsys.readouterr().out
    assert "Последние 20 строк" in out
    assert "строка 39" in out
    assert "строка 5" not in out


def test_json_содержит_шаг_код_и_секунды(gate, capsys):
    steps = [make_step(gate, "ruff"), make_step(gate, "pytest", code=2, text="2 failed")]
    assert gate.main(["--json"], steps=steps) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    for item in data["steps"]:
        assert {"step", "code", "seconds"} <= set(item)
        assert isinstance(item["seconds"], float)
    assert [item["step"] for item in data["steps"]] == ["ruff", "pytest"]
    assert data["steps"][1]["code"] == 2
    assert data["skipped"] == []


def test_quick_виден_в_итоговой_строке(gate, capsys):
    assert gate.main(["--quick"], steps=[make_step(gate, "ruff")]) == 0
    out = capsys.readouterr().out
    assert "ГЕЙТ ЗЕЛЁНЫЙ (без браузерных)" in out
    assert "пропущен: --quick" in out


def test_счётчики_pytest_читаются_из_вывода(gate):
    assert gate.parse_pytest("1475 passed, 287 skipped, 2 warnings in 91.20s") == (1475, 287)
    assert gate.parse_pytest("898 passed in 10.1s") == (898, 0)


def test_новые_находки_deadcode_читаются_из_вывода(gate):
    text = "Отсеяно обработчиков: 289\nНаходок: 12 (известных 12, новых 0)"
    assert gate.summarize("deadcode", text, 0) == "deadcode новых 0"
    assert gate.summarize("deadcode", "Находок: 13 (известных 12, новых 1)", 0) == \
        "deadcode новых 1"


def test_подсказка_об_окружении_только_когда_базы_нет(gate):
    result = gate.StepResult(key="pytest", title="pytest", code=1, seconds=0.1,
                             output="psycopg.OperationalError: could not connect to server")
    assert "Postgres" in (gate.environment_hint(result) or "")
    plain = gate.StepResult(key="pytest", title="pytest", code=1, seconds=0.1,
                            output="4 failed, 19 errors")
    assert gate.environment_hint(plain) is None


def test_отсутствующий_исполняемый_файл_не_ломает_гейт(gate, tmp_path):
    step = gate.Step(key="ruff", title="ruff",
                     command=(str(tmp_path / "нет-такого"), "check", "."))
    result = gate.run_step(step)
    assert result.code == 127
    assert "не найден исполняемый файл" in result.output
    assert result.summary == ""
