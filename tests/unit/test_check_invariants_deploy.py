"""Запрет автодеплоя держится проверкой, а не комментарием в workflow.

Обоснование: docs/development/26 §6 — прод обновляется только по команде владельца;
`deploy.yml` запускается только руками (`workflow_dispatch`), а cron не ходит на сервер.
Проверка статическая: тест работает на временном каталоге и не подключается к базе
(`scripts/check_invariants.py --static`).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_invariants.py"

WORKFLOW = """name: деплой на прод

on:
  workflow_dispatch:
    inputs:
      tag:
        required: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: echo deploy
"""


def _root(tmp_path: pathlib.Path, workflow: str = WORKFLOW) -> pathlib.Path:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "deploy.yml").write_text(workflow, encoding="utf-8")
    (root / "scripts" / "cron").mkdir(parents=True)
    (root / "scripts" / "cron" / "digest.sh").write_text(
        "#!/usr/bin/env bash\npython scripts/feed_digest.py\n", encoding="utf-8")
    return root


def _run(root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--static", "--root", str(root)],
        capture_output=True, text=True,
    )


def test_ручной_выкат_проходит(tmp_path):
    """Только workflow_dispatch — строка `OK   деплой`, код 0, база не нужна."""
    result = _run(_root(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK   деплой" in result.stdout
    assert "OK   cron" in result.stdout


def test_без_deploy_yml_проверка_проходит(tmp_path):
    """Публичное дерево без прод-workflow — не провал: автодеплою неоткуда взяться.

    Прод-workflow живут только в приватном репозитории (docs/development/26 §10),
    поэтому отсутствие `deploy.yml` — ожидаемое состояние публичного среза.
    """
    root = _root(tmp_path)
    (root / ".github" / "workflows" / "deploy.yml").unlink()

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK   деплой" in result.stdout


def test_автодеплой_по_push_падает(tmp_path):
    """Добавленный кем-то `push:` в `on:` — `FAIL деплой` и код 1."""
    workflow = WORKFLOW.replace("on:\n  workflow_dispatch:", "on:\n  push:\n  workflow_dispatch:")
    result = _run(_root(tmp_path, workflow))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL деплой" in result.stdout
    assert "push" in result.stdout


def test_расписание_тоже_автодеплой(tmp_path):
    """`schedule:` выкатил бы прод без команды владельца — тоже запрещён."""
    workflow = WORKFLOW.replace("on:\n  workflow_dispatch:", "on:\n  schedule:\n  workflow_dispatch:")
    result = _run(_root(tmp_path, workflow))

    assert result.returncode == 1
    assert "FAIL деплой" in result.stdout


def test_строка_про_выкат_в_cron_падает(tmp_path):
    """cron с ssh/systemctl — обход ручного workflow, отдельная строка проверки."""
    root = _root(tmp_path)
    (root / "scripts" / "cron" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\nssh root@203.0.113.10 'systemctl restart svoi-tool-api'\n",
        encoding="utf-8",
    )

    result = _run(root)

    assert result.returncode == 1
    assert "FAIL cron" in result.stdout
