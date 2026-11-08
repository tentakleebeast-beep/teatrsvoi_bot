"""Обёртки заданий планировщика лежат в репозитории (docs/infrastructure/36 §5).

Раньше логика жила в ~/.hermes/scripts вне git: правки не попадали в коммиты,
в CI не проверялись и легко снова получали старый путь проекта. Теперь файлы
лежат в scripts/cron, а дома остаётся тонкий лаунчер, который их запускает.
Тест проверяет только файлы репозитория и не пропускается в CI.
"""

import importlib.util
import types
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
CRON = PROJECT / "scripts" / "cron"
WRAPPERS = ("notify.py", "purge_intent.py")


def load(name):
    path = CRON / name
    spec = importlib.util.spec_from_file_location(f"cron_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", WRAPPERS)
def test_wrapper_lives_in_repository(name):
    path = CRON / name
    assert path.is_file(), f"нет файла {path}"
    text = path.read_text(encoding="utf-8")
    assert "Documents/Projects" not in text
    assert "SVOI_PROJECT" in text


@pytest.mark.parametrize("name", WRAPPERS)
def test_wrapper_finds_project_without_env(monkeypatch, name):
    monkeypatch.delenv("SVOI_PROJECT", raising=False)
    assert load(name).PROJECT == PROJECT


def test_purge_wrapper_passes_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TOKEN=abc # комментарий\nEMPTY=\n", encoding="utf-8")
    assert load("purge_intent.py").read_env(env_file) == {
        "TELEGRAM_TOKEN": "abc",
        "EMPTY": "",
    }


def result(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_notify_runs_plan_then_send(monkeypatch, capsys):
    module = load("notify.py")
    calls = []

    def run(args):
        calls.append(args[0])
        if args[0] == "scripts/plan_reminders.py":
            return result()
        return result(stdout="отправлено 2, ошибок 0")

    monkeypatch.setattr(module, "run", run)
    assert module.main() == 0
    assert calls == ["scripts/plan_reminders.py", "scripts/send_notifications.py"]
    assert "отправлено 2" in capsys.readouterr().out


def test_notify_silent_when_nothing_to_send(monkeypatch, capsys):
    module = load("notify.py")

    def run(args):
        if args[0] == "scripts/plan_reminders.py":
            return result()
        return result(stdout="отправлено 0, ошибок 0")

    monkeypatch.setattr(module, "run", run)
    assert module.main() == 0
    assert capsys.readouterr().out == ""


def test_notify_stops_when_plan_fails(monkeypatch, capsys):
    module = load("notify.py")
    calls = []

    def run(args):
        calls.append(args[0])
        return result(returncode=1, stderr="нет БД")

    monkeypatch.setattr(module, "run", run)
    assert module.main() == 1
    assert calls == ["scripts/plan_reminders.py"]
    assert "нет БД" in capsys.readouterr().err


def test_purge_reports_failure(monkeypatch, capsys):
    module = load("purge_intent.py")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: result(returncode=1, stderr="нет доступа"),
    )
    assert module.main() == 1
    assert "нет доступа" in capsys.readouterr().err


def test_purge_quiet_when_nothing_to_purge(monkeypatch, capsys):
    module = load("purge_intent.py")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: result())
    assert module.main() == 0
    assert capsys.readouterr().out == ""
