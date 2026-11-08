"""Публичный репозиторий — единственный: CI и ручные прод-операции живут в нём.

Обоснование: docs/development/26 §10 — после сведения к одному публичному репозиторию
(08.11.2026) ручные прод-операции (SSH к /opt/svoi через `secrets.DEPLOY_SSH_KEY`)
живут в публичном репозитории. Автодеплоя нет: все прод-workflow запускаются только
`workflow_dispatch`. Секретов в файлах нет, они заданы в настройках репозитория.

Проверка держит два правила:
1. список workflow в `.github/workflows` — ровно ожидаемый;
2. ни один workflow с секретами не стартует по `pull_request` (иначе код из форка
   получит доступ к секретам на публичном репозитории).
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLIC_ALLOWED = [
    "backup-timer.yml",
    "deploy.yml",
    "repair-schema.yml",
    "set-env-value.yml",
    "set-llm-key.yml",
    "tests.yml",
]


def test_публичное_дерево_содержит_только_разрешённые_workflow():
    """Публичный срез: `tests.yml` плюс пять ручных прод-операций."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        pytest.skip("вне CI: какой репозиторий — из дерева не видно")
    names = sorted(path.name for path in WORKFLOWS.glob("*.yml"))
    assert names == PUBLIC_ALLOWED, (
        "в .github/workflows неожиданные файлы: "
        f"{sorted(set(names) - set(PUBLIC_ALLOWED))} — ожидался ровно {PUBLIC_ALLOWED} "
        "(docs/development/26 §10)"
    )


def test_workflow_с_секретами_не_стартует_по_pull_request():
    """Прод-workflow только `workflow_dispatch`: иначе секреты доступны из форка."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "secrets." not in text:
            continue
        triggers = text.split("on:", 1)[1].split("jobs:", 1)[0] if "on:" in text else ""
        assert "pull_request" not in triggers, (
            f"{path.name} использует secrets и стартует по pull_request — "
            "на публичном репозитории это исполнение кода из форка с доступом к секретам"
        )
