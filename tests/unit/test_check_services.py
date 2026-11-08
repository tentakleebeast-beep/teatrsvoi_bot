"""Проверка unit-файлов планировщика (C-11, docs/infrastructure/35)."""

from __future__ import annotations

import shutil
from pathlib import Path

from scripts.check_services import PROJECT, REQUIRED, check_dir

UNITS = PROJECT / "deploy" / "systemd"


def units_copy(tmp_path: Path) -> Path:
    target = tmp_path / "systemd"
    target.mkdir()
    for unit in UNITS.glob("*"):
        shutil.copy(unit, target / unit.name)
    return target


def test_юниты_репозитория_в_порядке():
    report = check_dir(UNITS)
    assert report.ok, report.errors
    assert set(REQUIRED) <= set(report.checked)


def test_таймер_без_сервиса_ошибка(tmp_path):
    directory = units_copy(tmp_path)
    (directory / "svoi-notify-send.service").unlink()
    report = check_dir(directory)
    assert not report.ok
    assert any("svoi-notify-send.timer" in error and "парного" in error
               for error in report.errors)


def test_нет_обязательного_юнита(tmp_path):
    directory = units_copy(tmp_path)
    (directory / "svoi-watchdog.timer").unlink()
    report = check_dir(directory)
    assert any("нет обязательного юнита svoi-watchdog.timer" in error
               for error in report.errors)


def test_execstart_ссылается_на_пропавший_скрипт(tmp_path):
    directory = units_copy(tmp_path)
    unit = directory / "svoi-invariants.service"
    text = unit.read_text(encoding="utf-8").replace(
        "scripts/check_invariants.py", "scripts/no_such_script.py"
    )
    unit.write_text(text, encoding="utf-8")
    report = check_dir(directory)
    assert any("no_such_script.py" in error for error in report.errors)


def test_сервис_без_execstart(tmp_path):
    directory = units_copy(tmp_path)
    unit = directory / "svoi-watchdog.service"
    unit.write_text(
        "[Unit]\nDescription=x\n\n[Service]\nWorkingDirectory=/opt/svoi\n"
        "EnvironmentFile=/opt/svoi/.env\n",
        encoding="utf-8",
    )
    report = check_dir(directory)
    assert any("svoi-watchdog.service" in error and "ExecStart" in error
               for error in report.errors)


def test_таймер_без_oncalendar(tmp_path):
    directory = units_copy(tmp_path)
    unit = directory / "svoi-notify-plan.timer"
    unit.write_text("[Unit]\nDescription=x\n\n[Timer]\nPersistent=true\n", encoding="utf-8")
    report = check_dir(directory)
    assert any("OnCalendar" in error for error in report.errors)
