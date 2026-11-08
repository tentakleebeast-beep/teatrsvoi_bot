"""Личные идентификаторы не возвращаются в репозиторий (Ш-03 плана docs/plans/129).

Обоснование: docs/plans/129-public-repo-plan.md §Ш-03, решение Ш-00 п.2 (вариант B) —
репозиторий публикуется, поэтому chat_id владельца, адреса сервисного аккаунта и
конкретные id календарей/таблиц живут только в окружении (`ADMIN_CHAT_ID`,
`.env`), а в файлах остаются плейсхолдеры.

Тесты читают файлы репозитория и ищут шаблоны, а не значения: сами идентификаторы
в тест не записаны, иначе охрана вернула бы утечку на место.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 9–10 цифр подряд — форма telegram chat_id; годы, суммы и хеши сюда не попадают
CHAT_ID_RE = re.compile(r"(?<!\d)\d{9,10}(?!\d)")
# плейсхолдеры из решения Ш-03: ими заменены живые значения в тестах и документации
CHAT_PLACEHOLDERS = {"123456789", "123456788"}
CALENDAR_RE = re.compile(r"[A-Za-z0-9._-]+@group\.calendar\.google\.com")
SA_RE = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com")
PLACEHOLDER = {"<project-id>", "<CALENDAR_ID>", "xxxx", "abc123"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / line for line in out.stdout.splitlines() if line]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_workflows_не_содержат_chat_id_числом():
    """Чат владельца в workflow берётся из vars/secrets, а не пишется числом."""
    bad = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        found = [n for n in CHAT_ID_RE.findall(_read(path)) if n not in CHAT_PLACEHOLDERS]
        if found:
            bad[path.name] = len(found)
    assert bad == {}, f"chat_id числом в workflow: {bad}"


def test_env_example_не_содержит_личных_значений():
    """`.env.example` — только имена переменных и локальные адреса, без id и почт владельцев."""
    for name in (".env.example", ".env.dev.example"):
        for number, line in enumerate(_read(ROOT / name).splitlines(), start=1):
            body = line.split("#", 1)[0]
            key = body.split("=", 1)[0].strip().upper()
            if "CHAT" in key:
                assert not CHAT_ID_RE.search(body), f"{name}:{number}: chat_id числом"
            assert "@gmail." not in body, f"{name}:{number}: личная почта"


def test_календарные_id_и_service_account_это_плейсхолдеры():
    """Конкретный id календаря и адрес SA — только в .env; в файлах — плейсхолдеры."""
    bad = []
    for path in _tracked_files():
        if path.suffix not in {".md", ".example", ".yml", ".yaml", ".py", ".json", ".sql"}:
            continue
        text = _read(path)
        for match in CALENDAR_RE.findall(text):
            if not match.startswith(tuple(PLACEHOLDER)):
                bad.append(f"{path.relative_to(ROOT)}: {match}")
        for match in SA_RE.findall(text):
            if not match.startswith(tuple(PLACEHOLDER)):
                bad.append(f"{path.relative_to(ROOT)}: {match}")
    assert bad == [], f"конкретные идентификаторы Google: {bad}"


def test_личные_манифесты_участников_не_в_репозитории():
    """`bot/manifests/participants` — только формат `_example.md`, без личных файлов.

    Файл `<person_key>.md` — личные настройки человека (четвёртый слой промпта): в публичном
    дереве ему не место, боевая копия живёт на сервере вне git (`docs/development/26` §1).
    Переименование под вымышленное имя не спасает: файл ищется по `person_key` из боевой БД
    (`services/tool-api/app/api/admin.py`, `compose_prompt`).
    """
    participants = ROOT / "bot" / "manifests" / "participants"
    extra = sorted(p.name for p in participants.glob("*.md") if p.name != "_example.md")
    assert extra == [], f"личные манифесты в репозитории: {extra}"


def test_локальный_конфиг_имён_не_в_репозитории():
    """`config/participant-names.yaml` — личные данные (соответствие имён), в индексе только пример.

    Файл читают `scripts/_people.py` и `app/participant_names.py`, если он есть; боевая копия
    живёт на сервере вне git (`docs/development/26` §1). В репозитории — `.example` с
    плейсхолдерами вида `<...>`, иначе обезличивание Ш-03 вернулось бы тем же файлом.
    """
    personal = "config/participant-names.yaml"
    tracked = {p.relative_to(ROOT).as_posix() for p in _tracked_files()}
    assert personal not in tracked, "личный конфиг имён попал в индекс"
    assert personal in _read(ROOT / ".gitignore"), "нет строки в .gitignore"

    example = ROOT / "config" / "participant-names.example.yaml"
    assert example.is_file(), "пример конфига пропал"
    section = ""
    for number, line in enumerate(_read(example).splitlines(), start=1):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        if body.endswith(":") and " " not in body:
            section = body[:-1]
            continue
        if ":" not in body:
            continue
        key, value = (part.strip() for part in body.split(":", 1))
        if section == "names":
            assert key.startswith('"<') and value.startswith('"<'), \
                f"{example.name}:{number}: имя вместо плейсхолдера"
        elif section == "category_hints":
            assert key.startswith('"<'), f"{example.name}:{number}: имя вместо плейсхолдера"


def test_сиды_не_привязываются_к_chat_id_числом():
    """`db/init` — сиды для dev/test: админ ищется по роли, а не по chat_id владельца."""
    bad = []
    for path in sorted((ROOT / "db" / "init").glob("*.sql")):
        text = _read(path)
        for match in re.finditer(r"tg_chat_id\s*=\s*(\d{6,})", text):
            bad.append(f"{path.name}: tg_chat_id = {match.group(1)}")
    assert bad == [], f"chat_id в сидах: {bad}"
