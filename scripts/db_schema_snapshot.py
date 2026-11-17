#!/usr/bin/env python3
"""Снимок схемы БД: генерация и проверка (docs/architecture/169).

Зачем: документы про данные старели молча. `db/init/*.sql` неизменяемы, но ничто не
сверяло с ними тексты: в `docs/architecture/01` и `10` остались таблицы, которых в
живой базе нет, а новые (`poll*`, `feedback*`, `file_*`) там не упомянуты. Здесь
снимок фактической схемы Postgres — таблицы, колонки с типами, первичные, уникальные
и CHECK-ограничения, внешние ключи, представления. Расхождение — код 1 и точечный дифф.

  env -u PYTHONPATH .venv/bin/python scripts/db_schema_snapshot.py           # проверка
  env -u PYTHONPATH .venv/bin/python scripts/db_schema_snapshot.py --write   # обновление
  env -u PYTHONPATH .venv/bin/python scripts/db_schema_snapshot.py --dot docs/architecture/169-db-schema.dot

В снимок не входят индексы, последовательности, владельцы прав и данные: смена индекса
не делает документ устаревшим. Номер ревизии пишется в `meta` (в диффе не участвует):
снимок про форму схемы, а не про то, какая миграция приехала последней.

Строка подключения — общая для скриптов (`scripts/_cli.dsn`): окружение → `.env` →
локальная база разработки.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from _cli import dsn

PROJECT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT / "contracts" / "db-schema.json"

TABLES_SQL = """
select c.relname as name, a.attname as col, format_type(a.atttypid, a.atttypmod) as type,
       a.attnotnull as not_null, pg_get_expr(d.adbin, d.adrelid) as default
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
  left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
 where n.nspname = 'public' and c.relkind = 'r'
 order by c.relname, a.attnum
"""

CONSTRAINTS_SQL = """
select conrelid::regclass::text as tbl, contype, pg_get_constraintdef(oid) as def
  from pg_constraint
 where connamespace = 'public'::regnamespace and contype in ('p', 'u', 'c', 'f')
 order by 1, contype, 2
"""

VIEWS_SQL = """
select viewname as name, regexp_replace(definition, '\\s+', ' ', 'g') as def
  from pg_views where schemaname = 'public' order by 1
"""

ALEMBIC_SQL = "select version_num from alembic_version"

# Домены для картинки (docs/architecture/169 §2). Таблица, которой здесь нет, попадает
# в «Прочее» — так новая миграция видна на карте сразу, без правки скрипта.
DOMAINS: dict[str, tuple[str, ...]] = {
    "Люди и доступ": (
        "participant", "participant_role", "participant_discipline",
        "participant_capability_override", "rbac_capability", "rbac_role_capability",
        "invite_code", "invite_attempt", "discipline", "project", "person_alias",
        "venue",
    ),
    "Деньги": (
        "budget_period", "budget_plan", "budget_tx", "budget_month_total", "category",
        "category_candidate", "article_map", "article_alias", "finance_granularity",
        "finance_source_map", "dues_plan", "dues_charge",
    ),
    "Занятость и события": (
        "availability_hour", "availability_template", "event_plan", "slot_proposal",
    ),
    "Google Sheets": ("sheet_binding", "row_map", "import_batch", "import_row"),
    "Файлы и заметки": (
        "file_object", "file_text", "file_audio", "folder", "file_folder", "tag",
        "file_tag", "access_grant", "note",
    ),
    "Обратная связь": ("feedback", "feedback_answer", "feedback_attachment"),
    "Опросы": (
        "poll", "poll_question", "poll_option", "poll_audience", "poll_person",
        "poll_run", "poll_viewer", "poll_answer",
    ),
    "Агент и LLM": (
        "agent_action", "agent_proposal", "intent_log", "llm_policy", "llm_usage",
        "approval_request", "job",
    ),
    "Уведомления и аудит": (
        "notification", "notification_pref", "audit_event", "app_setting",
        "migration_marker",
    ),
}


def snapshot(conn: psycopg.Connection[Any]) -> dict:
    """Схема как словарь: таблица → колонки, ключи, проверки, внешние ключи."""
    tables: dict[str, dict] = {}
    for r in conn.execute(TABLES_SQL):
        t = tables.setdefault(
            r["name"],
            {"columns": {}, "primary_key": [], "unique": [], "check": [], "foreign_keys": []},
        )
        t["columns"][r["col"]] = {
            "type": r["type"],
            "not_null": r["not_null"],
            "default": r["default"],
        }

    for r in conn.execute(CONSTRAINTS_SQL):
        t = tables.get(r["tbl"])
        if t is None:
            continue
        kind, definition = r["contype"], r["def"]
        if kind == "p":
            t["primary_key"] = _cols(definition)
        elif kind == "u":
            t["unique"].append(_cols(definition))
        elif kind == "c":
            t["check"].append(definition)
        else:
            t["foreign_keys"].append(definition)

    views = {r["name"]: r["def"] for r in conn.execute(VIEWS_SQL)}
    alembic = conn.execute(ALEMBIC_SQL).fetchall()
    return {
        "meta": {"alembic": alembic[0]["version_num"] if alembic else None,
                 "note": "Снимок формы схемы. Индексы, права и данные не входят. "
                         "Описание — docs/architecture/169-db-schema.md."},
        "tables": tables,
        "views": views,
    }


def _cols(definition: str) -> list[str]:
    """Колонки из `PRIMARY KEY (a, b)` / `UNIQUE (a, b)` — без кавычек и сортировки."""
    inner = definition[definition.index("(") + 1: definition.rindex(")")]
    return [c.strip().strip('"') for c in inner.split(",")]


def diff(expected: object, actual: object, path: str = "") -> list[str]:
    """Различия двух снимков строками `путь: было → стало` (порядок списков значим)."""
    if type(expected) is not type(actual):
        return [f"{path}: {expected!r} → {actual!r}"]
    if isinstance(expected, dict):
        out = []
        for key in sorted(set(expected) | set(actual)):
            where = f"{path}.{key}" if path else str(key)
            if key not in expected:
                out.append(f"{where}: нет в снимке → {actual[key]!r}")
            elif key not in actual:
                out.append(f"{where}: {expected[key]!r} → нет в базе")
            else:
                out.extend(diff(expected[key], actual[key], where))
        return out
    if isinstance(expected, list):
        if expected == actual:
            return []
        return [f"{path}: {expected!r} → {actual!r}"]
    return [] if expected == actual else [f"{path}: {expected!r} → {actual!r}"]


def to_dot(snap: dict) -> str:
    """Карта схемы: домены — кластеры, таблицы — рамки, внешние ключи — стрелки."""
    known = {t for group in DOMAINS.values() for t in group}
    groups = dict(DOMAINS)
    rest = sorted(set(snap["tables"]) - known)
    if rest:
        groups["Прочее"] = tuple(rest)

    version = snap["meta"]["alembic"]
    lines = [
        "digraph db {",
        '  graph [bgcolor="#ffffff", rankdir=TB, fontname="Helvetica", fontsize=11,',
        '         nodesep=0.14, ranksep=0.34,',
        f'         compound=true, label="Схема БД СВОИ — ревизия {version}",',
        '         labelloc=t, fontsize=16];',
        '  node [shape=plaintext, fontname="Helvetica", fontsize=10];',
        '  edge [color="#8a8a8a", arrowsize=0.6, penwidth=0.8];',
    ]
    for i, (domain, names) in enumerate(groups.items()):
        lines.append(f'  subgraph cluster_{i} {{')
        lines.append(f'    label="{domain}"; fontsize=12; style="rounded"; color="#bbbbbb";')
        for name in names:
            if name not in snap["tables"]:
                continue
            lines.append(f'    "{name}" [label=<{_html_table(name, snap["tables"][name])}>];')
        lines.append("  }")

    for name, table in sorted(snap["tables"].items()):
        for fk in table["foreign_keys"]:
            target = fk.split("REFERENCES ")[1].split("(")[0].strip().strip('"')
            if target in snap["tables"]:
                lines.append(f'  "{name}" -> "{target}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _html_table(name: str, table: dict) -> str:
    """Таблица в HTML-подобной метке graphviz: имя жирным, ниже — ключевые колонки."""
    rows = [f'<tr><td bgcolor="#eef1f5"><b>{name}</b></td></tr>']
    for col in table["primary_key"]:
        kind = table["columns"].get(col, {}).get("type", "?")
        rows.append(f'<tr><td align="left"><u>{col}</u> : {_short(kind)}</td></tr>')
    fk_cols = {c for fk in table["foreign_keys"] for c in _fk_cols(fk)}
    for col in sorted(fk_cols - set(table["primary_key"])):
        kind = table["columns"].get(col, {}).get("type", "?")
        rows.append(f'<tr><td align="left">{col} : {_short(kind)}</td></tr>')
    others = len(table["columns"]) - len(table["primary_key"]) - len(fk_cols - set(table["primary_key"]))
    if others > 0:
        rows.append(f'<tr><td align="left"><font color="#777777">… ещё {others}</font></td></tr>')
    return ('<table border="0" cellborder="1" cellspacing="0" cellpadding="3">'
            + "".join(rows) + "</table>")


def _fk_cols(definition: str) -> list[str]:
    return _cols(definition[: definition.index("REFERENCES")])


def _short(kind: str) -> str:
    return {"character varying": "varchar", "timestamp with time zone": "timestamptz",
            "time without time zone": "time"}.get(kind, kind)


def render_dot(dot_path: Path, snap: dict) -> None:
    dot_path.write_text(to_dot(snap), encoding="utf-8")
    svg = dot_path.with_suffix(".svg")
    subprocess.run(["dot", "-Tsvg", str(dot_path), "-o", str(svg)], check=True)
    print(f"написано: {dot_path}\nнаписано: {svg}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="перезаписать снимок")
    parser.add_argument("--dot", metavar="PATH", help="нарисовать карту схемы (svg рядом)")
    args = parser.parse_args(argv)

    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        actual = snapshot(conn)

    if args.dot:
        render_dot(Path(args.dot), actual)
        return 0

    text = json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.write:
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(text, encoding="utf-8")
        print(f"написано: {SNAPSHOT_PATH} "
              f"({len(actual['tables'])} таблиц, {len(actual['views'])} представлений)")
        return 0

    if not SNAPSHOT_PATH.exists():
        print(f"нет снимка {SNAPSHOT_PATH}; создайте: --write")
        return 2
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    changes = diff({k: v for k, v in expected.items() if k != "meta"},
                   {k: v for k, v in actual.items() if k != "meta"})
    if not changes:
        print(f"схема совпадает со снимком: {len(actual['tables'])} таблиц, "
              f"{len(actual['views'])} представлений")
        return 0

    print("\n".join(difflib.unified_diff(
        json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True).splitlines(),
        json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True).splitlines(),
        "снимок contracts/db-schema.json", "живая база", n=1, lineterm="")))
    print(f"\nрасхождений: {len(changes)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
