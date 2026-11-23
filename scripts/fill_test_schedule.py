#!/usr/bin/env python3
"""
Репетиция цветов в тестовом листе «Занятость - тест (цвета)» (186 §4.3, §13 п.3, AC-10).

Пишет через общий протокол записи, а не «как получится»: ключ реестра
`schedule.test`, `sheet_binding`, страница по имени из тестового файла, `row_map`
с `entity_type=color_probe`, перечитывание адресов. Страницы не создаются:
нет страницы — отказ, а не `addSheet`.

  python scripts/fill_test_schedule.py --list                 # страницы на месте (чтение)
  python scripts/fill_test_schedule.py --month 2026-11        # что будет записано (чтение)
  python scripts/fill_test_schedule.py --month 2026-11 --apply

Откуда люди и данные. Страницы `MM.YYYY · Ник` берутся из самого тестового файла, ник
страницы ищется в шапке живого листа «Расписание студийцы» (`SCHEDULE_HUMAN_*`, только
чтение), его занятость разбирается тем же `month_grid.sheet_cell`/`hours_from_text`, что
и в сервисе. Литералов людей в скрипте нет: живой прогон 23.11.2026 показал, что список
«Дарья Смирнова, …» не совпадает ни с одной из пяти настоящих страниц («Даина»,
«Гагарина», «Кондракова», «Таюкин», «Загаринская»), а файла-источника в рабочем дереве
нет вовсе.

Чего не делает: не пишет в живой лист (только `values.get`), не создаёт страницы, не
ставит фиолетовую клетку и объединение — они уже стоят в тестовом файле, а писать мимо
ключа `schedule.test` нельзя.

Код выхода:

* 0 — `--list` показал страницы месяца (человек мог не найтись: живой лист не прочитан
  или подписи нет в шапке — это видно в строке, но код остаётся 0, потому что вопрос
  команды «есть ли страницы» решён) либо план/запись разобрали все пробы;
* 1 — нет страниц месяца, живой лист не прочитан в режиме плана, человек не найден
  или в клетках месяца нечего записывать;
* 2 — неверный месяц, нет доступа к Google (ключ сервисного аккаунта, права) или
  несовместимые флаги.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень проекта — раньше tool-api: `app.settings` тянет `services.shared` (как в `_cli`).
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "services", "tool-api"))

from _cli import add_write_mode, apply_env, now_local, parser, writes  # noqa: E402

if __name__ == "__main__":   # запуск файлом: `.env` читается до настроек сервиса
    apply_env()

from app import core, schedule_rehearsal, schedule_sheet, schedule_test_sheet  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.external import GOOGLE_API_ERRORS  # noqa: E402


def _pool_open() -> None:
    """Пул в проекте создаётся закрытым (его открывает FastAPI) — открываем сами."""
    opener = getattr(core.pool, "open", None)
    if opener is not None:
        opener()


def _pool_close() -> None:
    closer = getattr(core.pool, "close", None)
    if closer is not None:
        closer()


def test_titles(read) -> list[str]:
    """Имена листов тестового файла. Нет идентификатора или прав — отказ, а не догадка."""
    properties, _spreadsheet_id = schedule_test_sheet.pages(read)
    return [str((item.get("properties") or {}).get("title") or "").strip() for item in properties]


def print_page(report: schedule_rehearsal.PageReport) -> None:
    """Строка отчёта по странице: человек, колонка и разобранные клетки."""
    line = report.page.title
    if report.column:
        name = report.person or report.page.nick
        line += f" — человек «{name}», колонка {schedule_sheet.column_letter(report.column)}"
    if report.problems:
        codes = ", ".join(schedule_rehearsal.PROBLEMS.get(code, code) for code in report.problems)
        print(f"{line}: {codes}")
        return
    parts = [f"дней {len({probe.day for probe in report.probes})}",
             f"проб {len(report.probes)}"]
    parts += [f"{name} {report.counts.get(name, 0)}"
              for name in ("busy", "free", "uncertain", "empty", "unparsed")]
    print(f"{line}: " + ", ".join(parts))


def build_parser():
    """Флаги скрипта отдельно от `main`: `--list` проверяется тестом без сети."""
    ap = parser(__doc__ or "Репетиция цветов в тестовом листе")
    add_write_mode(ap)
    ap.add_argument("--month", default=now_local().strftime("%Y-%m"),
                    help="месяц страницы: YYYY-MM или MM.YYYY (по умолчанию текущий)")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="показать страницы тестового листа и найденного в шапке "
                         "живого листа человека; ничего не пишет")
    return ap


def validate_args(ap, args) -> None:
    """`--list` — просмотр: вместе с `--apply` он не имеет смысла и отказывает кодом 2."""
    if args.list_only and writes(args):
        ap.error("--list несовместим с --apply: просмотр ничего не пишет")


def read_sheets():
    """Клиент чтения тестового файла: (клиент, код выхода). Без ключа — чистый отказ.

    `core._sheets_read()` строит сервисного клиента и падает `OSError`
    (`FileNotFoundError` на `secrets/google-sa.json`) или `GoogleAuthError`, если ключа
    нет. Это тот же класс сбоя, что и отказ Google при чтении, и человеку нужен код 2
    и строка «нет доступа», а не traceback.
    """
    try:
        return core._sheets_read(), 0
    except GOOGLE_API_ERRORS as exc:
        print("тестовый лист не открыт: нет доступа к Google "
              f"({type(exc).__name__}: проверьте ключ сервисного аккаунта и права)",
              file=sys.stderr)
        return None, 2


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    validate_args(ap, args)

    try:
        label = schedule_test_sheet.month_label(args.month)
    except ApiError as exc:
        print(f"неверный месяц: {exc.code}", file=sys.stderr)
        return 2

    read, code = read_sheets()
    if read is None:
        return code
    try:
        titles = test_titles(read)
    except ApiError as exc:
        print(f"тестовый лист не открыт: {exc.code}", file=sys.stderr)
        return 2
    pages = schedule_rehearsal.pages(titles, label)

    if args.list_only:
        if not pages:
            print(f"в тестовом листе нет страниц «MM.YYYY · Ник» за {label}")
            return 1
        values, reason = schedule_rehearsal.live_sheet_values(read)
        for page in pages:
            if values is None:
                print(f"{page.title} — ник «{page.nick}»; живой лист не прочитан ({reason})")
                continue
            column, problem = schedule_rehearsal.person_column(values, page.nick)
            if column is None:
                problem_text = schedule_rehearsal.PROBLEMS.get(problem, problem)
                print(f"{page.title} — ник «{page.nick}»: {problem_text}")
                continue
            name = schedule_rehearsal.person_name(values, column)
            print(f"{page.title} — человек «{name}», колонка {schedule_sheet.column_letter(column)}")
        return 0

    values, reason = schedule_rehearsal.live_sheet_values(read)
    if values is None:
        print(f"живой лист «Расписание студийцы» не прочитан: {reason}")
        return 1
    if not pages:
        print(f"в тестовом листе нет страниц за {label} — писать некуда, страницы не создаются")
        return 1

    reports = [schedule_rehearsal.page_report(values, page) for page in pages]
    for report in reports:
        print_page(report)
    if not all(report.ok for report in reports):
        return 1
    if not writes(args):
        print("\nничего не записано: для записи нужен --apply")
        return 0

    failed = 0
    _pool_open()
    try:
        with core.pool.connection() as conn:
            for report in reports:
                try:
                    answer = schedule_test_sheet.write_probes(
                        conn, month=report.page.month, nick=report.page.nick,
                        probes=report.probes, page=report.page.title, apply=True,
                        service=core._sheets(write=True))
                except ApiError as exc:
                    failed += 1
                    print(f"{report.page.title}: отказ {exc.code}: {exc.detail}")
                    continue
                print(f"{report.page.title}: записано {answer.get('written', 0)},"
                      f" пропущено {answer.get('skipped', 0)}")
    finally:
        _pool_close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
