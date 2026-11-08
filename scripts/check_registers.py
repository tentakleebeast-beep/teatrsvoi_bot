#!/usr/bin/env python3
"""Проверка трёх реестров процесса: уроки, препятствия, намерения.

  docs/development/69-lessons.md    L-NNN   ошибка → правило → где закреплено
  docs/development/70-obstacles.md  O-NNN   где агенту нужен человек
  docs/development/71-intents.md    INT-NNN зачем делается работа и чем закончилась

Реестры ведёт агент сам, поэтому контроль — здесь, а не в ревью:
  * ID уникальны и идут по возрастанию, даты настоящие, статусы из закрытого списка;
  * урок «закреплено» ссылается на существующий файл вне docs/ (тест, миграция, скрипт, код);
  * препятствие «решено»/«отклонено» содержит решение, «открыто» — нет;
  * намерение «выполнено» ссылается на существующий файл или коммит.

Плюс реестр миграций `db/init/*.sql`: номер в начале имени уникален. Порядок применения —
сортировка по имени файла, поэтому два файла с одним номером дают разный порядок на чистой
базе и на проде, и схемы расходятся молча (docs/reports/115). Проверка дублей жила только
в отчёте `arch_drift.py`, где за порог не краснеет никто, — теперь она здесь, в гейте.

Запуск: env -u PYTHONPATH .venv/bin/python scripts/check_registers.py [--strict]
Код выхода 1 при ошибках; с --strict — и при предупреждениях (устаревшие «ожидает»).
Сегодняшняя дата — в проектной зоне сервиса (`Settings.tz`), а не в зоне процесса:
строки реестров датируют по Самаре, а CI живёт в UTC (docs/development/26 §7a п.1).
Тест: tests/unit/test_registers.py.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timezone

from _cli import add_tool_api_path

add_tool_api_path()
from app.settings import Settings  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEV = ROOT / "docs" / "development"

STALE_DAYS = 14
EMPTY = {"", "—", "-"}
CODE = re.compile(r"`([^`]+)`")
SHA = re.compile(r"^[0-9a-f]{7,40}$")


def project_tz() -> timezone:
    """Зона проекта — та же, что у сервиса.

    Реестры датируют строки по проектной зоне, и сервис считает текущий день через
    `datetime.now(core.TZ)`, где `core.TZ` — `Settings.tz`. Здесь берём тот же
    источник (`TZ_OFFSET_HOURS` из настроек), а не второй `timedelta(hours=4)`
    и не зону процесса: в CI процесс живёт в UTC (docs/development/26 §7a п.1).
    """
    return Settings.from_env().tz


def project_today(now: datetime | None = None) -> date:
    """«Сегодня» в проектной зоне, а не в зоне процесса.

    Вечером UTC (20:00–24:00) дата в проектной зоне уже следующая. Строка,
    добавленная в этот момент с датой «сегодня» по проекту, при проверке в зоне
    процесса выглядела датой в будущем, и job tests на main краснел каждую ночь.
    """
    moment = datetime.now(UTC) if now is None else now
    return moment.astimezone(project_tz()).date()


@dataclass(frozen=True)
class Spec:
    path: pathlib.Path
    prefix: str
    columns: tuple[str, ...]
    statuses: frozenset[str]
    kinds: frozenset[str] = frozenset()


LESSONS = Spec(
    DEV / "69-lessons.md", "L",
    ("ID", "Дата", "Что случилось", "Первопричина", "Правило", "Закреплено", "Статус", "Источник"),
    frozenset({"закреплено", "только текст", "ожидает", "отменено"}),
)
OBSTACLES = Spec(
    DEV / "70-obstacles.md", "O",
    ("ID", "Дата", "Тип", "Суть", "Что пробовал", "Что нужно от человека", "Статус", "Решение"),
    frozenset({"открыто", "решено", "отклонено"}),
    frozenset({"правило мешает", "окружение", "данные расходятся", "нет доступа",
               "нужно решение"}),
)
INTENTS = Spec(
    DEV / "71-intents.md", "INT",
    ("ID", "Дата", "Намерение", "Основание", "Статус", "Артефакты"),
    frozenset({"активно", "выполнено", "отложено", "отменено"}),
)
SPECS = (LESSONS, OBSTACLES, INTENTS)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


INIT_DIR = ROOT / "db" / "init"
MIGRATION_NAME = re.compile(r"^(\d{3})_\S+\.sql$")


def check_migrations(init_dir: pathlib.Path | None = None,
                     report: Report | None = None) -> Report:
    """Номер миграции уникален: дубль — ошибка, а не предупреждение.

    Порядок применения `db/init` — сортировка по имени файла (`server-deploy.sh db`,
    `tests/conftest.py`, `docker-entrypoint-initdb.d`). Два файла с одним номером
    применяются в алфавитном порядке, а на проде — в порядке появления файлов; если один
    из пары ссылается на другой, схемы расходятся молча. Так было с 046 и 047
    (`migration_duplicates = 2` в docs/reports/111) — и не краснело нигде.

    Проверяются и имена: файл без трёхзначного номера не встаёт в порядок вовсе.
    Пропуски номеров здесь не проверяются — это метрика `migration_gaps` в arch_drift.py,
    у пропуска бывает задокументированная причина (018).
    """
    report = Report() if report is None else report
    init_dir = INIT_DIR if init_dir is None else init_dir
    files = sorted(init_dir.glob("*.sql"))
    if not files:
        report.errors.append(f"нет миграций в {init_dir}")
        return report
    numbers: dict[int, list[str]] = {}
    for path in files:
        match = MIGRATION_NAME.match(path.name)
        if not match:
            report.errors.append(f"{path.name}: имя миграции должно быть NNN_имя.sql")
            continue
        numbers.setdefault(int(match.group(1)), []).append(path.name)
    for number, names in sorted(numbers.items()):
        if len(names) > 1:
            report.errors.append(f"db/init: номер {number:03d} занят дважды — "
                                 f"{', '.join(names)}; порядок применения станет разным")
    return report


def parse_table(text: str, columns: tuple[str, ...]) -> list[dict[str, str]] | None:
    """Строки первой таблицы, у которой заголовок совпадает с columns. None — таблицы нет.

    Между шапкой и строками может стоять разделитель шапки, `---` и пустая строка
    (так в docs/development/69-lessons.md). Раньше разбор останавливался на первой
    такой строке, журнал уроков читался как пустой, и правила уроков не применялись
    ни к одной записи — реестр был «зелёным» по построению.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _cells(line) != list(columns):
            continue
        rows = []
        started = False
        body = lines[i + 1:]
        for index, row in enumerate(body):
            if _header_separator(row):
                if started:
                    break
                continue
            if _cells(row):
                started = True
                cells = _cells(row)
                cells += [""] * (len(columns) - len(cells))
                rows.append(dict(zip(columns, cells, strict=False)))
            elif started:
                # Пустая строка внутри таблицы разделяет группы записей, а не закрывает её:
                # в 69-lessons.md записи идут блоками по нескольку строк.
                if row.strip() or not _next_is_row(body, index):
                    break
            elif not _bridge(row):
                break
        return rows
    return None


HEADER_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
RULE = re.compile(r"^-{3,}$")


def _header_separator(line: str) -> bool:
    return bool(HEADER_SEPARATOR.match(line.strip()))


def _bridge(line: str) -> bool:
    """Строка между шапкой и данными: пустая или горизонтальный разделитель `---`."""
    return not line.strip() or bool(RULE.match(line.strip()))


def _next_is_row(body: list[str], index: int) -> bool:
    """Следующая непустая строка — строка таблицы (значит, пустая была разделителем)."""
    for row in body[index + 1:]:
        if row.strip():
            return row.strip().startswith("|")
    return False


CELL_SEPARATOR = re.compile(r"(?<!\\)\|")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return []
    # `\|` — это разделитель внутри текста ячейки (так записана команда в L-019), а не граница.
    return [cell.replace("\\|", "|").strip() for cell in CELL_SEPARATOR.split(line[1:-1])]


def _date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        return None


def _refs(cell: str) -> list[str]:
    """Пути и коммиты в обратных кавычках. Имена без «/» и точки (вьюхи, функции) не считаем."""
    return [ref for ref in CODE.findall(cell)
            if "/" in ref or "." in ref or SHA.match(ref)]


def _path_of(ref: str) -> pathlib.Path:
    return ROOT / ref.split("#")[0].split("::")[0].split(":")[0].rstrip("/")


def _commit_exists(sha: str) -> bool | None:
    """True/False — есть ли коммит; None — проверить нельзя (неглубокий клон в CI, нет git)."""
    try:
        shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout.strip()
        if shallow == "true":
            return None
        result = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=ROOT,
                                capture_output=True)
        return result.returncode == 0
    except (OSError, subprocess.CalledProcessError):
        return None


def check_refs(where: str, cell: str, report: Report) -> list[str]:
    """Возвращает существующие ссылки; несуществующие — ошибки."""
    alive = []
    for ref in _refs(cell):
        if SHA.match(ref):
            exists = _commit_exists(ref)
            if exists is False:
                report.errors.append(f"{where}: коммита {ref} нет в истории")
                continue
            alive.append(ref)
        elif _path_of(ref).exists():
            alive.append(ref)
        else:
            report.errors.append(f"{where}: путь `{ref}` не существует")
    return alive


def check_rows(spec: Spec, rows: list[dict[str, str]], report: Report,
               today: date | None = None) -> None:
    today = today or project_today()
    id_re = re.compile(rf"^{spec.prefix}-(\d{{3}})$")
    previous = 0
    seen: set[str] = set()
    for row in rows:
        rid = row["ID"]
        where = f"{spec.path.name} {rid or '(без ID)'}"
        match = id_re.match(rid)
        if not match:
            report.errors.append(f"{where}: ID должен быть вида {spec.prefix}-NNN")
            continue
        if rid in seen:
            report.errors.append(f"{where}: ID повторяется")
        seen.add(rid)
        if int(match.group(1)) <= previous:
            report.errors.append(f"{where}: ID идут не по возрастанию")
        previous = max(previous, int(match.group(1)))

        when = _date(row["Дата"])
        if when is None:
            report.errors.append(f"{where}: дата «{row['Дата']}» не в формате ДД.ММ.ГГГГ")
        elif when > today:
            report.errors.append(f"{where}: дата в будущем")

        status = row["Статус"]
        if status not in spec.statuses:
            report.errors.append(f"{where}: статус «{status}» не из списка "
                                 f"{sorted(spec.statuses)}")
            continue
        if spec.kinds and row["Тип"] not in spec.kinds:
            report.errors.append(f"{where}: тип «{row['Тип']}» не из списка "
                                 f"{sorted(spec.kinds)}")

        RULES[spec.prefix](where, row, status, when, today, report)


def _lesson(where, row, status, when, today, report):
    if row["Правило"] in EMPTY:
        report.errors.append(f"{where}: урок без правила")
    alive = check_refs(where, row["Закреплено"], report)
    check_refs(where, row["Источник"], report)
    if status == "закреплено" and not [r for r in alive if not r.startswith("docs/")]:
        report.errors.append(f"{where}: «закреплено» требует файла вне docs/ "
                             f"(тест, миграция, скрипт, код); иначе статус «только текст»")
    if status == "только текст" and not alive:
        report.errors.append(f"{where}: «только текст» требует ссылки на документ")
    if status == "ожидает" and when and (today - when).days > STALE_DAYS:
        report.warnings.append(f"{where}: урок ждёт закрепления {(today - when).days} дн.")


def _obstacle(where, row, status, when, today, report):
    for column in ("Суть", "Что нужно от человека"):
        if row[column] in EMPTY:
            report.errors.append(f"{where}: пусто поле «{column}»")
    decided = row["Решение"] not in EMPTY
    if status in {"решено", "отклонено"} and not decided:
        report.errors.append(f"{where}: статус «{status}» без решения")
    if status == "открыто" and decided:
        report.errors.append(f"{where}: решение записано, а статус «открыто»")
    check_refs(where, row["Решение"], report)


def _intent(where, row, status, when, today, report):
    if row["Основание"] in EMPTY:
        report.errors.append(f"{where}: намерение без основания (ID требования, дефекта "
                             f"или запрос с датой)")
    alive = check_refs(where, row["Артефакты"], report)
    if status == "выполнено" and not alive:
        report.errors.append(f"{where}: «выполнено» без артефакта (файл или коммит)")


RULES = {"L": _lesson, "O": _obstacle, "INT": _intent}


def check(specs: tuple[Spec, ...] = SPECS, today: date | None = None) -> Report:
    report = Report()
    # «Сегодня» считается один раз на прогон и в проектной зоне (см. project_today).
    today = project_today() if today is None else today
    for spec in specs:
        if not spec.path.exists():
            report.errors.append(f"нет файла {spec.path.relative_to(ROOT)}")
            continue
        rows = parse_table(spec.path.read_text(encoding="utf-8"), spec.columns)
        if rows is None:
            report.errors.append(f"{spec.path.name}: не найдена таблица с колонками "
                                 f"{' | '.join(spec.columns)}")
            continue
        check_rows(spec, rows, report, today)
    # Реестр миграций проверяется в том же прогоне: гейт — это одна команда, а не список
    # из docs/26, который забывают дополнить.
    check_migrations(report=report)
    return report


def summary(specs: tuple[Spec, ...] = SPECS) -> list[str]:
    lines = []
    for spec in specs:
        rows = parse_table(spec.path.read_text(encoding="utf-8"), spec.columns) or []
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["Статус"]] = counts.get(row["Статус"], 0) + 1
        parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        lines.append(f"{spec.path.name:20} записей {len(rows):3}  {parts}")
    return lines


def migration_summary(init_dir: pathlib.Path | None = None) -> str:
    """Строка отчёта: сколько файлов миграций и сколько номеров они занимают."""
    init_dir = INIT_DIR if init_dir is None else init_dir
    files = sorted(init_dir.glob("*.sql"))
    numbers = {match.group(1) for path in files if (match := MIGRATION_NAME.match(path.name))}
    return (f"db/init              миграций {len(files):3}  "
            f"номеров {len(numbers):3}  дублей {len(files) - len(numbers)}")


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    report = check()
    print("Реестры процесса (docs/development/69, 70, 71) и номера миграций (db/init)")
    print("-" * 78)
    for line in summary():
        print(line)
    print(migration_summary())
    print("-" * 78)
    for error in report.errors:
        print(f"ОШИБКА  {error}")
    for warning in report.warnings:
        print(f"ВНИМАНИЕ {warning}")
    if report.errors or (strict and report.warnings):
        print(f"\nНЕ ПРОЙДЕНО: ошибок {len(report.errors)}, предупреждений {len(report.warnings)}")
        return 1
    print(f"\nВсе проверки пройдены. Предупреждений: {len(report.warnings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
