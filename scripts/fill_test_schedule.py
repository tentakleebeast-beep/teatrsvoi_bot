#!/usr/bin/env python3
"""
Репетиция цветов в тестовом листе «Занятость - тест (цвета)» (186 §4.3, AC-10).

Пишет через общий протокол записи, а не «как получится»: ключ реестра
`schedule.test`, `sheet_binding`, страница по шаблону `SCHEDULE_TEST_SHEET_PATTERN`
(`{month}` и `{nick}`), `row_map` с `entity_type=color_probe`, перечитывание адресов.
Страницы не создаются: нет страницы — `sheet_ambiguous`.

  python scripts/fill_test_schedule.py --list
  python scripts/fill_test_schedule.py --apply --month 2026-11

Данные для проб берутся из боевого файла «Расписание студийцы» (только чтение):
текст дня человека разбирается тем же `hours_from_text`, что и в сервисе.
"""

from __future__ import annotations

import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень проекта — раньше tool-api: `app.settings` тянет `services.shared` (как в `_cli`).
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "tool-api"))

from _cli import add_write_mode, apply_env, now_local, parser, writes  # noqa: E402

if __name__ == "__main__":   # запуск файлом: `.env` читается до настроек сервиса
    apply_env()

from app import core, schedule_test_sheet  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.schedule_grid import hours_from_text  # noqa: E402

SOURCE = os.path.join(ROOT, ".hermes", "desktop-attachments", "Расписание студийцы.xlsx")

PEOPLE = ["Дарья Смирнова", "Александра Орлова", "Алена Морозова",
          "Виктор Новиков", "Мария Лебедева"]
NICKS = {"Дарья Смирнова": "Дарья", "Александра Орлова": "Орлова",
         "Алена Морозова": "Морозова", "Виктор Новиков": "Новиков",
         "Мария Лебедева": "Лебедева"}


def _pool_open() -> None:
    """Пул в проекте создаётся закрытым (его открывает FastAPI) — открываем сами."""
    opener = getattr(core.pool, "open", None)
    if opener is not None:
        opener()


def _pool_close() -> None:
    closer = getattr(core.pool, "close", None)
    if closer is not None:
        closer()


def read_source() -> dict[str, dict[date, str]]:
    """Текст занятости из боевого файла: человек → день → текст. Только чтение."""
    from openpyxl import load_workbook
    wb = load_workbook(SOURCE, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))

    header = rows[0]
    columns = {str(cell).strip(): index for index, cell in enumerate(header)
               if cell and index >= 3 and str(cell).strip() in PEOPLE}

    out: dict[str, dict[date, str]] = {name: {} for name in columns}
    for row in rows[2:]:
        day = row[1]
        if not isinstance(day, date):
            continue
        for name, index in columns.items():
            if index < len(row) and row[index] not in (None, ""):
                out[name][day] = str(row[index]).strip()
    return out


def build_probes(source: dict[str, dict[date, str]], name: str,
                 label: str) -> list[schedule_test_sheet.Probe]:
    """День человека → пробы по часам в пределах месяца страницы."""
    probes = []
    for day, text in sorted(source.get(name, {}).items()):
        if day.strftime("%m.%Y") != label:
            continue
        for hour, status in sorted(hours_from_text(text).items()):
            if status in schedule_test_sheet.STATUSES:
                probes.append(schedule_test_sheet.Probe(day=day, hour=hour, status=status))
    return probes


def pages_report(label: str, titles: list[str]) -> int:
    """Страницы для людей месяца: (есть/нет). Ничего не создаёт."""
    pattern = core.SCHEDULE_TEST_SHEET_PATTERN
    missing = 0
    for name in PEOPLE:
        wanted = schedule_test_sheet.page_title(pattern, label, NICKS[name])
        found = titles.count(wanted)
        mark = "ok" if found == 1 else f"нет ({found})"
        missing += 0 if found == 1 else 1
        print(f"{name}: страница «{wanted}» — {mark}")
    return 0 if missing == 0 else 1


def main() -> int:
    ap = parser(__doc__ or "Репетиция цветов в тестовом листе")
    add_write_mode(ap)
    ap.add_argument("--month", default=now_local().strftime("%Y-%m"),
                    help="месяц страницы: YYYY-MM или MM.YYYY (по умолчанию текущий)")
    args = ap.parse_args()

    label = schedule_test_sheet.month_label(args.month)
    if not writes(args):
        _pool_open()
        try:
            properties, _sid = schedule_test_sheet.pages()
        finally:
            _pool_close()
        titles = [str((item.get("properties") or {}).get("title") or "") for item in properties]
        return pages_report(label, titles)

    try:
        source = read_source()
    except (OSError, KeyError) as exc:
        print(f"нет источника «{SOURCE}»: {exc}")
        return 2

    problems = 0
    _pool_open()
    try:
        with core.pool.connection() as conn:
            for name in PEOPLE:
                probes = build_probes(source, name, label)
                if not probes:
                    print(f"{name}: на {label} нечего писать")
                    continue
                try:
                    report = schedule_test_sheet.write_probes(
                        conn, month=label, nick=NICKS[name], probes=probes, apply=True)
                except ApiError as exc:
                    print(f"{name}: отказ {exc.code}: {exc.detail}")
                    problems += 1
                    continue
                print(f"{name}: страница «{report['sheet']}», записано "
                      f"{report.get('written', 0)}, пропущено {report['skipped']}")
    finally:
        _pool_close()
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
