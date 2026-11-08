"""Реестры процесса: уроки, препятствия, намерения (docs/development/69, 70, 71).

Реестры ведёт агент без ревью, поэтому проверка обязана ловить каждое нарушение формы:
сначала — что настоящие реестры чистые, затем — что заведомо плохие строки ловятся.
"""

import os
import pathlib
import sys
import time
from datetime import UTC, date, datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 28)


@pytest.fixture(scope="module")
def reg():
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_registers as mod
    yield mod


def _errors(reg, spec, rows, today=TODAY):
    report = reg.Report()
    full = [{column: "" for column in spec.columns} | row for row in rows]
    reg.check_rows(spec, full, report, today)
    return report


def test_настоящие_реестры_чистые(reg):
    report = reg.check()
    assert not report.errors, "\n".join(report.errors)


def test_сегодня_берётся_в_проектной_зоне_а_не_в_зоне_процесса(reg):
    """Окно расхождения зон: 23:30 UTC — это уже 03:30 следующего дня в Самаре.

    Строки реестров датируют по проектной зоне, поэтому проверка обязана брать
    «сегодня» оттуда же, иначе запись, добавленная вечером UTC, выглядит датой
    в будущем и job tests на main краснеет каждую ночь (docs/development/26 §7a п.1).
    """
    moment = datetime(2026, 11, 1, 23, 30, tzinfo=UTC)
    assert reg.project_today(moment) == date(2026, 11, 2)


def test_зона_реестров_совпадает_с_зоной_сервиса(reg):
    """Зона — одна на проект: берётся из настроек tool-api, а не хардкодится."""
    from app.settings import Settings

    assert reg.project_tz() == Settings.from_env().tz


def test_настоящие_реестры_чистые_в_зоне_процесса_utc(reg):
    """Тот же прогон, что в CI: зона процесса UTC не должна менять вердикт."""
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"
        time.tzset()
        report = reg.check()
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
    assert not report.errors, "\n".join(report.errors)


def test_таблица_находится_по_заголовку(reg):
    text = "# x\n\n| ID | Дата | Намерение | Основание | Статус | Артефакты |\n|---|---|---|---|---|---|\n" \
           "| INT-001 | 01.10.2026 | a | b | активно | — |\n\nтекст после"
    rows = reg.parse_table(text, reg.INTENTS.columns)
    assert rows == [{"ID": "INT-001", "Дата": "01.10.2026", "Намерение": "a",
                     "Основание": "b", "Статус": "активно", "Артефакты": "—"}]
    assert reg.parse_table("нет таблицы", reg.INTENTS.columns) is None


def test_ячейка_с_экранированным_разделителем_не_рвётся(reg):
    """В журнале есть `\\|\\|` внутри обратных кавычек — это текст ячейки, а не граница.

    Без учёта экранирования строка L-019 распадается, и правило урока проверяется
    по обрывку («статус» становится текстом с командой).
    """
    text = ("| ID | Дата | Что случилось | Первопричина | Правило | Закреплено | Статус | Источник |\n"
            "|---|---|---|---|---|---|---|---|\n\n---\n\n"
            "| L-001 | 26.10.2026 | с | п | `pgrep \\|\\| true` внутри подстановки "
            "| `tests/unit/test_registers.py` | закреплено | — |\n")
    rows = reg.parse_table(text, reg.LESSONS.columns)
    assert rows is not None and len(rows) == 1, rows
    assert rows[0]["Правило"] == "`pgrep || true` внутри подстановки"
    assert rows[0]["Статус"] == "закреплено"


def test_журнал_уроков_разбирается_через_разделитель(reg):
    """Между шапкой таблицы и строками в 69-lessons.md стоит `---` и пустая строка.

    Разбор обязан проходить через них: иначе строки журнала не читаются вовсе,
    и правила уроков не применяются ни к одной записи — реестр «зелёный» по построению.
    """
    text = reg.LESSONS.path.read_text(encoding="utf-8")
    rows = reg.parse_table(text, reg.LESSONS.columns)
    assert rows is not None
    assert len(rows) >= 24, f"разобрано записей {len(rows)}"
    ids = [row["ID"] for row in rows]
    assert ids[0] == "L-001"
    assert ids == [f"L-{number:03d}" for number in range(1, len(ids) + 1)]


def test_плохая_строка_урока_в_журнале_ловится(reg, tmp_path):
    """Строка урока с будущей датой и битым ID в том же формате обязана отклоняться."""
    text = (
        "# x\n\n"
        "| ID | Дата | Что случилось | Первопричина | Правило | Закреплено | Статус | Источник |\n"
        "|---|---|---|---|---|---|---|---|\n\n---\n\n"
        "| L-001 | 26.10.2026 | случилось | причина | правило "
        "`tests/unit/test_registers.py` | `tests/unit/test_registers.py` | закреплено | — |\n"
        "| L-002 | 01.12.2026 | случилось | причина | правило "
        "`docs/development/26-process-and-git.md` | `docs/development/26-process-and-git.md` "
        "| только текст | — |\n"
        "| L-3 | 26.10.2026 | случилось | причина | правило "
        "`docs/development/26-process-and-git.md` | `docs/development/26-process-and-git.md` "
        "| только текст | — |\n\n---\n"
    )
    path = tmp_path / "69-lessons.md"
    path.write_text(text, encoding="utf-8")
    spec = reg.Spec(path, "L", reg.LESSONS.columns, reg.LESSONS.statuses)
    rows = reg.parse_table(text, spec.columns)
    assert rows is not None and len(rows) == 3, rows
    report = reg.Report()
    reg.check_rows(spec, rows, report, TODAY)
    assert any("вида L-NNN" in error for error in report.errors), report.errors
    assert any("в будущем" in error for error in report.errors), report.errors


def test_урок_закреплено_только_документом_не_принимается(reg):
    report = _errors(reg, reg.LESSONS, [{
        "ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "закреплено",
        "Закреплено": "`docs/development/26-process-and-git.md`"}])
    assert any("вне docs/" in e for e in report.errors)


def test_урок_закреплено_тестом_принимается(reg):
    report = _errors(reg, reg.LESSONS, [{
        "ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "закреплено",
        "Закреплено": "`tests/unit/test_registers.py`"}])
    assert not report.errors


def test_несуществующий_путь_ловится(reg):
    report = _errors(reg, reg.LESSONS, [{
        "ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "закреплено",
        "Закреплено": "`tests/unit/test_нет_такого.py`"}])
    assert any("не существует" in e for e in report.errors)


def test_урок_без_правила_ловится(reg):
    report = _errors(reg, reg.LESSONS, [{"ID": "L-001", "Дата": "01.10.2026",
                                         "Правило": "—", "Статус": "ожидает"}])
    assert any("без правила" in e for e in report.errors)


def test_давний_ожидает_даёт_предупреждение_а_не_ошибку(reg):
    report = _errors(reg, reg.LESSONS, [{"ID": "L-001", "Дата": "01.09.2026",
                                         "Правило": "p", "Статус": "ожидает"}])
    assert not report.errors
    assert report.warnings and "ждёт закрепления" in report.warnings[0]


@pytest.mark.parametrize("rows,needle", [
    ([{"ID": "L-1", "Дата": "01.10.2026", "Правило": "p", "Статус": "ожидает"}], "вида L-NNN"),
    ([{"ID": "L-002", "Дата": "01.10.2026", "Правило": "p", "Статус": "ожидает"},
      {"ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "ожидает"}], "возрастанию"),
    ([{"ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "ожидает"},
      {"ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "ожидает"}], "повторяется"),
    ([{"ID": "L-001", "Дата": "2026-10-01", "Правило": "p", "Статус": "ожидает"}], "ДД.ММ.ГГГГ"),
    ([{"ID": "L-001", "Дата": "01.12.2026", "Правило": "p", "Статус": "ожидает"}], "в будущем"),
    ([{"ID": "L-001", "Дата": "01.10.2026", "Правило": "p", "Статус": "готово"}], "не из списка"),
])
def test_форма_записей(reg, rows, needle):
    report = _errors(reg, reg.LESSONS, rows)
    assert any(needle in e for e in report.errors), report.errors


@pytest.mark.parametrize("status,decision,needle", [
    ("решено", "—", "без решения"),
    ("отклонено", "", "без решения"),
    ("открыто", "сделали так", "статус «открыто»"),
])
def test_препятствие_статус_согласован_с_решением(reg, status, decision, needle):
    report = _errors(reg, reg.OBSTACLES, [{
        "ID": "O-001", "Дата": "01.10.2026", "Тип": "окружение", "Суть": "s",
        "Что нужно от человека": "q", "Статус": status, "Решение": decision}])
    assert any(needle in e for e in report.errors), report.errors


def test_препятствие_тип_из_закрытого_списка(reg):
    report = _errors(reg, reg.OBSTACLES, [{
        "ID": "O-001", "Дата": "01.10.2026", "Тип": "прочее", "Суть": "s",
        "Что нужно от человека": "q", "Статус": "открыто", "Решение": "—"}])
    assert any("тип" in e for e in report.errors)


def test_намерение_выполнено_без_артефакта_ловится(reg):
    report = _errors(reg, reg.INTENTS, [{
        "ID": "INT-001", "Дата": "01.10.2026", "Намерение": "n", "Основание": "FR-D18",
        "Статус": "выполнено", "Артефакты": "—"}])
    assert any("без артефакта" in e for e in report.errors)


def test_намерение_без_основания_ловится(reg):
    report = _errors(reg, reg.INTENTS, [{
        "ID": "INT-001", "Дата": "01.10.2026", "Намерение": "n", "Основание": "—",
        "Статус": "активно"}])
    assert any("без основания" in e for e in report.errors)


def test_несуществующий_коммит_ловится(reg, monkeypatch):
    monkeypatch.setattr(reg, "_commit_exists", lambda sha: False)
    report = _errors(reg, reg.INTENTS, [{
        "ID": "INT-001", "Дата": "01.10.2026", "Намерение": "n", "Основание": "x",
        "Статус": "выполнено", "Артефакты": "`deadbee`"}])
    assert any("коммита deadbee нет" in e for e in report.errors)


def test_коммит_в_неглубоком_клоне_не_роняет_проверку(reg, monkeypatch):
    monkeypatch.setattr(reg, "_commit_exists", lambda sha: None)
    report = _errors(reg, reg.INTENTS, [{
        "ID": "INT-001", "Дата": "01.10.2026", "Намерение": "n", "Основание": "x",
        "Статус": "выполнено", "Артефакты": "`deadbee`"}])
    assert not report.errors


# ─────────────────────────────────────────── номера миграций (docs/reports/115)

def test_дубль_номера_миграции_краснит_гейт(reg, tmp_path):
    """Два файла с одним номером — ошибка гейта, а не предупреждение отчёта.

    Ровно это случилось с 046 и 047: `migration_duplicates = 2` лежало в отчёте
    arch_drift (docs/reports/111), а порядок применения на чистой базе и на проде
    расходился молча. Проверка обязана называть номер и оба файла.
    """
    for name in ("046_files_folders.sql", "046_income_misc_category.sql", "047_ok.sql"):
        (tmp_path / name).write_text("-- пусто\n", encoding="utf-8")
    report = reg.check_migrations(tmp_path)
    assert len(report.errors) == 1, report.errors
    error = report.errors[0]
    assert "046" in error
    assert "046_files_folders.sql" in error and "046_income_misc_category.sql" in error


def test_уникальные_номера_миграций_проходят(reg, tmp_path):
    for name in ("001_schema.sql", "002_more.sql", "010_tail.sql"):
        (tmp_path / name).write_text("-- пусто\n", encoding="utf-8")
    report = reg.check_migrations(tmp_path)
    assert not report.errors, report.errors


def test_миграция_без_номера_в_имени_ловится(reg, tmp_path):
    """Файл без трёхзначного номера не встаёт в порядок применения вовсе."""
    (tmp_path / "001_schema.sql").write_text("-- пусто\n", encoding="utf-8")
    (tmp_path / "hotfix.sql").write_text("-- пусто\n", encoding="utf-8")
    report = reg.check_migrations(tmp_path)
    assert any("NNN_имя.sql" in error for error in report.errors), report.errors


def test_пустой_каталог_миграций_ловится(reg, tmp_path):
    """Каталог без миграций — это ошибка пути, а не «дублей нет, всё хорошо»."""
    report = reg.check_migrations(tmp_path)
    assert any("нет миграций" in error for error in report.errors), report.errors


def test_проверка_реестров_включает_номера_миграций(reg, tmp_path, monkeypatch):
    """`check()` — то, что зовёт гейт, — обязан проверять и db/init, а не только реестры.

    Без этой связки проверка существовала бы отдельной функцией, которую никто не зовёт.
    """
    for name in ("046_a.sql", "046_b.sql"):
        (tmp_path / name).write_text("-- пусто\n", encoding="utf-8")
    monkeypatch.setattr(reg, "INIT_DIR", tmp_path)
    report = reg.check()
    assert any("номер 046 занят дважды" in error for error in report.errors), report.errors


def test_настоящие_миграции_без_дублей_номеров(reg):
    """Реальный db/init: дублей нет, файлов столько же, сколько номеров."""
    report = reg.check_migrations()
    assert not report.errors, "\n".join(report.errors)
    assert len(list(reg.INIT_DIR.glob("*.sql"))) >= 50
