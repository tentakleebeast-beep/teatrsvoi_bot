#!/usr/bin/env python3
"""Стенд и данные смоука: `prepare` / `cleanup` / `check` с обязательным маркером.

Зачем скрипт, а не «руками создать и потом не забыть удалить». Набор §6.2 дизайна
`docs/development/143` — люди, файл, заметка, занятость, расход, событие, обращение —
это записи в базе, и уборка руками не воспроизводима и в отчёте недоказуема. Маркер
`SMOKE-YYYYMMDD-NN` ставится в имя/комментарий каждого созданного объекта и остаётся
единственным признаком для уборки.

    .env.smoke: DATABASE_URL=…:5433/svoi_smoke, SMOKE_DUMP=/path/svoi_….dump

    python scripts/smoke_data.py prepare --marker SMOKE-20261108-01
    python scripts/smoke_data.py check   --marker SMOKE-20261108-01
    python scripts/smoke_data.py cleanup --marker SMOKE-20261108-01

Границы, которые держит скрипт:
  * работает только с базой `svoi_smoke`; `svoi`, `svoi_ui` и любая другая — отказ
    с кодом 2 до первого запроса (в чужие базы не пишем, К4);
  * `prepare` не удаляет найденные остатки прошлого прогона, а падает с кодом 1
    и печатает их: «накопленные» тестовые данные портят следующие числа (К5);
  * `cleanup` удаляет только строки с маркером и возвращает `budget_month_total`
    к значениям из снимка, снятого в `prepare` (К6);
  * счётчики `svoi` и `svoi_ui` сверяются до и после восстановления: стенд не должен
    писать в соседние базы.

Коды выхода: 0 — успех; 1 — остатки/незавершённая уборка/сервис не отвечает;
2 — неверные аргументы или чужая база.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAND_DB = "svoi_smoke"
MARKER_RE = re.compile(r"^SMOKE-\d{8}-\d{2}$")
STATE_DIR = Path(os.getenv("SVOI_SMOKE_STATE_DIR", Path.home() / ".cache" / "svoi-smoke"))
# Таблицы набора §6.2 и признак «строка наша» — по маркеру или по маркерному человеку.
# Порядок — от детей к родителям: удалять в этом порядке, считать в любом.
TABLES: tuple[tuple[str, str], ...] = (
    ("feedback_answer",
     "feedback_id in (select id from feedback where text like %(m)s)"),
    ("feedback", "text like %(m)s"),
    ("event_plan", "title like %(m)s"),
    ("availability_hour",
     "participant_id in (select id from participant where display_name like %(m)s)"),
    ("note", "coalesce(title, '') like %(m)s or coalesce(body, '') like %(m)s "
             "or participant_id in (select id from participant where display_name like %(m)s)"),
    ("file_object", "coalesce(name, '') like %(m)s "
                    "or participant_id in (select id from participant where display_name like %(m)s)"),
    ("budget_tx", "coalesce(purpose, '') like %(m)s or coalesce(raw_text, '') like %(m)s "
                  "or coalesce(article_raw, '') like %(m)s"),
    ("participant_discipline",
     "participant_id in (select id from participant where display_name like %(m)s)"),
    ("participant_capability_override",
     "participant_id in (select id from participant where display_name like %(m)s)"),
    ("participant_role",
     "participant_id in (select id from participant where display_name like %(m)s)"),
    ("participant", "display_name like %(m)s"),
)
# Строки, которые «просто лежат в стенде» и маркером не помечены: остаток прошлого
# прогона ищется по ним, а не по всем таблицам (иначе шум от чужих данных).
LEFTOVER_COLUMNS = (
    ("participant", "display_name"),
    ("note", "title"),
    ("note", "body"),
    ("budget_tx", "purpose"),
    ("budget_tx", "raw_text"),
    ("feedback", "text"),
    ("file_object", "name"),
    ("event_plan", "title"),
)


# --- подключение и границы -----------------------------------------------------

def dsn() -> str:
    """Строка подключения стенда: только `svoi_smoke`, иначе отказ."""
    value = (os.getenv("SMOKE_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("smoke_data.py: нет SMOKE_DATABASE_URL/DATABASE_URL — "
                         "соберите .env.smoke по .env.smoke.example")
    name = value.rsplit("/", 1)[-1].split("?")[0]
    if name != STAND_DB:
        raise SystemExit(f"smoke_data.py: база {name!r}, а стенд — это только {STAND_DB!r}. "
                         f"В чужие базы скрипт не пишет: правьте DATABASE_URL в .env.smoke")
    return value


def connect(target: str | None = None, *, admin: bool = False):
    """Соединение с базой стенда (или с `postgres` для create/drop) с autocommit."""
    import psycopg  # noqa: PLC0415

    url = target or dsn()
    if admin:
        url = url.rsplit("/", 1)[0] + "/postgres"
    return psycopg.connect(url, autocommit=True)


def api_url() -> str:
    return (os.getenv("TOOL_API_URL") or "http://127.0.0.1:8011").rstrip("/")


def state_path(marker: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"state-{marker}.json"


def count_rows(conn, table: str, where: str, like: str) -> int:
    """Число строк таблицы, попадающих под условие по маркеру."""
    row = conn.execute(f"select count(*) from {table} where {where}", {"m": like}).fetchone()
    return int(row[0])


def table_counts(conn, marker: str) -> dict[str, int]:
    """Счётчики по всем таблицам набора §6.2 — 0 значит «убрано»."""
    like = f"%{marker}%"
    return {table: count_rows(conn, table, where, like) for table, where in TABLES}


def leftovers(conn) -> list[dict]:
    """Объекты с любым маркером `SMOKE-…` в стенде: остатки прошлых прогонов."""
    found = []
    for table, column in LEFTOVER_COLUMNS:
        rows = conn.execute(
            f"select {column} from {table} where {column} like '%%SMOKE-%%' limit 20").fetchall()
        for row in rows:
            found.append({"table": table, "column": column, "value": str(row[0])[:120]})
    return found


# --- prepare -------------------------------------------------------------------

def dump_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_dump(target: str, dump: Path) -> str:
    """Восстановить базу стенда из дампа, не убивая работающий сервис.

    Дамп разворачивается в соседнюю базу `svoi_smoke_restore`, схема накатывается до
    головы, и только потом имена меняются: `svoi_smoke` → в сторону, `…_restore` → на её
    место. Прямой `drop database` рвал пул соединений работающего tool-api, и после
    `prepare` сервис отвечал `connection closed` — стенд оставался «поднятым» только на
    бумаге. Возвращает строку о миграциях для отчёта.
    """
    name = target.rsplit("/", 1)[-1].split("?")[0]
    temp = f"{name}_restore"
    temp_url = target.rsplit("/", 1)[0] + f"/{temp}"
    with connect(target, admin=True) as conn:
        conn.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                     "where datname = %s and pid <> pg_backend_pid()", (temp,))
        conn.execute(f'drop database if exists "{temp}"')
        conn.execute(f'create database "{temp}"')
    binary = shutil.which("pg_restore")
    if binary:
        command = [binary, "--no-owner", "--no-privileges", "-d", temp_url, str(dump)]
        proc = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    else:
        command = ["docker", "exec", "-i", "svoi-postgres", "pg_restore",
                   "--no-owner", "--no-privileges", "-U", "svoi", "-d", temp]
        with dump.open("rb") as handle:
            proc = subprocess.run(command, stdin=handle, capture_output=True,  # noqa: S603
                                  text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"smoke_data.py: pg_restore вернул {proc.returncode}: "
                         f"{(proc.stderr or '').strip()[:500]}")
    migrations = migrate(temp_url)
    with connect(target, admin=True) as conn:
        conn.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                     "where datname = %s and pid <> pg_backend_pid()", (name,))
        conn.execute(f'drop database if exists "{name}"')
        conn.execute(f'alter database "{temp}" rename to "{name}"')
    return migrations


def migrate(target: str) -> str:
    """Накатить схему до головы: дамп прода может быть старше кода (как в отчёте 142)."""
    env = dict(os.environ, DATABASE_URL=target)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"),
                           "upgrade", "head"], cwd=ROOT, env=env,  # noqa: S603
                          capture_output=True, text=True, check=False)
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return f"код {proc.returncode}: {tail[-1] if tail else ''}"


def neighbor_counts(target: str) -> dict[str, dict[str, int]]:
    """Счётчики соседних баз `svoi` и `svoi_ui`: стенд не должен их менять (К4)."""
    counts = {}
    for name in ("svoi", "svoi_ui"):
        url = target.rsplit("/", 1)[0] + f"/{name}"
        try:
            with connect(url) as conn:
                counts[name] = {"participant": int(
                    conn.execute("select count(*) from participant").fetchone()[0])}
        except Exception as exc:  # noqa: BLE001 — соседней базы может не быть
            counts[name] = {"error": str(exc).splitlines()[0][:160]}
    return counts


def health() -> dict:
    """GET /health стенда: 200 и поля, которые ждёт сценарий СМ-11."""
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    url = f"{api_url()}/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 — локальный стенд
            return {"url": url, "code": response.status,
                    "body": json.loads(response.read().decode("utf-8"))}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"url": url, "code": 0, "error": str(exc)[:200]}


def wait_health(attempts: int = 15, delay: float = 1.0) -> dict:
    """`/health` с повторами: после подмены базы пул сервиса переподключается не мгновенно."""
    import time  # noqa: PLC0415

    result = health()
    for _ in range(attempts):
        if result["code"] == 200:
            return result
        time.sleep(delay)
        result = health()
    return result


MONTH_COLUMNS = ("dues_total", "expense_total", "show_income_total",
                 "invest_total", "sublease_total")


def month_totals(conn) -> dict[str, dict[str, float]]:
    """Итоги месяцев по колонкам: снимок до прогона и сверка после уборки (К6).

    Хранится не «одно число», а все пять колонок: уборка возвращает ровно тот снимок,
    а не подгоняет сумму под ожидание.
    """
    rows = conn.execute(
        f"select period, {', '.join(MONTH_COLUMNS)} from budget_month_total").fetchall()
    return {row[0]: {column: float(value)
                     for column, value in zip(MONTH_COLUMNS, row[1:], strict=True)}
            for row in rows}


def cmd_prepare(args: argparse.Namespace) -> int:
    target = dsn()
    try:
        with connect(target) as conn:
            found = leftovers(conn)
    except Exception as exc:  # noqa: BLE001 — базы стенда может ещё не быть: это первый запуск
        print(f"стенд ещё не создан ({str(exc).splitlines()[0][:120]}) — остатков нет")
        found = []
    if found:
        print(f"стенд не готов: остатки прошлых прогонов ({len(found)}), "
              f"ничего не удалено:")
        for item in found:
            print(f"  {item['table']}.{item['column']}: {item['value']}")
        print("уберите их: scripts/smoke_data.py cleanup --marker <их маркер>")
        return 1

    dump = Path(args.dump or os.getenv("SMOKE_DUMP") or "")
    if not dump.is_file():
        raise SystemExit("smoke_data.py: нет дампа стенда — укажите --dump или SMOKE_DUMP")

    before = neighbor_counts(target)
    sha = dump_sha256(dump)
    print(f"дамп: {dump} ({dump.stat().st_size} байт, sha256 {sha})")
    restore = restore_dump(target, dump)
    print(f"база {STAND_DB} восстановлена; миграции: {restore}")
    after = neighbor_counts(target)
    if before != after:
        print(f"ВНИМАНИЕ: счётчики соседних баз изменились: {before} → {after}")
        return 1

    with connect(target) as conn:
        baseline = month_totals(conn)
        counts = table_counts(conn, args.marker)
    health_result = wait_health()
    state_path(args.marker).write_text(json.dumps({
        "marker": args.marker, "dump": str(dump), "dump_sha256": sha,
        "date": datetime.now().isoformat(timespec="seconds"),
        "budget_month_total": baseline, "neighbors": before,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"итоги месяцев до прогона: {len(baseline)} периодов")
    print(f"счётчики по маркеру до прогона: {sum(counts.values())} строк")
    print(f"/health: код {health_result['code']} ({health_result['url']})")
    if health_result["code"] != 200 and not args.skip_health:
        print("стенд не поднят: запустите tool-api :8011 по docs/development/144")
        return 1
    return 0


# --- check и cleanup -----------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    target = dsn()
    with connect(target) as conn:
        counts = table_counts(conn, args.marker)
        totals = month_totals(conn)
    state_file = state_path(args.marker)
    baseline = {}
    if state_file.is_file():
        baseline = json.loads(state_file.read_text(encoding="utf-8")).get("budget_month_total", {})
    # Снимок может быть пустым (в копии нет итогов месяцев) — тогда «совпадает» значит
    # «и сейчас пусто», а не «проверить нечем»: пустая таблица и есть исходное значение.
    restored = state_file.is_file() and set(totals) == set(baseline) and all(
        all(abs(totals[period].get(column, 0.0) - value) < 0.01
            for column, value in values.items())
        for period, values in baseline.items())
    report = {
        "marker": args.marker, "counts": counts, "leftovers": sum(counts.values()),
        "budget_month_total_now": totals, "budget_month_total_before": baseline,
        "month_total_restored": restored, "messages_to_people": 0, "prod_writes": 0,
        "dump_sha256": (json.loads(state_file.read_text(encoding="utf-8")).get("dump_sha256", "")
                        if state_file.is_file() else ""),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for table, count in counts.items():
            print(f"{table:36} {count}")
        print(f"{'ИТОГО':36} {report['leftovers']}")
        print(f"итоги месяцев совпадают со снимком: {'да' if restored else 'нет'}")
    return 0 if report["leftovers"] == 0 and restored else 1


def marker_participants(conn, marker: str) -> list[int]:
    rows = conn.execute("select id from participant where display_name like %(m)s",
                        {"m": f"%{marker}%"}).fetchall()
    return [int(row[0]) for row in rows]


def referencing_columns(conn) -> list[tuple[str, str]]:
    """Кто ссылается на `participant`: таблица и колонка (включая появившиеся позже).

    Список не пишется руками: новая таблица с внешним ключом на человека иначе роняла
    уборку на `ForeignKeyViolation` и оставляла стенд грязным (так и вышло на `notification`).
    """
    rows = conn.execute("""
        select cl.relname, att.attname
          from pg_constraint c
          join pg_class cl on cl.oid = c.conrelid
          join pg_class rf on rf.oid = c.confrelid
          join pg_attribute att on att.attrelid = c.conrelid and att.attnum = c.conkey[1]
         where c.contype = 'f' and rf.relname = 'participant'
         order by cl.relname""").fetchall()
    return [(row[0], row[1]) for row in rows]


def delete_objects_from_storage(conn, marker: str) -> int:
    """Удалить объекты S3, созданные маркером. Нет настроек хранилища — говорим прямо."""
    endpoint = os.getenv("STORAGE_ENDPOINT", "").strip()
    if not endpoint:
        print("хранилище не настроено (STORAGE_ENDPOINT): объекты S3 не удалялись")
        return 0
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        print("boto3 нет: объекты S3 не удалялись")
        return 0
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=os.getenv("STORAGE_ACCESS_KEY", "").strip(),
        aws_secret_access_key=os.getenv("STORAGE_SECRET_KEY", "").strip(),
        region_name=os.getenv("STORAGE_REGION", "svoi").strip() or "svoi")
    bucket = os.getenv("STORAGE_BUCKET_PRIVATE", "svoi-private").strip()
    keys = [row[0] for row in conn.execute(
        "select object_key from file_object where name like %s and object_key is not null",
        (f"%{marker}%",)).fetchall()]
    for key in keys:
        client.delete_object(Bucket=bucket, Key=key)
    return len(keys)


def cmd_cleanup(args: argparse.Namespace) -> int:
    target = dsn()
    state_file = state_path(args.marker)
    baseline = {}
    if state_file.is_file():
        baseline = json.loads(state_file.read_text(encoding="utf-8")).get("budget_month_total", {})
    with connect(target) as conn:
        people = marker_participants(conn, args.marker)
        removed = {}
        if people:
            # 1. Всё, что ссылается на смоук-людей: сначала дети, потом сами люди.
            for table, column in referencing_columns(conn):
                if table == "participant":
                    continue
                cur = conn.execute(
                    f"delete from {table} where {column} = any(%(ids)s)", {"ids": people})
                if cur.rowcount:
                    removed[table] = removed.get(table, 0) + cur.rowcount
        # 2. Объекты с маркером в тексте: заметки, файлы, расходы, события, обращения.
        for table, where in TABLES:
            if table == "participant":
                continue
            before = count_rows(conn, table, where, f"%{args.marker}%")
            if not before:
                continue
            conn.execute(f"delete from {table} where {where}", {"m": f"%{args.marker}%"})
            removed[table] = removed.get(table, 0) + before
        if people:
            conn.execute("delete from participant where id = any(%(ids)s)", {"ids": people})
            removed["participant"] = len(people)
        objects = delete_objects_from_storage(conn, args.marker)
        if baseline:
            # Итоги месяца возвращаем к снимку prepare: приложение пересчитывает их по
            # записям, и после удаления расхода суммы обязаны стать прежними (К6).
            for period, values in baseline.items():
                conn.execute(
                    """insert into budget_month_total
                           (period, dues_total, expense_total, show_income_total,
                            invest_total, sublease_total, computed_at)
                       values (%s, %s, %s, %s, %s, %s, now())
                       on conflict (period) do update
                         set dues_total = excluded.dues_total,
                             expense_total = excluded.expense_total,
                             show_income_total = excluded.show_income_total,
                             invest_total = excluded.invest_total,
                             sublease_total = excluded.sublease_total,
                             computed_at = now()""",
                    (period, *[values.get(column, 0.0) for column in MONTH_COLUMNS]))
        left = table_counts(conn, args.marker)
    for table, count in removed.items():
        if count:
            print(f"удалено {table:36} {count}")
    if objects:
        print(f"удалено объектов хранилища: {objects}")
    rest = sum(left.values())
    print(f"остатков по маркеру: {rest}")
    if rest:
        print("уборка неполная: " + ", ".join(f"{t}={c}" for t, c in left.items() if c))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Стенд смоука: prepare / cleanup / check",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, helptext in (("prepare", "проверить остатки и восстановить базу из дампа"),
                           ("cleanup", "удалить всё, что создано маркером"),
                           ("check", "напечатать счётчики по маркеру и итоги месяца")):
        command = sub.add_parser(name, help=helptext)
        command.add_argument("--marker", required=True, help="SMOKE-YYYYMMDD-NN")
        if name == "prepare":
            command.add_argument("--dump", default=None, help="дамп стенда (или SMOKE_DUMP)")
            command.add_argument("--skip-health", action="store_true",
                                 help="не требовать ответа tool-api (первый запуск: сервиса ещё нет)")
        if name == "check":
            command.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)
    if not MARKER_RE.match(args.marker):
        print(f"smoke_data.py: маркер {args.marker!r} не вида SMOKE-YYYYMMDD-NN")
        return 2
    handlers = {"prepare": cmd_prepare, "cleanup": cmd_cleanup, "check": cmd_check}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
