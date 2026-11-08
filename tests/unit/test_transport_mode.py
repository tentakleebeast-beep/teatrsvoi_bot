"""R-3: транспорт Telegram — только polling, переменной MODE нет.

Обоснование (Ш-51): файл создан в 7ec011e R-3: релиз на polling, переменная MODE убрана из .env.example и runbook (INT-003)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
RUNBOOK = ROOT / "docs" / "infrastructure" / "35-deploy-runbook.md"


def env_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_has_no_mode() -> None:
    keys = env_keys(ENV_EXAMPLE.read_text(encoding="utf-8"))
    assert "MODE" not in keys
    # обязательный минимум контура остаётся на месте
    assert {"ENV", "TZ_OFFSET_HOURS", "TELEGRAM_TOKEN", "DATABASE_URL"} <= keys


def test_code_does_not_read_mode() -> None:
    pattern = re.compile(r"""getenv\(\s*["']MODE["']|environ\[["']MODE["']\]""")
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "services").rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_runbook_explains_polling_and_conflict() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "## 5b. Почему polling" in text
    assert "409 Conflict" in text
    assert not re.search(r"^MODE\s*=", text, re.MULTILINE)


def test_m1_адрес_mini_app_есть_в_контракте_окружения() -> None:
    """M-1: переменная без строки в .env.example разъезжается молча (docs/75 §5.3)."""
    keys = env_keys(ENV_EXAMPLE.read_text(encoding="utf-8"))
    assert "WEBAPP_BASE_URL" in keys
    assert "WEBAPP_SECRET" in keys
