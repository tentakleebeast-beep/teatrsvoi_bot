#!/usr/bin/env python3
"""Засев `sheet_binding`: где именно живут листы бота и какая у них шапка.

Зачем (docs/development/186 §4.3, §8). Привязка не создаётся при старте сервиса: её
засевает человек/администратор этим скриптом, а сервис только читает. Скрипт идёт по
реестру (`app/google_targets.py`), открывает каждый лист **по имени**, считает сумму
шапки той же функцией, что и запись (`app/sheet_binding.header_checksum`), и делает
upsert в `sheet_binding`.

Два режима (критерий AC-5):

* `--check` (по умолчанию) — **не ходит в Google**: сверяет строки `sheet_binding` с
  реестром. Код 0 — у каждого листа есть строка с тем же `spreadsheet_id`, именем листа
  и суммой шапки; код 1 — список ключей, которых не хватает или которые расходятся.
  Если привязка в базе есть, а Google недоступен, проверка всё равно отвечает честно;
* `--apply` — читает Google и пишет строки (идемпотентно: второй прогон не меняет
  `header_checksum`/`spreadsheet_id`, если лист не менялся);
* `--dry-run` — как `--apply`, но без записи в базу: показывает, что было бы записано.

Тестовый лист цветов живёт страницами `MM.YYYY · Ник` (186 §3), и у `sheet_binding`
одна строка на ключ. Поэтому страницу выбирает `--page "<имя>"`; без него скрипт берёт
единственную страницу, подходящую под шаблон `SCHEDULE_TEST_SHEET_PATTERN` (пустой
шаблон — стандартный `MM.YYYY · Ник`), а при нескольких честно просит `--page`, а не
засевает наугад.

Лист занятости тоже многостраничный (`Зан. 07.2026` … `Зан. 11.2026`), но страница у него
на каждый месяц. Поэтому без `SCHEDULE_SHEET_TITLE` в привязку пишется шаблон
`Зан. {month}`, а сумма шапки снимается с живой страницы месяца (шапка у месячных страниц
одна и та же). Одно статичное имя листа сломало бы сверку в следующем месяце (карта Д-4).

Что НЕ делает: не пишет в Google (только `values.get` и метаданные), не угадывает имя
листа («первый лист файла» запрещён), не засевает календари (`calendar.draft` — не
таблица) и Drive-корень.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/google_seed_bindings.py --check
    env -u PYTHONPATH .venv/bin/python scripts/google_seed_bindings.py --apply --only budget.transactions

Код выхода: 0 — привязки на месте, 1 — часть объектов не засеяна (нет id, лист не
найден, нет прав, строка расходится), 2 — нет соединения с базой или ключа сервисного
аккаунта (только для `--apply`/`--dry-run`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Корень проекта — раньше tool-api: `app.settings` импортирует `services.shared.settings`,
# поэтому одного `services/tool-api` в пути мало (та же схема, что в `scripts/_cli.py`
# и `scripts/build_screen_map.py`). Проверяется запуском `python scripts/<имя>.py`.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

if __name__ == "__main__":   # запуск файлом: `.env` проекта читается до настроек сервиса
    from _cli import apply_env

    apply_env()

from app import core, google_targets, sheet_binding  # noqa: E402

#: Листы бота (объекты записи). Календарь в этот список не входит по природе объекта.
WRITE_SHEET_KEYS = tuple(sorted(key for key, target in google_targets.WRITE_TARGETS.items()
                                if target.kind == google_targets.SHEET))

#: Живые листы, у которых есть одно имя страницы: их тоже надо привязать, иначе
#: `freshness` навсегда отвечает `binding_missing` (замечание ревью E1-4).
READ_SHEET_KEYS = ("budget.live", "schedule.human")

#: Заголовок листа финансов, если конфиг недоступен: `config/sheet-mapping.yaml`
#: `sources.finance.target_sheet`.
DEFAULT_FINANCE_TITLE = "Финансы 2026 от 07"


def _finance_title() -> str:
    """Имя листа финансов из `config/sheet-mapping.yaml` (единственный источник правды)."""
    path = ROOT / "config" / "sheet-mapping.yaml"
    try:
        import yaml  # noqa: PLC0415 — нужен только этому скрипту
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        title = str(((data.get("sources") or {}).get("finance") or {})
                    .get("target_sheet") or "").strip()
        return title or DEFAULT_FINANCE_TITLE
    except (OSError, ValueError, ImportError):
        return DEFAULT_FINANCE_TITLE


def _object_id(key: str) -> str:
    """Идентификатор объекта: у записи — `resolve`, у чтения — `read_id`."""
    if key in google_targets.WRITE_TARGETS:
        return google_targets.resolve(key)
    return google_targets.read_id(key)


def _page_regex(template: str) -> re.Pattern[str]:
    """Шаблон страницы → образец имени: `{month}`/`{nick}` заменяются на свои формы."""
    sample = re.escape(template)
    sample = sample.replace(re.escape("{month}"), r"\d{2}\.\d{4}")
    sample = sample.replace(re.escape("{nick}"), r".+")
    return re.compile(f"^{sample}$")


def _test_page(titles: list[str], pattern: str, page: str) -> tuple[str, str]:
    """Страница тестового листа: явная `--page` или единственная под шаблоном.

    Файл цветов многостраничный (`MM.YYYY · Ник`), а привязка одна. Поэтому явное имя
    сильнее шаблона, а несколько подходящих страниц — отказ с просьбой назвать `--page`:
    засеять «первую» значило бы повторить ошибку «первого листа файла» (186 §4.3).
    """
    wanted = str(page or "").strip()
    if wanted:
        matches = [title for title in titles if title.strip() == wanted]
        return (matches[0], "") if len(matches) == 1 else ("", f"страниц «{wanted}»: {len(matches)}")
    template = str(pattern or "").strip() or sheet_binding.DEFAULT_TEST_PAGE_PATTERN
    regex = _page_regex(template)
    matches = [title for title in titles if regex.match(title.strip())]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        return "", f"нет страницы по шаблону «{template}»"
    listed = ", ".join(matches[:3]) + ("…" if len(matches) > 3 else "")
    return "", f"страниц по шаблону «{template}»: {len(matches)} ({listed}) — укажите --page"


def _workload_page(props: list[dict], template: str) -> tuple[str, str]:
    """Страница, с которой берётся сумма шапки: текущий месяц, иначе ближайший.

    В привязке хранится шаблон (`Зан. {month}`), а сумма шапки снимается с живой страницы.
    У месячных страниц «Занятости» шапка одна и та же (замер 24.11.2026: `Дата`, `День`,
    часы 08–23 и легенда — у `Зан. 07.2026`…`Зан. 10.2026` сумма совпадает), поэтому
    сумма любой страницы годится для всех. Если страницы текущего месяца ещё нет, берём
    ближайшую существующую, а не отказываем: привязка нужна и до первого дня месяца.
    """
    titles = [str((item.get("properties") or {}).get("title") or "").strip() for item in props]
    current = sheet_binding.month_page_title(template, datetime.now(core.TZ))
    if current in titles:
        return current, ""
    matches = [title for title in titles if _page_regex(template).match(title)]
    if not matches:
        return "", f"нет ни одной страницы по шаблону «{template}»"
    return matches[-1], ""


def _sheet_title(key: str, props: list[dict], pattern: str,
                 page: str = "") -> tuple[str | None, str]:
    """Имя листа по привязке/настройке. Возвращает (имя, причина отказа)."""
    binding_title = google_targets.target(key).meta.get("sheet_title", "")
    if key == "budget.transactions":
        return binding_title or "Транзакции", ""
    if key == "schedule.workload":
        # явное имя сильнее шаблона; без настройки засеваем шаблон месяца — одно статичное
        # имя сломало бы сверку в следующем месяце (186 §4.3, карта Д-4)
        title = str(core.SCHEDULE_SHEET_TITLE or "").strip() or binding_title
        return (title, "") if title else (sheet_binding.DEFAULT_WORKLOAD_PAGE_PATTERN, "")
    if key == "schedule.human":
        title = str(core.SCHEDULE_HUMAN_SHEET_TITLE or "").strip()
        return (title, "") if title else ("", "SCHEDULE_HUMAN_SHEET_TITLE не задан")
    if key == "budget.live":
        title = _finance_title()
        return (title, "") if title else ("", "не задан лист финансов в sheet-mapping.yaml")
    if key == "schedule.test":
        titles = [str((item.get("properties") or {}).get("title") or "") for item in props]
        template = str(core.SCHEDULE_TEST_SHEET_PATTERN or "").strip() or pattern
        return _test_page(titles, template, page)
    return binding_title, ""


def _seed_one(conn, key: str, *, dry_run: bool, page: str = "") -> str:
    """Одна привязка: id, лист по имени, шапка, upsert. Возвращает исход.

    `page` — явное имя страницы для многостраничного тестового листа (`--page`).
    """
    try:
        object_id = _object_id(key)
    except Exception as exc:  # noqa: BLE001 — исход отчёта, а не падение
        return f"не настроен: {getattr(exc, 'code', exc)}"
    target = google_targets.target(key)
    meta_extra = target.meta
    service = core._sheets_read()
    try:
        meta = service.spreadsheets().get(spreadsheetId=object_id,
                                          fields=sheet_binding.SHEET_FIELDS).execute()
    except Exception as exc:  # noqa: BLE001
        return f"лист недоступен: {str(exc)[:120]}"
    props = meta.get("sheets") or []
    title, problem = _sheet_title(key, props, str(meta_extra.get("sheet_title") or ""),
                                  page=page)
    if not title:
        return problem or "имя листа не определено"
    # у шаблона (`Зан. {month}`) в привязку идёт само имя-шаблон, а шапка читается
    # с живой страницы месяца: суммы шапки месячных страниц совпадают (см. `_workload_page`)
    sheet_title = title
    if sheet_binding.is_month_template(title):
        sheet_title, problem = _workload_page(props, title)
        if not sheet_title:
            return problem
    try:
        sheet_binding.pick_sheet(props, sheet_title)
    except Exception as exc:  # noqa: BLE001
        return f"лист не найден однозначно: {getattr(exc, 'code', exc)}"
    header_row = int(meta_extra.get("header_row") or 1)
    values = service.spreadsheets().values().get(
        spreadsheetId=object_id, range=f"'{sheet_title}'!A{header_row}:AZ{header_row}",
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
                 (key, object_id, title, target.layout, header_row,
                  int(meta_extra.get("first_col") or 1), meta_extra.get("last_col"),
                  checksum, json.dumps(meta_extra.get("meta") or {}, ensure_ascii=False)))
    return f"ok: лист «{title}», сумма шапки {checksum[:12]}…"


def _check_one(conn, key: str) -> tuple[bool, str]:
    """Сверить строку `sheet_binding` с реестром — без обращения к Google."""
    try:
        object_id = _object_id(key)
    except Exception as exc:  # noqa: BLE001
        return False, f"не настроен: {getattr(exc, 'code', exc)}"
    binding = sheet_binding.load_binding(conn, key)
    if binding is None:
        return False, "нет строки привязки"
    if not binding.spreadsheet_id:
        return False, "пустой spreadsheet_id"
    if binding.spreadsheet_id != object_id:
        return False, f"привязан другой файл: {binding.spreadsheet_id}"
    if not binding.title:
        return False, "пустое имя листа"
    if not binding.header_checksum:
        return False, "нет контрольной суммы шапки"
    return True, f"ok: лист «{binding.title}», сумма шапки {binding.header_checksum[:12]}…"


def _open_pool() -> None:
    """Пул в этом проекте создаётся закрытым (его открывает FastAPI) — открываем сами."""
    opener = getattr(core.pool, "open", None)
    if opener is not None:
        opener()


def _close_pool() -> None:
    closer = getattr(core.pool, "close", None)
    if closer is not None:
        closer()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Засев привязок листов бота (186 §4.3)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="сверить строки в базе (по умолчанию), Google не вызывается")
    mode.add_argument("--apply", action="store_true", help="прочитать Google и записать")
    mode.add_argument("--dry-run", action="store_true", help="как --apply, но без записи")
    parser.add_argument("--all", action="store_true",
                        help="включая привязки живых листов (чтение)")
    parser.add_argument("--only", action="append", default=[],
                        help="ключ реестра (можно несколько раз)")
    parser.add_argument("--page", default="",
                        help="имя страницы тестового листа цветов (например «11.2026 · Дарья»); "
                             "без него берётся единственная страница под шаблоном")
    args = parser.parse_args(argv)

    allowed = WRITE_SHEET_KEYS + READ_SHEET_KEYS
    keys = tuple(args.only) or (allowed if (args.apply or args.dry_run or args.all)
                                else WRITE_SHEET_KEYS)
    unknown = [key for key in keys if key not in allowed]
    if unknown:
        print(f"нет в реестре листов: {', '.join(unknown)}")
        return 1

    if args.check or not (args.apply or args.dry_run):
        problems = 0
        _open_pool()
        try:
            with core.pool.connection() as conn:
                for key in keys:
                    ok, text = _check_one(conn, key)
                    print(f"{key}: {text}")
                    problems += 0 if ok else 1
        finally:
            _close_pool()
        return 0 if problems == 0 else 1

    if not core.GOOGLE_SA_JSON:
        print("нет ключа сервисного аккаунта (GOOGLE_SA_JSON) — нечего читать")
        return 2

    problems = 0
    _open_pool()
    try:
        with core.pool.connection() as conn:
            for key in keys:
                outcome = _seed_one(conn, key, dry_run=args.dry_run, page=args.page)
                print(f"{key}: {outcome}")
                if not outcome.startswith("ok"):
                    problems += 1
                if args.dry_run:
                    conn.rollback()
    finally:
        _close_pool()
    return 0 if problems == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
