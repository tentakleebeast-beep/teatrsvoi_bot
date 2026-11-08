"""CI собирает и запускает образы, ручной выкат проверяется одной командой (R-5).

Обоснование (Ш-51): файл создан в 876cbb4 R-5: job images собирает и запускает оба сервиса, deploy-check.sh для выката (INT-003)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[2]
WORKFLOW = PROJECT / ".github" / "workflows" / "tests.yml"
SCRIPT = PROJECT / "scripts" / "deploy-check.sh"
RUNBOOK = PROJECT / "docs" / "infrastructure" / "35-deploy-runbook.md"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def step_text(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_job_images_собирает_оба_сервиса():
    job = workflow()["jobs"]["images"]
    text = step_text(job)
    assert "services/tool-api/Dockerfile" in text
    assert "services/tg-gateway/Dockerfile" in text
    assert "docker build" in text


def test_job_images_поднимает_api_с_postgres():
    text = step_text(workflow()["jobs"]["images"])
    # схема накатывается Alembic (как на проде), а не initdb: база без alembic_version
    # не даёт приложению стартовать (docs/reports/132, шаг 7)
    assert "alembic -c alembic.ini upgrade head" in text
    assert "docker-entrypoint-initdb.d" not in text
    # накат идёт до старта api-ci, иначе сервис не увидит схему
    assert text.index("upgrade head") < text.index("--name api-ci")
    assert "postgres:16" in text
    assert "/health" in text
    # smoke — не только /health: авторизованный запрос к данным
    assert "/tools/categories" in text
    assert "Authorization: Bearer" in text


def test_deploy_check_скрипт_на_месте_и_без_ошибок_синтаксиса():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "deploy-check.sh должен быть исполняемым"
    bash = shutil.which("bash")
    assert bash, "нет bash"
    done = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_deploy_check_проверяет_версию_gateway_и_юниты():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "health_agent.py" in text
    assert "tg-gateway" in text
    assert "check_services.py" in text
    assert "exit 1" in text


def test_runbook_описывает_проверку_выката():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "### 10a." in text
    assert "deploy-check.sh" in text
    assert "job `images`" in text
