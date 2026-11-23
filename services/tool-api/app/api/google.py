"""Google: режим приложения, свежесть источников, кнопка «Сверить».

Зачем (docs/development/186 §5, §7, §9; docs/development/190 §10). До этой карты у
чтения Google не было одного входа: экран не мог честно сказать «данные от 17:20», а
«Сверить» означало «каждый делает что хочет». Здесь три маршрута и один смысл у каждого:

* `POST /tools/google/mode` — переключение режима `transitional`/`target`. Это решение
  администратора, поэтому право `admin.panel` (существующая возможность из `db/init/043`;
  строки `*` в справочнике прав нет). Переход в `target` без выгрузки запрещён (400
  `mode_backup_required`), успех пишет аудит `google.mode.set`;
* `GET /tools/google/freshness` — по каждому источнику `as_of`, `last_edit_time`,
  `stale`, `error`. Прошлый снимок никогда не выдаётся за свежий;
* `POST /tools/google/refresh` — перечитать перечисленные источники и вернуть
  расхождения. По умолчанию чистит черновик после переноса человеком (§5.3); с
  `dry_run=true` не пишет ничего — ни в Google, ни в базу (190 §10.1).

Фоновой синхронизации нет: ни таймеров, ни задач, ни n8n (§7). Отметка чтения ставится
только после успешного чтения и никогда при `dry_run`: иначе неудачная попытка выдала бы
прошлый снимок за свежий (AC-17).

Тесты: `tests/unit/test_google_mode.py`, `test_google_freshness.py`,
`test_google_errors.py`, `test_google_no_pii.py`, `test_google_refresh_report.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import Field

from .. import calendar_sync, core, google_targets, sheet_binding
from ..errors import ApiError
from ..external import GOOGLE_API_ERRORS

router = APIRouter()


class WindowIn(core.ApiIn):
    """Окно чтения календаря: `{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}` (188 §10)."""

    from_: str = Field(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")
    to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ModeIn(core.ApiIn):
    """Смена режима. `backup` обязателен только для `target` (186 §2.3)."""

    mode: str = Field(min_length=1, max_length=20)
    backup: str | None = Field(default=None, max_length=500)


class RefreshIn(core.ApiIn):
    """Тело «Сверить»: источники, окно и запрет записи.

    `dry_run` по умолчанию `false` — это правило 186 §5.3 (чистка черновика при
    сверке). Экран «Сверить» шлёт `true`, чтобы ничего не менять (дизайн 187 §4).
    """

    sources: list[str] | None = None
    dry_run: bool = False
    window: WindowIn | None = None


@router.post("/tools/google/mode")
def google_mode_set(body: ModeIn, who: dict = Depends(core.require("admin.panel"))) -> dict:
    """Сменить режим приложения. Право администратора: это решение уровня владельца."""
    with core.pool.connection() as conn:
        after = google_targets.set_google_mode(conn, body.mode, who.get("id"), body.backup)
    return {"ok": True, **after}


@router.get("/tools/google/freshness")
def google_freshness(who: dict = Depends(core.require("schedule.read.all"))) -> dict:
    """Свежесть источников: экран показывает «данные от …» и не врёт про свежесть.

    Один список на все источники, включая `calendar.read`: экран не собирает
    свежесть из двух ответов (186 §7).
    """
    with core.pool.connection() as conn:
        sources = calendar_sync.freshness(conn)
    return {"ok": True, "sources": sources,
            "stale": any(item["stale"] for item in sources)}


@router.post("/tools/google/refresh")
def google_refresh(body: RefreshIn | None = None,
                   who: dict = Depends(core.require("schedule.read.all"))) -> dict:
    """Перечитать источники и вернуть расхождения. Ни живые листы, ни боевой не пишет."""
    body = body or RefreshIn()
    requested = body.sources or ["calendar.read"]
    unknown = [key for key in requested if key not in google_targets.READ_SOURCES]
    if unknown:
        raise ApiError(400, f"unknown_source: нет в реестре — {', '.join(unknown)}",
                       code="unknown_source")
    time_from, time_to = _window(body)

    report: dict = {"ok": True, "dry_run": body.dry_run,
                    "window": {"from": time_from.date().isoformat(),
                               "to": (time_to - timedelta(days=1)).date().isoformat()},
                    "sources": {}}
    with core.pool.connection() as conn:
        for key in requested:
            if key == "calendar.read":
                report["sources"][key] = calendar_sync.scan(
                    conn, read_service=core._calendar(),
                    draft_service=core._calendar_write(),
                    time_from=time_from, time_to=time_to, dry_run=body.dry_run)
            else:
                report["sources"][key] = _read_source(conn, key, dry_run=body.dry_run)
    return report


def _parse_day(text: str, raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise ApiError(400, f"bad_window: {text} — ожидается YYYY-MM-DD",
                       code="bad_window") from None


def _window(body: RefreshIn) -> tuple[datetime, datetime]:
    """Окно из тела или текущий календарный месяц в зоне сервиса (188 §10)."""
    if body.window is None:
        return calendar_sync.default_window()
    start = _parse_day("from", body.window.from_).replace(tzinfo=core.TZ)
    end = _parse_day("to", body.window.to).replace(tzinfo=core.TZ) + timedelta(days=1)
    if end <= start:
        raise ApiError(400, "bad_window: начало окна позже конца", code="bad_window")
    return start, end


def _entry(key: str) -> dict:
    """Пустая строка отчёта: пока ничего не прочитано, `as_of` не выдумывается."""
    return {"key": key, "as_of": None, "last_edit_time": None, "stale": True,
            "error": None}


def _read_source(conn, key: str, *, dry_run: bool = False) -> dict:
    """Перечитать один листовой/дисковый источник.

    Отметка чтения ставится только после успешного чтения и только без `dry_run`:
    «Сверить» на экране ничего не пишет (190 §10.1), а неудачное чтение не имеет права
    выглядеть свежим (AC-17). Календарь сюда не попадает: у него своя сверка.
    """
    entry = _entry(key)
    if google_targets.target(key).kind == google_targets.DRIVE:
        return _read_drive(conn, entry, dry_run=dry_run)
    if not google_targets.read_id(key):
        entry["error"] = "source_unset"
        return entry
    try:
        session = sheet_binding.open_sheet(key, conn=conn, write=False)
    except ApiError as exc:
        entry["error"] = exc.code
        return entry
    entry["as_of"] = datetime.now(core.TZ).isoformat()
    entry["last_edit_time"] = (session.modified_time.isoformat()
                               if session.modified_time else None)
    entry["stale"] = False
    if not dry_run:
        sheet_binding.touch_after_read(conn, key, session.modified_time)
    return entry


def _read_drive(conn, entry: dict, *, dry_run: bool = False) -> dict:
    """Метаданные корня Drive: только список файлов, содержимое не качается (§3)."""
    root = google_targets.read_id("drive.root")
    if not root:
        entry["error"] = "source_unset"
        return entry
    try:
        answer = core._drive_read().files().list(
            q=f"'{root}' in parents and trashed=false", pageSize=100,
            fields="files(id,name,mimeType,modifiedTime)").execute()
    except GOOGLE_API_ERRORS as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        entry["error"] = "no_access" if status in (401, 403) else "drive_unavailable"
        return entry
    entry["as_of"] = datetime.now(core.TZ).isoformat()
    entry["files"] = len(answer.get("files") or [])
    entry["stale"] = False
    if not dry_run:
        calendar_sync.mark_source_read(conn, calendar_sync.DRIVE_READ_KEY)
    return entry
