"""Конфликт карт канбана по файлам, а не по корням каталогов (docs/development/26 §7b).

Проверяем на фикстуре: разные файлы одного каталога стартуют вместе; второй писатель
существующего файла ждёт и видит имя файла; новый файл конфликтом не считается;
сгенерированные файлы (`docs/README.md`, `contracts/openapi.json`) помечены «пересобрать»
и запуск не блокируют; область не теряется, когда раздел «Область» начинается с `tests/`.
Реального спавна нет — читается только временная база, `hermes` подменяется заглушкой.

Обоснование: docs/development/26 §7b, INT-008.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Файлы, которые в фикстуре «существуют» в репозитории. Пути важны буквально:
# конфликт считается по точному пути, поэтому имена — как в настоящем репозитории.
REPO_FILES = (
    "scripts/kanban_areas.py",
    "scripts/kanban_queue.py",
    "scripts/build_docs_index.py",
    "docs/README.md",
    "contracts/openapi.json",
    "docs/development/26-process-and-git.md",
    "docs/reports/report-120.md",
    "docs/reports/report-121.md",
    ".github/workflows/tests.yml",
    "app/pages/person.py",
    "app/api/schedule/routes.py",
    "services/tool-api/app/files_store.py",
    "services/tool-api/app/api/files.py",
)


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AREAS = _load("kanban_areas")
CHECK = _load("check_kanban_cards")


def _repo(tmp_path: Path) -> Path:
    """Фиктивный корень репозитория с существующими файлами."""
    repo = tmp_path / "repo"
    for rel in REPO_FILES:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    (repo / "docs" / "reports").mkdir(parents=True, exist_ok=True)
    return repo


def _board(tmp_path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """rows: (id, status, title, body)."""
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "create table tasks (id text primary key, title text not null, body text, "
        "assignee text, status text not null, created_at integer not null, priority integer)")
    for i, (tid, status, title, body) in enumerate(rows):
        conn.execute("insert into tasks (id, title, body, assignee, status, created_at, priority) "
                     "values (?,?,?,?,?,?,?)", (tid, title, body, None, status, i, 3))
    conn.commit()
    conn.close()
    return db


# --- область как ярлык ------------------------------------------------------------

def test_область_по_телу_экран():
    """Объявленный раздел «Область» с путём страницы Mini App — это экраны."""
    body = ("Что и зачем\nПравка карточки участника.\n\nОбласть\n"
            "- `services/tool-api/app/pages/person.py` — экран\n"
            "- `tests/webapp/test_person.py` — тест\n\nКритерии приёмки\n- зелено\n")
    assert AREAS.card_area("Любая карта", body) == AREAS.SCREENS


def test_область_по_телу_домен_api():
    """`app/api/schedule` — домен API «расписание», а не инфраструктура."""
    body = "Область\n- `app/api/schedule/routes.py`\n- `tests/unit/test_schedule.py`\n\nГраницы\n"
    assert AREAS.card_area("Что-то", body) == "API: расписание"


def test_область_по_телу_инфраструктура():
    """`scripts/`, `deploy/`, `docs/` — инфраструктура."""
    body = ("Область\n- `scripts/check_kanban_cards.py`\n"
            "- `docs/development/26-process-and-git.md`\n")
    assert AREAS.card_area("Что-то", body) == AREAS.INFRA


def test_продуктовая_область_сильнее_инфраструктуры():
    """В теле и скрипт, и экран: карта правит экран, значит область — экраны."""
    body = ("Область\n- `scripts/build_docs_index.py`\n"
            "- `services/tool-api/app/pages/week.py`\n")
    assert AREAS.card_area("Что-то", body) == AREAS.SCREENS


def test_область_не_теряется_когда_раздел_начинается_с_tests():
    """Путь `tests/` первым в разделе не обрывает разбор: область берётся по продукту."""
    body = ("Область: tests/unit/test_files.py, services/tool-api/app/files_store.py —\n"
            "тест и файловый слой.\n\nКритерии приёмки: (1) заголовок собирается в одном месте\n")
    assert AREAS.card_area("Имя файла в Content-Disposition", body) == AREAS.API


def test_карта_files_store_получает_область_api():
    """Регресс t_9ca07ae1: `services/<сервис>/app/<файл>.py` — это API, а не null."""
    assert AREAS._classify_path("services/tool-api/app/files_store.py") == AREAS.API
    assert AREAS._classify_path("services/tool-api/app/api/files.py") == "API: файлы"


def test_путь_github_не_превращается_в_github(tmp_path):
    """`./` срезается, а точка в `.github` остаётся: иначе файл workflow не находится."""
    repo = _repo(tmp_path)
    assert AREAS._clean_path("./.github/workflows/tests.yml") == ".github/workflows/tests.yml"
    assert AREAS._clean_path("`.github/workflows/tests.yml`") == ".github/workflows/tests.yml"
    assert AREAS._classify_path(".github/workflows/tests.yml") == AREAS.INFRA
    declared = AREAS.card_files("Область: `.github/workflows/tests.yml`\n", repo)
    assert declared["files"] == [".github/workflows/tests.yml"]


def test_неопределённая_область_называет_причину():
    """`null` молча не выставляется: у карты печатается причина, почему области нет."""
    assert AREAS.card_area("Прибраться в репозитории", "Просто сделать лучше") is None
    note = AREAS.area_note("Прибраться в репозитории", "Область\n- `tools/whatever.py`\n")
    assert note and "ни один путь" in note
    report = AREAS.format_report([("teatrsvoi", [
        {"id": "t_x", "status": "running", "title": "Прибраться", "area": None,
         "area_note": note, "files": [], "generated": [], "new_files": []},
    ])])
    text = "\n".join(report)
    assert "не определена" in text and "ни один путь" in text


def test_область_по_заголовку():
    """Если раздела «Область» нет, область берётся из ключевых слов заголовка."""
    assert AREAS.card_area("Mini App: единый баннер обрыва связи", None) == AREAS.SCREENS
    assert AREAS.card_area("Экран расписания: выходные", "") == AREAS.SCREENS
    assert AREAS.card_area("Роутер расписания: коды ошибок", None) == AREAS.API
    assert AREAS.card_area("API: снимок OpenAPI", None) == AREAS.API


# --- конфликт по файлам -----------------------------------------------------------

def test_разные_файлы_в_одном_каталоге_стартуют_вместе(tmp_path):
    """Критерий 1: две карты, пишущие разные файлы в `docs/reports/`, идут в start обе."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_120", "ready", "Отчёт 120", "Область: `docs/reports/report-120.md`\n"),
        ("t_121", "ready", "Отчёт 121", "Область: `docs/reports/report-121.md`\n"),
    ])
    plan = AREAS.dispatch_plan(db, repo_root=repo)
    assert sorted(card["id"] for card in plan["start"]) == ["t_120", "t_121"]
    assert plan["wait"] == []
    assert plan["ok"] is True
    assert plan["pairs"][0]["verdict"] == "можно вместе"


def test_второй_писатель_существующего_файла_ждёт(tmp_path):
    """Критерий 2: один и тот же существующий файл — вторая карта в wait с именем файла."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_run", "running", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_same", "ready", "Правка правила", "Область: `scripts/kanban_areas.py`\n"),
        ("t_other", "ready", "Соседний скрипт", "Область: `scripts/kanban_queue.py`\n"),
    ])
    plan = AREAS.dispatch_plan(db, repo_root=repo)
    assert plan["ok"] is False
    assert [card["id"] for card in plan["wait"]] == ["t_same"]
    assert plan["wait"][0]["busy_files"] == ["scripts/kanban_areas.py"]
    assert [card["id"] for card in plan["hold"]] == ["t_same"]
    assert "t_other" in [card["id"] for card in plan["start"]]
    assert "t_same" not in [card["id"] for card in plan["start"]]
    assert any("конфликт по файлу: scripts/kanban_areas.py" == pair["verdict"]
               for pair in plan["pairs"])


def test_новый_файл_конфликтом_не_считается(tmp_path):
    """Отчёт со своим номером ещё не существует: две такие карты стартуют вместе."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_a", "ready", "Отчёт A", "Область: `docs/reports/report-130.md`\n"),
        ("t_b", "ready", "Отчёт B", "Область: `docs/reports/report-131.md`\n"),
    ])
    plan = AREAS.dispatch_plan(db, repo_root=repo)
    assert sorted(card["id"] for card in plan["start"]) == ["t_a", "t_b"]
    assert plan["wait"] == []
    assert AREAS.file_conflicts(db, repo) == []


def test_каталог_в_области_конфликтом_не_считается(tmp_path):
    """Карта заявила каталог (`docs/reports/`), а не файл: пересечения по файлу нет."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_a", "running", "Аудит 2/4", "Область: docs/reports/ (новый отчёт)\n"),
        ("t_b", "ready", "Аудит 4/4", "Область: docs/reports/ (новый отчёт)\n"),
    ])
    plan = AREAS.dispatch_plan(db, repo_root=repo)
    assert [card["id"] for card in plan["start"]] == ["t_b"]
    assert plan["wait"] == []
    assert AREAS.file_conflicts(db, repo) == []


def test_две_карты_в_running_на_одном_файле_это_дефект(tmp_path):
    """Две карты в работе уже пишут один файл — это находка проверки, а не норма."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_a", "running", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_b", "running", "Правка правила", "Область: `scripts/kanban_areas.py`\n"),
    ])
    conflicts = AREAS.file_conflicts(db, repo)
    assert len(conflicts) == 1
    assert conflicts[0]["files"] == ["scripts/kanban_areas.py"]
    assert conflicts[0]["cards"] == ["t_a", "t_b"]
    assert AREAS.dispatch_plan(db, repo_root=repo)["ok"] is False


def test_сгенерированные_файлы_не_блокируют_запуск(tmp_path):
    """Критерий 4: `docs/README.md` и `contracts/openapi.json` — «пересобрать», не «ждать»."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_run", "running", "Индекс документов",
         "Область: `docs/README.md`, `contracts/openapi.json`\n"),
        ("t_ready", "ready", "Новый документ",
         "Область: `docs/README.md`, `contracts/openapi.json`\n"),
    ])
    plan = AREAS.dispatch_plan(db, repo_root=repo)
    assert [card["id"] for card in plan["start"]] == ["t_ready"]
    assert plan["wait"] == []
    assert plan["ok"] is True
    assert {item["file"] for item in plan["rebuild"]} == {"docs/README.md",
                                                          "contracts/openapi.json"}
    assert plan["pairs"][0]["verdict"].startswith("пересобрать:")
    assert AREAS.file_conflicts(db, repo) == []


def test_проверка_карт_падает_на_двух_картах_одного_файла(tmp_path):
    """`check_kanban_cards.file_clashes` печатает сам файл и обе карты."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_a", "running", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_b", "running", "Правка правила", "Область: `scripts/kanban_areas.py`\n"),
    ])
    clashes = CHECK.file_clashes(db, repo)
    assert len(clashes) == 1
    assert clashes[0]["files"] == ["scripts/kanban_areas.py"]
    assert {card["id"] for card in clashes[0]["cards"]} == {"t_a", "t_b"}


def test_проверка_карт_молчит_на_разных_файлах(tmp_path):
    """Разные файлы, в том числе в одном каталоге, — нарушения нет."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_a", "running", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_b", "running", "Отчёт", "Область: `docs/reports/report-120.md`\n"),
    ])
    assert CHECK.file_clashes(db, repo) == []


# --- сторож ----------------------------------------------------------------------

def test_сторож_отпускает_карту_когда_файл_освободился(tmp_path):
    """Придержанная карта возвращается в очередь, как только её файл никто не пишет."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_done", "done", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_held", "scheduled", "Правка правила", "Область: `scripts/kanban_queue.py`\n"),
    ])
    plan = AREAS.dispatch_plan(db, held=["t_held"], repo_root=repo)
    assert [card["id"] for card in plan["release"]] == ["t_held"]
    assert plan["ok"] is True


def test_сторож_отпускает_карту_придержанную_не_им(tmp_path):
    """Файл придержанных мог потеряться: карту всё равно отпускаем по причине в журнале."""
    db = _board(tmp_path, [
        ("t_lost", "scheduled", "Аудит интерфейсов", "Область\n- `scripts/kanban_areas.py`\n"),
    ])
    conn = sqlite3.connect(db)
    conn.execute("create table task_events (id integer primary key, task_id text, run_id text,"
                 " kind text, payload text, created_at integer)")
    conn.execute("insert into task_events (task_id, kind, payload, created_at) values"
                 " ('t_lost', 'scheduled', '{\"reason\": \"ждёт: область «инфраструктура» занята\"}', 0)")
    conn.commit()
    conn.close()
    plan = AREAS.dispatch_plan(db, held=[])
    assert [card["id"] for card in plan["release"]] == ["t_lost"]


def test_сторож_не_отпускает_карту_отложенную_по_времени(tmp_path):
    """Карта, отложенная по времени («ждёт до 12:00 — окно релиза»), не наше дело.

    `kanban_schedule` пишет произвольный текст причины, поэтому сторож опирается только на
    формулировки правила («ждёт: файл …», «ждёт: область …»). Иначе пятиминутный тик снимал
    бы такую карту в ready до срока.
    """
    db = _board(tmp_path, [
        ("t_time", "scheduled", "Релиз", "Область\n- `scripts/kanban_areas.py`\n"),
    ])
    conn = sqlite3.connect(db)
    conn.execute("create table task_events (id integer primary key, task_id text, run_id text,"
                 " kind text, payload text, created_at integer)")
    conn.execute("insert into task_events (task_id, kind, payload, created_at) values"
                 " ('t_time', 'scheduled',"
                 " '{\"reason\": \"ждёт до 12:00 — окно релиза, потом проверить\"}', 0)")
    conn.commit()
    conn.close()
    assert AREAS.area_hold(db, "t_time") is False
    plan = AREAS.dispatch_plan(db, held=[])
    assert plan["release"] == []


def test_сторож_не_держит_карты_разных_областей(tmp_path):
    """Разные области — все ready-карты стартуют, держать нечего."""
    db = _board(tmp_path, [
        ("t_run", "running", "Mini App: person", "Область\n- `app/pages/person.py`\n"),
        ("t_ready", "ready", "Роутер расписания", "Область\n- `app/api/schedule/routes.py`\n"),
    ])
    plan = AREAS.dispatch_plan(db)
    assert plan["ok"] is True
    assert plan["wait"] == []
    assert [card["id"] for card in plan["start"]] == ["t_ready"]


def test_сторож_зовёт_hermes_и_помнит_придержанные(tmp_path):
    """Реальный вызов идёт в `hermes kanban schedule/unblock`, id помнятся в файле.

    Вместо hermes подставляется заглушка, которая только пишет аргументы: спавна нет.
    """
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_run", "running", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_same", "ready", "Правка правила", "Область: `scripts/kanban_areas.py`\n"),
    ])
    log = tmp_path / "calls.log"
    fake = tmp_path / "hermes"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n', encoding="utf-8")
    fake.chmod(0o755)
    held_file = tmp_path / "held"

    plan = AREAS.dispatch_plan(db, repo_root=repo)
    actions = AREAS.apply_plan(plan, held_file, "teatrsvoi", hermes_bin=str(fake))

    calls = log.read_text(encoding="utf-8")
    assert "kanban --board teatrsvoi schedule t_same" in calls
    assert "ждёт: файл «scripts/kanban_areas.py» занят картой t_run" in calls
    assert actions and "придержал t_same" in actions[0]
    assert held_file.read_text(encoding="utf-8") == "t_same\n"


def test_сторож_придерживает_карту_без_реального_спавна(tmp_path):
    """`apply_plan` в сухом режиме показывает schedule/unblock и ничего не меняет."""
    repo = _repo(tmp_path)
    db = _board(tmp_path, [
        ("t_run", "running", "WIP по областям", "Область: `scripts/kanban_areas.py`\n"),
        ("t_same", "ready", "Правка правила", "Область: `scripts/kanban_areas.py`\n"),
        ("t_held", "scheduled", "Старая придержанная", "Область: `app/pages/home.py`\n"),
    ])
    held_file = tmp_path / "kanban-areas-hold"
    held_file.write_text("t_held\n", encoding="utf-8")

    plan = AREAS.dispatch_plan(db, held=["t_held"], repo_root=repo)
    actions = AREAS.apply_plan(plan, held_file, "teatrsvoi", dry_run=True)

    assert any("schedule t_same" in line for line in actions), "карта занятого файла ждёт"
    assert any("unblock t_held" in line for line in actions), "освободившуюся отпускаем"
    assert held_file.read_text(encoding="utf-8") == "t_held\n", "сухой режим не пишет состояние"
