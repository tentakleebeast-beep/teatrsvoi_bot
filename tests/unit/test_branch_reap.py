"""Уборка ветвей: мусор отделяется от живой работы числом патчей, а не датой (docs/development/26 §7a п.8, §7c).

Проверяется решение, а не запуск git: классификация ветвей — чистая функция от
`unique`/`worktree`/`current`. Дефект, который она закрывает: к 15.11.2026 на `origin`
было 68 ветвей, 65 из них влиты, и отличить их от работы можно было только
`git rev-list --count main..branch`, а не по дате или названию.
"""

import importlib.util
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def _load():
    path = PROJECT / "scripts" / "branch_reap.py"
    spec = importlib.util.spec_from_file_location("branch_reap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REAP = _load()


def test_влитая_ветвь_и_живая_различаются_числом_патчей():
    merged = REAP.Branch("wt/old", unique=0, scope="remote")
    alive = REAP.Branch("wt/active", unique=3, scope="local")
    buckets = REAP.classify([merged, alive])
    assert [b.name for b in buckets["merged"]] == ["wt/old"]
    assert [b.name for b in buckets["alive"]] == ["wt/active"]
    assert buckets["protected"] == []


def test_ветвь_под_worktree_защищена_даже_при_нуле_патчей():
    """Идущая карта держит worktree: удалять её нельзя, пока работа не закончена."""
    branch = REAP.Branch("wt/t_1d18c635", unique=0, scope="local",
                         worktree="/tmp/repo/.worktrees/t_1d18c635")
    assert branch.protected is True
    assert branch.fate == "protected"
    assert REAP.classify([branch])["merged"] == []


def test_main_и_текущая_ветвь_не_удаляются():
    assert REAP.Branch("main", unique=0, scope="local").fate == "protected"
    assert REAP.Branch("wt/now", unique=0, scope="local", current=True).fate == "protected"


def test_порядок_ветвей_стабилен_по_имени():
    """Отчёт сравнивают глазами между неделями: порядок не должен плавать."""
    names = ["wt/b", "wt/a"]
    buckets = REAP.classify([REAP.Branch(n, unique=0, scope="local") for n in names])
    assert [b.name for b in buckets["merged"]] == ["wt/a", "wt/b"]


def test_удалённый_двойник_ветви_под_worktree_тоже_защищён():
    """У карты в работе есть и локальная ветвь, и push на origin: уборка не должна
    сносить удалённую копию, пока карта не закрыта."""
    remote = REAP.Branch("wt/t_1d18c635", unique=0, scope="remote",
                         worktree="/tmp/repo/.worktrees/t_1d18c635")
    buckets = REAP.classify([remote])
    assert buckets["merged"] == []
    assert [b.scope for b in buckets["protected"]] == ["remote"]
