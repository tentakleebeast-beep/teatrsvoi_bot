"""Ш-39: логи сервисов лежат в файлах, а --live замечает пустой лог."""

from pathlib import Path

import gateway_modules

from scripts import check_services


def test_непустой_лог_не_проблема(tmp_path):
    (tmp_path / "gateway.log").write_text("Start polling\n", encoding="utf-8")
    assert check_services.check_logs("gateway", tmp_path) == []


def test_оба_лога_пусты_это_проблема(tmp_path):
    (tmp_path / "gateway.log").write_text("", encoding="utf-8")
    (tmp_path / "gateway.err.log").write_text("", encoding="utf-8")
    problems = check_services.check_logs("gateway", tmp_path)
    assert len(problems) == 1
    assert "gateway.log" in problems[0] and "пусты" in problems[0]


def test_файлов_лога_нет_не_выдумываем(tmp_path):
    assert check_services.check_logs("gateway", tmp_path) == []


def test_сдвинутый_лог_не_считается_текущим(tmp_path):
    (tmp_path / "gateway.log.1").write_text("old\n", encoding="utf-8")
    assert check_services.check_logs("gateway", tmp_path) == []


def test_gateway_пишет_в_stdout():
    source = gateway_modules.source("config")
    assert "logging.basicConfig(level=logging.INFO, stream=sys.stdout" in source
