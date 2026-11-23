#!/usr/bin/env python3
"""Отчёт о правах сервисного аккаунта на объекты Google (docs/development/186 §6).

Зачем. Права бота проверялись «по памяти»: читатель на живых файлах и боевом календаре,
редактор на копиях, тестовом листе и черновом календаре. Скрипт показывает фактическую
роль и расхождение с требуемой, код выхода 1 при расхождении.

Правила (186 §6):
  * порядок при закрытии доступов — сначала выдать редактора на объекты записи, потом
    снимать ссылку с живых: иначе бот остаётся без доступа к тому, что должен писать;
  * предупреждение, если доступ открыт на `anyone` с ролью `writer`.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/google_rights.py
    env -u PYTHONPATH .venv/bin/python scripts/google_rights.py --json

Код выхода: 0 — расхождений нет, 1 — есть, 2 — нет ключа/сети.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import core, google_targets  # noqa: E402

#: Роли Google, которые считаются «писателем» по нашему правилу.
WRITER_ROLES = {"owner", "organizer", "writer"}


def _sheets_permission(spreadsheet_id: str) -> tuple[str | None, list[str]]:
    """Роль сервисного аккаунта на файле и список предупреждений.

    `permissions.list` отдаёт роль по каждому, кому выдан доступ; нам нужна своя —
    с `type = user` и почтой сервисного аккаунта.
    """
    warnings: list[str] = []
    answer = core._drive_read().permissions().list(
        fileId=spreadsheet_id,
        fields="permissions(id,type,role,emailAddress)").execute()
    our_email = _service_account_email()
    role = None
    for item in answer.get("permissions") or []:
        if item.get("type") == "anyone" and item.get("role") in WRITER_ROLES:
            warnings.append("доступ открыт на anyone с правом записи")
        if our_email and str(item.get("emailAddress") or "").lower() == our_email:
            role = str(item.get("role") or "")
    return role, warnings


def _calendar_role(calendar_id: str) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    answer = core._calendar().acl().list(calendarId=calendar_id).execute()
    our_email = _service_account_email()
    role = None
    for item in answer.get("items") or []:
        scope = item.get("scope") or {}
        if scope.get("type") == "default" and item.get("role") in WRITER_ROLES:
            warnings.append("календарь открыт публично с правом записи")
        if our_email and str(scope.get("value") or "").lower() == our_email:
            role = str(item.get("role") or "")
    return role, warnings


def _service_account_email() -> str:
    try:
        with open(core.GOOGLE_SA_JSON, encoding="utf-8") as handle:
            return str(json.load(handle).get("client_email") or "").lower()
    except (OSError, ValueError):
        return ""


def _enough(role: str | None, required: str) -> bool:
    """Хватает ли фактической роли для требуемой."""
    if not role:
        return False
    if required == google_targets.ROLE_READER:
        return True                    # reader и выше читает
    return role in {"owner", "organizer", "writer"}


def _check(key: str) -> dict:
    target = google_targets.target(key)
    required = target.required_role
    try:
        object_id = google_targets.read_id(key) if key in google_targets.READ_SOURCES \
            else google_targets.resolve(key)
    except Exception as exc:  # noqa: BLE001 — исход отчёта
        return {"key": key, "required": required, "role": None, "ok": False,
                "warnings": [], "error": getattr(exc, "code", str(exc))}
    if not object_id:
        return {"key": key, "required": required, "role": None, "ok": False,
                "warnings": [], "error": "write_target_unset"}
    try:
        if target.kind == google_targets.CALENDAR:
            role, warnings = _calendar_role(object_id)
        else:
            role, warnings = _sheets_permission(object_id)
    except Exception as exc:  # noqa: BLE001
        return {"key": key, "required": required, "role": None, "ok": False,
                "warnings": [], "error": str(exc)[:120]}
    return {"key": key, "required": required, "role": role,
            "ok": _enough(role, required), "warnings": warnings, "error": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Права сервисного аккаунта (186 §6)")
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    args = parser.parse_args(argv)

    if not core.GOOGLE_SA_JSON:
        print("нет ключа сервисного аккаунта (GOOGLE_SA_JSON)")
        return 2
    keys = sorted(set(google_targets.READ_SOURCES) | set(google_targets.WRITE_TARGETS))
    rows = [_check(key) for key in keys]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'объект':<22} {'требуется':<9} {'фактически':<11} итог")
        for row in rows:
            verdict = "ok" if row["ok"] else "РАСХОЖДЕНИЕ"
            print(f"{row['key']:<22} {row['required']:<9} {str(row['role']):<11} {verdict}")
            for warning in row["warnings"]:
                print(f"  ! {warning}")
            if row["error"]:
                print(f"  ! {row['error']}")
    broken = [row for row in rows if not row["ok"]]
    return 0 if not broken else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
