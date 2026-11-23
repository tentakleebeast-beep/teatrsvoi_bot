#!/usr/bin/env python3
"""Отчёт о правах сервисного аккаунта на объекты Google (docs/development/186 §6).

Зачем. Права бота проверялись «по памяти»: читатель на живых файлах и боевом календаре,
редактор на копиях, тестовом листе и черновом календаре. Скрипт показывает фактическую
роль и расхождение с требуемой, код выхода 1 при расхождении.

После возврата ревью E5-4 (боевой календарь не трогаем пробой). `acl.list` и
`permissions.list` отдают 403 тому, кто не владелец объекта, даже когда читать и писать
он может (так отчёт показывал расхождение на календарях, где бот работает). Поэтому:

* читатель календаря — `events.list` по текущему окну;
* писатель календаря — `acl.list` пишущим клиентом, а если он закрыт (403) — для
  чернового календаря пробное событие `insert` + `delete`, для боевого (`calendar.live`)
  — `accessRole` из `calendarList.get`: в боевой календарь пробное событие не вставляется
  никогда (V-120 б, `docs/development/190` I-7);
* `calendar.live` в переходном режиме пропускается строкой отчёта: ключ там не действует;

* читатель/писатель таблицы — `files.get(fields="capabilities(canEdit,canModifyContent)")`
  (Drive считает возможности для того, кто спрашивает);
* предупреждение «доступ на anyone с правом записи» — по `permissions.list`, его
  недоступность не мешает роли.

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
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Корень проекта — раньше tool-api: `app.settings` импортирует `services.shared.settings`,
# поэтому одного `services/tool-api` в пути мало (та же схема, что в `scripts/_cli.py`
# и `scripts/build_screen_map.py`). Проверяется запуском `python scripts/<имя>.py`.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

if __name__ == "__main__":   # запуск файлом: `.env` проекта читается до настроек сервиса
    from _cli import apply_env

    apply_env()

from app import core, google_targets  # noqa: E402

#: Роли Google, которые считаются «писателем» по нашему правилу. `writerWithoutPrivateAccess`
#: — вариант writer без доступа к приватным событиям (Calendar API отдаёт его в ACL);
#: своим событием он распоряжается, что и проверено живым insert+delete в черновом.
WRITER_ROLES = {"owner", "organizer", "writer", "writerWithoutPrivateAccess"}

#: Как подписывается роль, измеренная операцией, а не списком прав.
READER_PROBE = "reader (events.list)"
WRITER_PROBE = "writer (insert+delete)"
#: Боевой календарь: пишущая проба запрещена, роль берётся из `accessRole`.
ACL_PROBE = "calendarList.accessRole"

#: Заголовки пробного события в календаре: его видно человеку, поэтому без эмодзи.
PROBE_SUMMARY = "[svoi] проверка прав бота"
PROBE_KIND = "rights_probe"


def _http_status(exc: BaseException) -> int | None:
    """HTTP-код отказа Google (`HttpError.resp.status`), если он есть."""
    return getattr(getattr(exc, "resp", None), "status", None)


def _service_account_email() -> str:
    try:
        with open(core.GOOGLE_SA_JSON, encoding="utf-8") as handle:
            return str(json.load(handle).get("client_email") or "").lower()
    except (OSError, ValueError):
        return ""


def _sheets_role(spreadsheet_id: str) -> tuple[str | None, list[str]]:
    """Роль сервисного аккаунта на файле по `capabilities`, а не по списку прав.

    `permissions.list` доступен только владельцу, поэтому читается отдельно и только
    ради предупреждения «anyone/writer»; роль считается по тому, что Drive разрешает
    этому же аккаунту (`files.get` возвращает возможности спрашивающего).
    """
    warnings: list[str] = []
    try:
        answer = core._drive_read().permissions().list(
            fileId=spreadsheet_id,
            fields="permissions(id,type,role)").execute()
    except Exception as exc:  # noqa: BLE001 — исход отчёта, не поломка
        if _http_status(exc) not in (403, 404):
            raise
        answer = {}
        warnings.append("список прав файла закрыт (аккаунт не владелец) — роль измерена возможностью правки")
    for item in answer.get("permissions") or []:
        if item.get("type") == "anyone" and item.get("role") in WRITER_ROLES:
            warnings.append("доступ открыт на anyone с правом записи")
    meta = core._drive_read().files().get(
        fileId=spreadsheet_id,
        fields="capabilities(canEdit,canModifyContent)").execute()
    caps = meta.get("capabilities") or {}
    if not caps:
        raise RuntimeError("files.get: capabilities пусты — доступ не подтверждён")
    if caps.get("canEdit") or caps.get("canModifyContent"):
        return google_targets.ROLE_WRITER, warnings
    return google_targets.ROLE_READER, warnings


def _calendar_read_role(calendar_id: str) -> tuple[str | None, list[str]]:
    """Читатель календаря: `events.list` по текущему окну — то, что делает сверка."""
    from app import calendar_sync

    time_from, time_to = calendar_sync.default_window()
    try:
        core._calendar().events().list(
            calendarId=calendar_id, timeMin=time_from.isoformat(),
            timeMax=time_to.isoformat(), singleEvents=True, maxResults=1).execute()
    except Exception as exc:  # noqa: BLE001 — исход отчёта
        raise RuntimeError(f"events.list: {_http_status(exc) or exc}") from exc
    return READER_PROBE, []


def _calendar_write_probe(service, calendar_id: str, warnings: list[str]) -> str:
    """Писатель календаря: создать и сразу удалить своё событие.

    `acl.list` для этого не годится — его закрывает не отсутствие прав, а отсутствие
    владения календарём; вставка же проверяет ровно то право, которым бот пользуется.
    """
    start = (datetime.now(UTC) + timedelta(days=30)).replace(
        hour=3, minute=0, second=0, microsecond=0)
    body = {
        "summary": PROBE_SUMMARY,
        "description": "Пробное событие scripts/google_rights.py: создаётся и сразу удаляется.",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=10)).isoformat()},
        "extendedProperties": {"private": {"svoi_source": "tool-api", "svoi_kind": PROBE_KIND}},
    }
    try:
        created = service.events().insert(calendarId=calendar_id, body=body).execute()
    except Exception as exc:  # noqa: BLE001 — исход отчёта
        raise RuntimeError(f"insert: {_http_status(exc) or exc}") from exc
    event_id = str(created.get("id") or "")
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except Exception as exc:  # noqa: BLE001 — событие осталось, это предупреждение
        warnings.append(f"пробное событие {event_id} не удалено ({_http_status(exc) or exc}) — удалить руками")
    return WRITER_PROBE


def _calendar_access_role(calendar_id: str) -> tuple[str | None, list[str]]:
    """Роль по `accessRole` из `calendarList.get`: без пишущей пробы.

    Нужна боевому календарю: вставить туда пробное событие нельзя (V-120 б, I-7), а
    `acl.list` закрыт не владельцу. `accessRole` — то, что Google думает о правах
    этого аккаунта на календарь: `owner`/`writer`/`reader`/`freeBusyReader`.
    """
    try:
        answer = core._calendar().calendarList().get(calendarId=calendar_id).execute()
    except Exception as exc:  # noqa: BLE001 — исход отчёта, не поломка
        if _http_status(exc) not in (403, 404):
            raise
        return None, [f"accessRole недоступен ({_http_status(exc) or 'нет ответа'}) — "
                      "роль боевого календаря подтвердите вручную"]
    return str(answer.get("accessRole") or "") or None, []


def _calendar_write_role(calendar_id: str,
                         allow_probe: bool = True) -> tuple[str | None, list[str]]:
    """Писатель календаря: сначала `acl.list` пишущим клиентом, затем — по ключу.

    Для чернового календаря `acl.list` может быть закрыт не владельцу, и тогда роль
    подтверждает пробная вставка. Для боевого (`allow_probe=False`) проба запрещена:
    остаётся `accessRole`, а не измеренная роль честно называется не измеренной.
    """
    warnings: list[str] = []
    service = core._calendar_write()
    our_email = _service_account_email()
    try:
        answer = service.acl().list(calendarId=calendar_id).execute()
    except Exception as exc:  # noqa: BLE001 — 403 здесь значит «не владелец»
        if _http_status(exc) not in (403, 404):
            raise
        answer = None
    role = None
    for item in (answer or {}).get("items") or []:
        scope = item.get("scope") or {}
        if scope.get("type") == "default" and item.get("role") in WRITER_ROLES:
            warnings.append("календарь открыт публично с правом записи")
        if our_email and str(scope.get("value") or "").lower() == our_email:
            role = str(item.get("role") or "")
    if role:
        return role, warnings
    if not allow_probe:
        role, extra = _calendar_access_role(calendar_id)
        warnings.extend(extra)
        if role:
            warnings.append(f"роль взята из {ACL_PROBE}, пишущая проба не выполнялась")
        return role, warnings
    return _calendar_write_probe(service, calendar_id, warnings), warnings


def _enough(role: str | None, required: str) -> bool:
    """Хватает ли фактической роли для требуемой."""
    if not role:
        return False
    if required == google_targets.ROLE_READER:
        return True                    # reader и выше читает
    return role in WRITER_ROLES or role == WRITER_PROBE


def _live_calendar_skip() -> dict | None:
    """`calendar.live` в переходном режиме не действует — строка отчёта, не расхождение."""
    key = google_targets.LIVE_CALENDAR
    required = google_targets.target(key).required_role
    try:
        mode = google_targets.google_mode()
    except Exception as exc:  # noqa: BLE001 — исход отчёта
        return {"key": key, "required": required, "role": None, "ok": False,
                "warnings": [], "error": f"google.mode: {str(exc)[:100]}"}
    if mode == google_targets.MODE_TARGET:
        return None
    return {"key": key, "required": required, "role": None, "ok": True,
            "warnings": [f"ключ действует только в целевом режиме (google.mode={mode}) — "
                         "пропущен"], "error": None}


def _check(key: str) -> dict:
    target = google_targets.target(key)
    required = target.required_role
    if key == google_targets.LIVE_CALENDAR:
        skipped = _live_calendar_skip()
        if skipped is not None:
            return skipped
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
            if required == google_targets.ROLE_READER:
                role, warnings = _calendar_read_role(object_id)
            else:
                role, warnings = _calendar_write_role(
                    object_id, allow_probe=key != google_targets.LIVE_CALENDAR)
        else:
            role, warnings = _sheets_role(object_id)
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
