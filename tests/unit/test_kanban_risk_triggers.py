"""Риск-триггеры worker-test: проверка по риску, а не на каждую карту (docs/development/107).

Правило, которое держит этот файл: диспетчер поднимает тестировщика там, где ревью уже
промахивалось (экраны и статика Mini App, токены и initData, контракт ошибок), и не
поднимает на картах, которые этих мест не трогают. Иначе «тестировщик по риску»
превращается в «тестировщик на каждую карту» и удваивает прогоны.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script():
    path = ROOT / "scripts" / "kanban_risk_route.py"
    spec = importlib.util.spec_from_file_location("kanban_risk_route", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Модуль обязан быть в sys.modules: dataclass сверяется с ним при разборе типов.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RISK = _script()


def test_конфиг_триггеров_читается():
    rules = RISK.Rules.load()
    assert rules.assignee == "worker-test"
    assert rules.paths, "в config/kanban-risk-triggers.yaml пусто"


@pytest.mark.parametrize("changed", [
    "services/tool-api/app/static/webapp-ui.css",
    "services/tool-api/app/static/webapp-shell.js",
    "services/tool-api/app/pages/home.py",
    "services/tool-api/app/webapp_page.py",
    "services/tool-api/app/api/webapp_api.py",
    "services/tool-api/app/webapp.py",
    "services/tool-api/app/errors.py",
    # Карта часто называет область коротко: «app/static», «app/pages».
    "app/static/webapp-ui.css",
    "app/pages/money.py",
])
def test_правки_mini_app_поднимают_тестировщика(changed):
    decision = RISK.decide(RISK.Rules.load(), "карта", "Область: что-то", files=[changed])
    assert decision.triggered, f"{changed} должен поднимать worker-test"
    assert decision.assignee == "worker-test"


@pytest.mark.parametrize("changed", [
    "scripts/build_reports.py",
    "scripts/kanban_metrics.py",
    "db/init/070_notes.sql",
    "docs/development/26-process-and-git.md",
    "tests/unit/test_rules.py",
])
def test_чужой_код_тестировщика_не_поднимает(changed):
    decision = RISK.decide(RISK.Rules.load(), "карта", "Область: отчёты и миграции",
                           files=[changed])
    assert not decision.triggered, f"{changed} не должен поднимать worker-test"


def test_бюджет_токенов_llm_не_повод():
    """Голое «токен» ловило карты про лимиты модели — фразы в конфиге это чинят."""
    rules = RISK.Rules.load()
    body = "Область: services/tool-api/app/llm.py. Бюджет токенов: лимит на чат, счётчик."
    assert not RISK.decide(rules, "Модели и бюджеты", body).triggered


def test_обычная_карта_по_тексту_не_поднимается():
    decision = RISK.decide(RISK.Rules.load(), "Отчёт по бюджету за октябрь",
                           "Область: scripts/build_reports.py. Свести суммы по статьям.")
    assert not decision.triggered


def test_пути_из_описания_карты_читаются():
    body = ("## Область\nservices/tool-api/app/static/**\n"
            "Ссылка на docs/reports/104-test-audit.md — не ассет.\n")
    found = RISK.paths_from_text(body)
    assert "services/tool-api/app/static/**" in found
    assert all("/" in path for path in found)


def test_glob_понимает_двойную_звёздочку():
    assert RISK.glob_match("app/static/**", "app/static/x/y.css")
    assert RISK.glob_match("app/static/**", "app/static")
    assert not RISK.glob_match("app/static/**", "app/pages/home.py")


def test_сокращение_образца_не_превращает_чужое_в_риск():
    """`tests/webapp/**` не должен ловить `webapp/admin`, а статика гейтвея — не наша."""
    assert RISK._variants("services/tool-api/app/static/**") == [
        "services/tool-api/app/static/**", "tool-api/app/static/**", "app/static/**"]
    assert RISK.path_hits("app/static/webapp-ui.css", "services/tool-api/app/static/**")
    assert not RISK.path_hits("webapp/admin", "tests/webapp/**")
    assert not RISK.path_hits("services/tg-gateway/app/static/x.css",
                              "services/tool-api/app/static/**")


def test_карта_из_базы_и_решение_по_ней(tmp_path, monkeypatch):
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute("create table tasks (id text primary key, title text not null, body text, "
                 "assignee text, status text not null, created_at integer not null)")
    conn.execute("insert into tasks values (?,?,?,?,?,?)",
                 ("t_risk", "Mini App: статика отдаёт 500",
                  "Область: services/tool-api/app/static/**", "worker-feature", "ready", 0))
    conn.execute("insert into tasks values (?,?,?,?,?,?)",
                 ("t_calm", "Отчёт по бюджету", "Область: scripts/build_reports.py",
                  "worker-feature", "ready", 1))
    conn.commit()
    conn.close()

    risk = RISK.read_task(db, "t_risk")
    calm = RISK.read_task(db, "t_calm")
    rules = RISK.Rules.load()
    assert RISK.decide(rules, risk["title"], risk["body"]).triggered
    assert not RISK.decide(rules, calm["title"], calm["body"]).triggered
    assert [t["id"] for t in RISK.open_tasks(db)] == ["t_risk", "t_calm"]


def test_карта_проверки_несёт_четыре_обязанности(tmp_path):
    rules = RISK.Rules.load()
    parent = {"id": "t_parent", "title": "Mini App: экран «Время»",
              "body": "Область: app/static/**"}
    decision = RISK.decide(rules, parent["title"], parent["body"])
    body = RISK.check_body(decision, parent, rules)

    assert "--browser chromium" in body and "--browser webkit" in body, "п.1: два движка"
    assert "test_static_contract.py" in body, "п.2: отдача ассетов"
    assert "репро-тест" in body, "п.3: репро в tests/"
    assert "Не закрывать карту без репро" in body, "п.4: запрет закрытия"
    assert "worker-test" in body


def test_нет_триггеров_код_выхода_единица(monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv",
                        ["kanban_risk_route.py", "--files", "scripts/build_reports.py"])
    assert RISK.main() == 1
    assert "не сработал" in capfd.readouterr().out


def test_есть_триггер_код_выхода_ноль(monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv",
                        ["kanban_risk_route.py", "--files",
                         "services/tool-api/app/static/webapp-ui.css"])
    assert RISK.main() == 0
    assert "СРАБОТАЛ" in capfd.readouterr().out
