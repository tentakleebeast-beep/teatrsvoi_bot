#!/usr/bin/env python3
"""Сверяет живую базу с миграциями в db/init — без изменений данных.

Зачем: восстановленная из дампа база может быть позади кода. Так было на проде
30.10.2026 — не хватало `participant.write_gender` и `budget_tx.is_plan`, и Mini App
падал с 500 на неделе занятости и на записи операции. Полный прогон цепочки
`db --migrate` на такой базе не подходит: часть миграций рассчитана на порядок
«от чистой схемы» (004 упирается в уникальность алиаса, которую позже ослабили).

    DATABASE_URL=... python3 deploy/check_schema.py            # только отчёт
    DATABASE_URL=... python3 deploy/check_schema.py --files    # плюс список файлов
    DATABASE_URL=... python3 deploy/check_schema.py --expect-head  # версия = head
    DATABASE_URL=... python3 deploy/check_schema.py --root /tmp/тег --expect-head  # head чужого дерева
    DATABASE_URL=... python3 deploy/check_schema.py --stamp-head --files "db/init/061_master_role.sql"

`--expect-head` — read-only сверка `alembic_version` с головой цепочки ревизий
(docs/reports/132). Ею baseline после восстановления из дампа: `alembic stamp head`
ставится только когда структура уже равна цепочке, иначе выкат останавливается.
Строка «схема впереди/позади» называет, в какую сторону разошлось: впереди — понижающих
миграций нет и код старше базы не стартует (откат выката, docs/infrastructure/35 §10.3).

`--root PATH` — взять `alembic.ini` и цепочку ревизий из другого дерева: откат выката
сверяет базу с head выбранного тега, вытащенного `git archive`, не делая checkout.

`--stamp-head` — та же сверка структуры, но с записью учёта: SQL, накатанный
напрямую через psql (workflow `repair-schema.yml`), структуру меняет, а
`alembic_version` — нет, и следующий выкат падает на сверке версии и откатывается
(08.11.2026, v0.8.0). Флаг ставит версию на ревизию, соответствующую применённым
файлам (`--files`), либо на head цепочки, если файлы не названы. Порядок жёсткий:
сначала структура должна совпасть с db/init, потом двигается версия — «вперёд
структуры» версия не уходит и назад не откатывается.

`--env-file PATH` — взять `DATABASE_URL` из файла настроек, если переменной нет в
окружении: на сервере настройки лежат в `/opt/svoi/.env`, а не в шелле раннера.

Код возврата 1, если чего-то не хватает или версия не равна head.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

INIT_DIR = Path(__file__).resolve().parents[1] / "db" / "init"
ROOT = INIT_DIR.parents[1]
COLUMN_RE = re.compile(r"alter\s+table\s+(\w+)\s+add\s+column\s+if\s+not\s+exists\s+(\w+)", re.I)
TABLE_RE = re.compile(r"create\s+table\s+if\s+not\s+exists\s+(\w+)", re.I)


def expected() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Что требуют миграции: таблицы и колонки, с указанием файла."""
    tables: dict[str, set[str]] = {}
    columns: dict[str, set[str]] = {}
    for path in sorted(INIT_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for name in TABLE_RE.findall(text):
            tables.setdefault(name, set()).add(path.name)
        for table, column in COLUMN_RE.findall(text):
            columns.setdefault(f"{table}.{column}", set()).add(path.name)
    return tables, columns


def script_directory(root: Path):
    """Каталог ревизий Alembic: источник head и родословной версий."""
    from alembic.config import Config  # noqa: PLC0415 — нужен только с --expect-head/--stamp-head
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(root / "alembic.ini")))


def head_revision(root: Path) -> str:
    """Голова цепочки ревизий из файлов, без подключения к базе."""
    heads = script_directory(root).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"цепочка ревизий разветвлена: {sorted(heads)}")
    return heads[0]


def chain(root: Path) -> list[str]:
    """Ревизии от головы к началу цепочки: по ней считается «впереди/позади»."""
    script = script_directory(root)
    head = head_revision(root)
    return [rev.revision for rev in script.iterate_revisions(head, "base")]


def current_version(conn: psycopg.Connection) -> str | None:
    """Версия схемы из alembic_version; None — таблицы нет."""
    with conn.cursor() as cur:
        cur.execute("select to_regclass('public.alembic_version')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("select version_num from alembic_version")
        rows = cur.fetchall()
    return rows[0][0] if len(rows) == 1 else None


def structure_gaps(url: str) -> tuple[list[str], list[str]]:
    """Чего не хватает базе по сравнению с db/init: таблицы и колонки."""
    want_tables, want_columns = expected()
    with psycopg.connect(url) as conn:
        have_tables, have_columns = live(conn)
    return (sorted(set(want_tables) - have_tables),
            sorted(set(want_columns) - have_columns))


def revision_position(version: str, chain_list: list[str]) -> str:
    """Где версия базы относительно head этого дерева: equal, behind или ahead.

    Цепочка линейная (docs/reports/132): `chain_list[0]` — head, дальше старше.
    Версия, которой в цепочке нет, применена позже её head — схема впереди кода.
    Это и есть случай отката выката: понижающих миграций нет, старый код не стартует.
    """
    if version == chain_list[0]:
        return "equal"
    return "behind" if version in chain_list else "ahead"


POSITION_HINTS = {
    "ahead": "схема впереди тега: ревизия новее его head, понижающих миграций нет — "
             "код старше базы не стартует (опускать базу: restore из дампа или alembic downgrade)",
    "behind": "схема позади тега: ревизии тега не накатаны — накатите миграции "
              "(server-deploy.sh db --migrate)",
}


def check_expect_head(url: str, root: Path) -> int:
    """0 — версия базы равна head цепочки; 1 — отстала, впереди или учёта нет."""
    with psycopg.connect(url) as conn:
        version = current_version(conn)
    head = head_revision(root)
    print(version_line(version, head))
    if version is None:
        print(f"схема не применена: в alembic_version нет версии (head={head})")
        return 1
    if version != head:
        position = revision_position(version, chain(root))
        print(f"версия схемы {version} не равна head {head}")
        print(POSITION_HINTS[position])
        return 1
    print(f"схема совпадает с Alembic: {head}")
    return 0


def version_line(version: str | None, head: str) -> str:
    """Строка в духе `alembic current`: по ней видно «SQL есть, учёта нет»."""
    return f"alembic current: {version or 'нет'} | head: {head}"


def target_revision(chain_list: list[str], files: list[str]) -> tuple[str, list[str]]:
    """Какую ревизию ставить: максимум по применённым файлам, иначе head.

    Имя файла `db/init/NNN_x.sql` — это и есть id ревизии (docs/reports/132).
    Возвращает (ревизия, файлы без соответствующей ревизии).
    """
    known = set(chain_list)
    mapped: list[str] = []
    unknown: list[str] = []
    for item in files:
        stem = Path(item).stem
        if stem in known:
            mapped.append(stem)
        else:
            unknown.append(item)
    if not mapped:
        raise ValueError("ни один файл не соответствует ревизии Alembic: " + ", ".join(files))
    order = {rev: index for index, rev in enumerate(chain_list)}
    return max(mapped, key=lambda rev: order[rev]), unknown


def stamp(url: str, root: Path, revision: str) -> None:
    """Записать версию схемы тем же механизмом, что и деплой (`alembic stamp`)."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config

    cfg = Config(str(root / "alembic.ini"))
    cfg.attributes["url"] = url
    command.stamp(cfg, revision)


def stamp_head(url: str, root: Path, files: list[str]) -> int:
    """Довести учёт версии до применённой структуры — после psql-наката."""
    chain_list = chain(root)
    head = chain_list[0]
    try:
        target, unknown = target_revision(chain_list, files) if files else (head, [])
    except ValueError as exc:
        print(str(exc))
        return 1
    for item in unknown:
        print(f"файл {item} не меняет версию схемы: ревизии с таким id в цепочке нет")

    missing_tables, missing_columns = structure_gaps(url)
    if missing_tables or missing_columns:
        # Версия не обгоняет структуру: иначе выкат пройдёт сверку и упадёт на данных.
        for name in missing_tables:
            print(f"нет таблицы: {name}")
        for name in missing_columns:
            print(f"нет колонки: {name}")
        print("структура отстаёт от db/init — версию не двигаю: "
              "сначала накатите файлы (repair-schema files=… или server-deploy.sh db --migrate)")
        return 1

    with psycopg.connect(url) as conn:
        version = current_version(conn)
    print(version_line(version, head))

    order = {rev: index for index, rev in enumerate(chain_list)}
    if version == head:
        print("версия схемы уже равна head: учёт актуален, ничего не меняю")
        return 0
    if version in order:
        if order[version] < order[target]:
            # Назад версия не идёт: применённый позже файл не отменяет учёт.
            print(f"версия уже впереди цели {target}: оставляю {version}")
            target = version
        elif order[version] > order[target]:
            print(f"ставлю версию {target} (было {version})")
    else:
        print(f"версия {version or 'отсутствует'} в цепочке этого checkout "
              f"не найдена — ставлю {target} после сверки структуры")

    if version != target:
        stamp(url, root, target)
    with psycopg.connect(url) as conn:
        after = current_version(conn)
    print(version_line(after, head))
    if after != head:
        later = chain_list[:order[after]] if after in order else []
        print(f"версия {after} отстаёт от head {head}: передайте оставшиеся файлы "
              f"({', '.join(f'db/init/{rev}.sql' for rev in later)}) "
              f"или выполните на сервере server-deploy.sh db --migrate")
        return 1
    print(f"учёт версии приведён к head: {head}")
    return 0


def url_from_env_file(path: Path) -> str | None:
    """DATABASE_URL из файла настроек: на сервере он в /opt/svoi/.env, не в шелле."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DATABASE_URL":
            value = value.split(" #", 1)[0].strip().strip('"').strip("'")
            return value or None
    return None


def live(conn: psycopg.Connection) -> tuple[set[str], set[str]]:
    with conn.cursor() as cur:
        cur.execute("select table_name from information_schema.tables where table_schema = 'public'")
        tables = {row[0] for row in cur.fetchall()}
        cur.execute(
            "select table_name || '.' || column_name from information_schema.columns "
            "where table_schema = 'public'")
        columns = {row[0] for row in cur.fetchall()}
    return tables, columns


def files_argument(argv: list[str]) -> list[str]:
    """Значение `--files "a.sql b.sql"` — тот же формат, что у workflow."""
    for index, arg in enumerate(argv):
        if arg == "--files" and index + 1 < len(argv):
            return argv[index + 1].split()
        if arg.startswith("--files="):
            return arg.split("=", 1)[1].split()
    return []


def root_argument(argv: list[str]) -> Path | None:
    """Значение `--root PATH`: каталог дерева, чей `alembic.ini` задаёт head."""
    for index, arg in enumerate(argv):
        if arg == "--root" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if arg.startswith("--root="):
            return Path(arg.split("=", 1)[1])
    return None


def main(argv: list[str]) -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        for index, arg in enumerate(argv):
            candidate = None
            if arg == "--env-file" and index + 1 < len(argv):
                candidate = argv[index + 1]
            elif arg.startswith("--env-file="):
                candidate = arg.split("=", 1)[1]
            if candidate:
                path = Path(candidate)
                if not path.is_file():
                    print(f"файл настроек не найден: {path}")
                    return 2
                url = url_from_env_file(path)
                if not url:
                    print(f"в {path} нет DATABASE_URL")
                    return 2
    if not url:
        print("DATABASE_URL не задан")
        return 2
    show_files = any(arg == "--files" or arg.startswith("--files=") for arg in argv)
    # Дерево для сверки версии: по умолчанию это сам скрипт (текущий checkout),
    # при откате — дерево выбранного тега, вытащенное git archive.
    root = root_argument(argv) or ROOT
    if not (root / "alembic.ini").is_file():
        print(f"нет {root / 'alembic.ini'}: сверять версию схемы не с чем")
        return 2

    if "--stamp-head" in argv:
        return stamp_head(url, root, files_argument(argv))

    if "--expect-head" in argv:
        return check_expect_head(url, root)

    want_tables, want_columns = expected()
    with psycopg.connect(url) as conn:
        have_tables, have_columns = live(conn)

    missing_tables = sorted(set(want_tables) - have_tables)
    missing_columns = sorted(set(want_columns) - have_columns)

    if not missing_tables and not missing_columns:
        print(f"схема совпадает с миграциями: таблиц {len(want_tables)}, колонок {len(want_columns)}")
        return 0

    for name in missing_tables:
        files = ", ".join(sorted(want_tables[name])) if show_files else ""
        print(f"нет таблицы: {name} {files}".rstrip())
    for name in missing_columns:
        files = ", ".join(sorted(want_columns[name])) if show_files else ""
        print(f"нет колонки: {name} {files}".rstrip())
    print(f"\nвсего не хватает: таблиц {len(missing_tables)}, колонок {len(missing_columns)}")
    print("применить точечно: docker exec -i <pg> psql -U <user> -d <db> -v ON_ERROR_STOP=1 < db/init/<файл>")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
