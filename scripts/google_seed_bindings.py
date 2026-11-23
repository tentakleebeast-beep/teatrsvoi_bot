#!/usr/bin/env python3
"""Засев `sheet_binding`: где именно живут листы бота и какая у них шапка.

Зачем (docs/development/186 §4.3, §8). Привязка не создаётся при старте сервиса: её
засевает человек/администратор этим скриптом, а сервис только читает. Скрипт идёт по
реестру (`app/google_targets.py`), открывает каждый лист **по имени**, считает сумму
шапки той же функцией, что и запись (`app/sheet_binding.header_checksum`), и делает
upsert в `sheet_binding`.

Что НЕ делает: не пишет в Google (только `values.get` и метаданные), не угадывает имя
листа («первый лист файла» запрещён), не трогает боевые листы из `WRITE_TARGETS` нет —
их здесь нет по определению.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/google_seed_bindings.py --dry-run
    env -u PYTHONPATH .venv/bin/python scripts/google_seed_bindings.py --only budget.transactions

Код выхода: 0 — все привязки на месте, 1 — часть объектов не засеяна (нет id, лист не
найден, нет прав), 2 — нет соединения с базой или ключа сервисного аккаунта.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app import core, google_targets, sheet_binding  # noqa: E402

#: Заголовки страниц для тестового листа цветов (186 §3): страница — «MM.YYYY · Ник».
TEST_PAGE_HINT = "страница по шаблону SCHEDULE_TEST_SHEET_PATTERN"


def _sheet_title(key: str, props: list[dict], pattern: str) -> tuple[str | None, str]:
    """Имя листа по привязке/настройке. Возвращает (имя, причина отказа)."""
    binding_title = google_targets.WRITE_TARGETS[key].meta.get("sheet_title", "")
    if key == "budget.transactions":
        return binding_title or "Транзакции", ""
    if key == "schedule.workload":
        title = str(core.SCHEDULE_SHEET_TITLE or "").strip() or binding_title
        return (title, "") if title else ("", "SCHEDULE_SHEET_TITLE не задан")
    if key == "schedule.test":
        wanted = str(core.SCHEDULE_TEST_SHEET_PATTERN or "").strip() or pattern
        if not wanted:
            return "", f"не задан {TEST_PAGE_HINT}"
        titles = [str((item.get("properties") or {}).get("title") or "") for item in props]
        matches = [title for title in titles if title.strip() == wanted.strip()]
        return (matches[0], "") if len(matches) == 1 else ("", f"страниц «{wanted}»: {len(matches)}")
    return binding_title, ""


def _seed_one(conn, key: str, *, dry_run: bool) -> str:
    """Одна привязка: id, лист по имени, шапка, upsert. Возвращает код исхода."""
    try:
        spreadsheet_id = google_targets.resolve(key)
    except Exception as exc:  # noqa: BLE001 — исход отчёта, а не падение
        return f"не настроен: {getattr(exc, 'code', exc)}"
    target = google_targets.WRITE_TARGETS[key]
    meta_extra = target.meta
    service = core._sheets_read()
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                          fields=sheet_binding.SHEET_FIELDS).execute()
    except Exception as exc:  # noqa: BLE001
        return f"лист недоступен: {str(exc)[:120]}"
    props = meta.get("sheets") or []
    title, problem = _sheet_title(key, props, str(meta_extra.get("sheet_title") or ""))
    if not title:
        return problem or "имя листа не определено"
    try:
        sheet_binding.pick_sheet(props, title)
    except Exception as exc:  # noqa: BLE001
        return f"лист не найден однозначно: {getattr(exc, 'code', exc)}"
    header_row = int(meta_extra.get("header_row") or 1)
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!A{header_row}:AZ{header_row}",
        majorDimension="ROWS").execute().get("values", [])
    checksum = sheet_binding.header_checksum(values[0] if values else [],
                                             int(meta_extra.get("first_col") or 1),
                                             meta_extra.get("last_col"))
    if dry_run:
        return f"ok (dry-run): лист «{title}», сумма шапки {checksum[:12]}…"
    conn.execute("""
        insert into sheet_binding (key, spreadsheet_id, sheet_title, layout, header_row,
                                   first_col, last_col, header_checksum, meta, updated_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        on conflict (key) do update set spreadsheet_id = excluded.spreadsheet_id,
                                        sheet_title = excluded.sheet_title,
                                        layout = excluded.layout,
                                        header_row = excluded.header_row,
                                        first_col = excluded.first_col,
                                        last_col = excluded.last_col,
                                        header_checksum = excluded.header_checksum,
                                        meta = excluded.meta,
                                        updated_at = now()""",
                 (key, spreadsheet_id, title, target.layout, header_row,
                  int(meta_extra.get("first_col") or 1), meta_extra.get("last_col"),
                  checksum, json.dumps(meta_extra.get("meta") or {}, ensure_ascii=False)))
    return f"ok: лист «{title}», сумма шапки {checksum[:12]}…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Засев привязок листов бота (186 §4.3)")
    parser.add_argument("--dry-run", action="store_true", help="показать, ничего не писать")
    parser.add_argument("--only", action="append", default=[],
                        help="ключ реестра (можно несколько раз)")
    args = parser.parse_args(argv)

    keys = args.only or sorted(google_targets.WRITE_TARGETS)
    unknown = [key for key in keys if key not in google_targets.WRITE_TARGETS]
    if unknown:
        print(f"нет в реестре объектов записи: {', '.join(unknown)}")
        return 1
    if not core.GOOGLE_SA_JSON:
        print("нет ключа сервисного аккаунта (GOOGLE_SA_JSON) — нечего читать")
        return 2

    problems = 0
    with core.pool.connection() as conn:
        for key in keys:
            outcome = _seed_one(conn, key, dry_run=args.dry_run)
            print(f"{key}: {outcome}")
            if not outcome.startswith("ok"):
                problems += 1
            if args.dry_run:
                conn.rollback()
    return 0 if problems == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
