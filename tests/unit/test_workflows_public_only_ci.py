"""В публичном репозитории .github/workflows — только CI, прод-workflow там нет.

Обоснование: docs/development/26 §10 — ручные прод-операции (SSH к /opt/svoi через
`secrets.DEPLOY_SSH_KEY`) живут в приватном репозитории; в публичном они раскрывают
инфраструктуру. Публикация копирует дерево приватного репозитория, поэтому без этой
проверки файлы вернулись бы в публичный молча.

Репозиторий опознаётся по `GITHUB_REPOSITORY`: в приватном прод-workflow законны, поэтому
там проверка пропускается; вне CI (локально, где remote'ов несколько) — тоже.
"""

from __future__ import annotations

import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLIC_ALLOWED = ["tests.yml"]


def test_публичное_дерево_содержит_только_ci():
    """Публичный срез: один workflow — `tests.yml`; остальные живут в приватном репозитории."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        pytest.skip("вне CI: какой репозиторий — из дерева не видно")
    if repo.endswith("-private"):
        pytest.skip("приватный репозиторий: прод-workflow здесь и живут")
    names = sorted(path.name for path in WORKFLOWS.glob("*.yml"))
    assert names == PUBLIC_ALLOWED, (
        "в публичном .github/workflows лишние файлы: "
        f"{sorted(set(names) - set(PUBLIC_ALLOWED))} — прод-workflow живут в приватном "
        "репозитории (docs/development/26 §10)"
    )
