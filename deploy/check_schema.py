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
    python3 deploy/check_schema.py --check-code --files "db/init/063_x.sql" --code-root /opt/svoi

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

`--stamp-head` (с версии v0.10.0) сверяет ревизию с головой **выкаченного кода**
(`--code-root`, по умолчанию `/opt/svoi`) и отказывается ставить версию вперёд неё: такой
миграции работающий код не знает, и `tool-api` не поднимется (`RuntimeError: схема не
применена`). Случай выката v0.10.0 (10.11.2026): 063 накатили из `main` вперёд тега v0.9.2,
чья голова — 062, служба ушла в `activating (auto-restart)` на три минуты. Сверяется не
запрошенная цель из `--files`, а **итоговая** версия учёта: назад версия не идёт, поэтому
уже записанная ревизия впереди кода видна и при повторном накате со старым файлом — иначе
скрипт рапортовал бы зелёным в сломанном состоянии. Расхождение видно в обе стороны: цель
позади головы кода — сообщение «схема отстаёт», её лечит накат миграций из выкаченного тега.

`--check-code` — та же сверка без базы: read-only проверка «файлы из `--files` не новее
головы выкаченного кода» до наката SQL. Ею workflow `repair-schema.yml` валится раньше,
чем структура базы уедет вперёд учёта версии.

Код возврата 1, если чего-то не хватает или версия не равна head.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg

INIT_DIR = Path(__file__).resolve().parents[1] / "db" / "init"
ROOT = INIT_DIR.parents[1]
# Выкаченный на сервере код: его голова — контракт, с которым tool-api стартует.
CODE_ROOT = Path("/opt/svoi")
TAG_TIMEOUT = 5
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


def code_relation(target: str, code_chain: list[str]) -> str:
    """Где цель учёта относительно головы выкаченного кода: equal, ahead или behind.

    `ahead` — накатанная миграция новее кода на диске; такой ревизии этот код не знает,
    и `tool_api` не стартует (`alembic_version` сверяется с головой своего кода).
    Именно так упал выкат v0.10.0 10.11.2026: 063 накатили при выкаченном v0.9.2 (head 062).
    `behind` — обратный случай (v0.8.0): код ждёт больше, чем применено.
    """
    if target == code_chain[0]:
        return "equal"
    return "behind" if target in code_chain else "ahead"


def code_head(root: Path) -> str:
    """Голова цепочки выкаченного кода — то, что `tool-api` потребует при старте."""
    return head_revision(root)


def code_tag(root: Path) -> str | None:
    """Тег выкаченного дерева для текста отказа; git может отсутствовать — тогда None."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=TAG_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def code_note(relation: str, target: str, deployed_head: str, tag: str | None,
              root: Path) -> str:
    """Текст сверки цели учёта с головой выкаченного кода — для обеих сторон."""
    label = f"тег {tag}" if tag else "метка неизвестна (нет git в каталоге выката)"
    where = f"{label}, {root}"
    if relation == "ahead":
        return (
            f"НОВАЯ МИГРАЦИЯ ВПЕРЁД ВЫКАЧЕННОГО КОДА: ревизия {target} новее головы\n"
            f"  работающего кода {deployed_head} ({where}, /opt/svoi).\n"
            f"Такого файла этот код не знает: tool-api при старте откажется подниматься\n"
            f"  (RuntimeError: версия схемы {target} ≠ head {deployed_head}) и уйдёт\n"
            f"  в activating (auto-restart) — так было на выкате v0.10.0, 10.11.2026.\n"
            f"Версию не двигаю. Нужен тег, чья голова цепочки — {target} или новее:\n"
            f"  1) выкатите его (deploy/release.sh <тег> --apply),\n"
            f"  2) накатите миграции ИЗ ЭТОГО ЖЕ тега (sudo deploy/server-deploy.sh db),\n"
            f"  3) повторите накат файлов и --stamp-head уже под выкаченным кодом.\n"
            f"Порядок выката: docs/infrastructure/35 §10.2.")
    return (
        f"СХЕМА ОТСТАЁТ ОТ ВЫКАЧЕННОГО КОДА: цель учёта {target} старше головы\n"
        f"  работающего кода {deployed_head} ({where}, /opt/svoi).\n"
        f"Код ждёт {deployed_head}: после наката накатите остаток из выкаченного тега\n"
        f"  (sudo deploy/server-deploy.sh db) — иначе сервис не поднимется.\n"
        f"Порядок выката: docs/infrastructure/35 §10.2.")


def resulting_version(target: str, version: str | None, order: dict[str, int]) -> str:
    """Какая ревизия останется в `alembic_version` после наката.

    Назад учёт не идёт: если записанная версия новее цели из `--files`, остаётся она.
    Поэтому сверять с головой кода надо не запрошенную цель, а итог: при уже залипшем
    состоянии версия впереди кода не видна по списку старых файлов. Ревизию, которой
    нет в цепочке этого checkout, понизить нельзя — тоже остаётся она.
    """
    if version is None or version not in order:
        return target
    return version if order[version] < order[target] else target


def guard_code(target: str, code_root: Path | None, explicit: bool) -> bool:
    """True — версию ставить нельзя: цель впереди головы выкаченного кода.

    Проверка идёт до записи версии и до подключения к базе: отказ дешевле, чем
    структура, уехавшая вперёд учёта (тогда нужен ручной stamp).
    """
    root = code_root or CODE_ROOT
    if not (root / "alembic.ini").is_file():
        if explicit:
            print(f"нет {root / 'alembic.ini'}: сверить {target} с головой выкаченного "
                  f"кода не с чем (--code-root PATH)")
            return True
        print(f"голова выкаченного кода не проверена: нет {root / 'alembic.ini'} — "
              f"сверка с кодом пропущена (не сервер?)")
        return False
    code_chain = chain(root)
    relation = code_relation(target, code_chain)
    if relation == "equal":
        return False
    print(code_note(relation, target, code_chain[0], code_tag(root), root))
    return relation == "ahead"


def stamp(url: str, root: Path, revision: str) -> None:
    """Записать версию схемы тем же механизмом, что и деплой (`alembic stamp`)."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config

    cfg = Config(str(root / "alembic.ini"))
    cfg.attributes["url"] = url
    command.stamp(cfg, revision)


def stamp_head(url: str, root: Path, files: list[str], code_root: Path | None = None,
               code_root_explicit: bool = False) -> int:
    """Довести учёт версии до применённой структуры — после psql-наката."""
    chain_list = chain(root)
    head = chain_list[0]
    try:
        wanted, unknown = target_revision(chain_list, files) if files else (head, [])
    except ValueError as exc:
        print(str(exc))
        return 1
    target = wanted
    for item in unknown:
        print(f"файл {item} не меняет версию схемы: ревизии с таким id в цепочке нет")

    # Сверка с выкаченным кодом — до базы: версия вперёд его головы роняет старт сервиса.
    if guard_code(target, code_root, code_root_explicit):
        return 1

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
    # Сверка с выкаченным кодом идёт и здесь, по ИТОГОВОЙ версии учёта: назад версия
    # не идёт, поэтому при уже залипшем состоянии (v0.10.0: в alembic_version 063, а код
    # на диске знает 062) расхождение задаёт записанная version, а не цель из `--files`.
    # Без этой сверки повторный накат со списком старого файла рапортовал зелёным,
    # а `tool-api` после рестарта снова уходил в `activating (auto-restart)`.
    final = resulting_version(target, version, order)
    if guard_code(final, code_root, code_root_explicit):
        return 1
    target = final

    if version == head:
        print("версия схемы уже равна head: учёт актуален, ничего не меняю")
        return 0
    if version is None:
        print(f"ставлю версию {target}: учёта версии не было")
    elif version not in order:
        print(f"версия {version} в цепочке этого checkout "
              f"не найдена — ставлю {target} после сверки структуры")
    elif order[version] < order[wanted]:
        # Назад версия не идёт: применённый позже файл не отменяет учёт.
        print(f"версия уже впереди цели {wanted}: оставляю {version}")
    elif order[version] > order[wanted]:
        print(f"ставлю версию {wanted} (было {version})")

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


def check_code(root: Path, files: list[str], code_root: Path | None,
               code_root_explicit: bool) -> int:
    """Read-only: файлы из `--files` против головы выкаченного кода. База не нужна.

    Работает до наката SQL: отказ здесь оставляет базу как была, а не в состоянии
    «структура впереди учёта». Разные деревья (checkout раннера и выкаченный тег)
    задаются раздельно: `--root` — цепочка из файлов, `--code-root` — код на диске.
    """
    if not files:
        print("--check-code требует --files: сверять нечего")
        return 2
    try:
        target, unknown = target_revision(chain(root), files)
    except ValueError as exc:
        print(str(exc))
        return 1
    for item in unknown:
        print(f"файл {item} не меняет версию схемы: ревизии с таким id в цепочке нет")
    if guard_code(target, code_root, code_root_explicit):
        return 1
    if (code_root or CODE_ROOT).joinpath("alembic.ini").is_file():
        print(f"файлы не новее выкаченного кода: ревизия {target}, "
              f"голова кода {code_head(code_root or CODE_ROOT)} — накат безопасен")
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


def code_root_argument(argv: list[str]) -> Path | None:
    """Значение `--code-root PATH`: дерево выкаченного кода (по умолчанию /opt/svoi)."""
    for index, arg in enumerate(argv):
        if arg == "--code-root" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if arg.startswith("--code-root="):
            return Path(arg.split("=", 1)[1])
    return None


def main(argv: list[str]) -> int:
    # Дерево для сверки версии: по умолчанию это сам скрипт (текущий checkout),
    # при откате — дерево выбранного тега, вытащенное git archive.
    root = root_argument(argv) or ROOT
    if not (root / "alembic.ini").is_file():
        print(f"нет {root / 'alembic.ini'}: сверять версию схемы не с чем")
        return 2
    code_root = code_root_argument(argv)
    code_root_explicit = code_root is not None

    # Read-only сверка с выкаченным кодом: базы не касается, DATABASE_URL не нужен.
    if "--check-code" in argv:
        return check_code(root, files_argument(argv), code_root, code_root_explicit)

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

    if "--stamp-head" in argv:
        return stamp_head(url, root, files_argument(argv), code_root, code_root_explicit)

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
