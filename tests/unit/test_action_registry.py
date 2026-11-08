"""Реестр действий держит правило «дом + вход» (docs/architecture/25 §9.4, Ш-13).

Реестр — не таблица для чтения, а обещание: у каждого действия человека есть дом, где оно
выполняется целиком, и вход в этот дом; каждый путь записи из реестра существует в коде,
а каждый маршрут записи tool-api назван строкой реестра либо объяснён как «не путь записи».

Обоснование: `docs/reports/124-actions-homes-and-requirements.md` §2 (модель «дом + вход»)
и §3 (целевой реестр, 47 строк) — отчёт остаётся обоснованием, §9.4 документа 25 — сам
реестр. Тест ловит три класса поломок: реестр разошёлся с обоснованием, дом или вход
потерялся, маршрут записи появился мимо реестра (правило 6 §9.2 ADR-13).

Маршруты-оболочки Mini App (`/webapp/*`) в проверку «маршрут без строки реестра» не входят:
по ADR-13 правило 2 у действия один путь записи, а экран зовёт его же. Их контракт держат
`tests/integration/test_static_contract.py` и `scripts/audit_actions.py`.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "architecture" / "25-architecture-evolution.md"
JUSTIFICATION = ROOT / "docs" / "reports" / "124-actions-homes-and-requirements.md"

SYSTEM_START = "### 9.4 Реестр действий"
SYSTEM_END = "### 9.5 "
JUST_START = "## 3. Целевой реестр"
JUST_END = "### Итог по домам"

COLUMNS = ("id", "action", "who", "right", "route", "home", "entrances",
           "requirements", "was")

#: Пространства канонных маршрутов tool-api: здесь маршрут записи обязан иметь строку
#: реестра. `/webapp/*` — оболочки экранов, `/health` — чтение.
WRITE_NAMESPACES = ("/tools/", "/admin/", "/files", "/folders", "/tags", "/agent/")

#: Маршруты записи, которые не являются путём записи действия человека. Каждая запись —
#: причина, а не отписка; список закрытый и проверяется в обе стороны: новый маршрут без
#: строки реестра и без причины роняет тест, а причина для удалённого маршрута — тоже.
#: Разбор списка — карта Ш-14 (`t_018f1bf6`, ревизия сирот и служебных маршрутов).
NOT_AN_ACTION: dict[str, str] = {
    # разбор текста: запись появляется после карточки с подтверждением
    "POST /tools/availability/parse": "разбор текста занятости, не пишет",
    "POST /tools/availability/parse-interval": "разбор интервала, не пишет",
    "POST /tools/budget/parse": "разбор текста операции, не пишет",
    "POST /tools/profile/intro-parse": "разбор рассказа о себе (вход П1), не пишет",
    "POST /tools/intent/resolve": "механизм каскада намерения (Р2 удалён из действий, 124 §4.4)",
    "POST /tools/feedback/triage": "разбор пачки обращений: ответ или доставка (механизм О2)",
    "POST /tools/availability/template": "чтение шаблона недели (POST ради параметров)",
    # шаги карточки и черновика: запись идёт своим путём (Д2, Д3, С1)
    "POST /tools/agent/propose": "черновик предложения агента, пишется подтверждением Д2",
    "POST /tools/agent/plan": "черновик плана из N предложений, пишется подтверждением плана",
    "POST /tools/agent/plan/confirm": "исполнение подтверждённого плана: механизм, а не отдельное действие человека",
    "POST /tools/agent/cancel-task": "флаг отмены идущей задачи для петли агента, ничего не пишет",
    "POST /tools/agent/files": "файловый инструмент агента; входа нет, корзина (а) в 124 — удаление в AUD-8.1",
    "POST /tools/assistant/ask": "старый маршрут помощника; свободный текст идёт в /tools/agent/ask (Ш-12)",
    # агентские действия по предложению — механизм, не действие человека из §3
    "POST /agent/actions/{}/apply": "применение предложенного агентом действия (механизм)",
    "POST /agent/actions/{}/reject": "отклонение предложенного агентом действия (механизм)",
    # служебные маршруты: не действие человека, решение — Ш-14 (AUD-8)
    "POST /admin/bootstrap": "первичный служебный вход по сервисному токену",
    "POST /admin/files/cold-rotate": "холодная ротация хранилища по таймеру (Х3 в 124 удалена)",
    # дубли записи, которые разбирает Ш-14
    "POST /tools/availability/week": "вторая запись недели рядом с POST /tools/availability/commit (дубль, Ш-14)",
    # файловые операции, которых нет строкой в §3 отчёта 124 (кандидаты Ш-14)
    "POST /files/{}/folder": "перемещение файла в папку — вне §3 отчёта 124",
    "POST /files/{}/link": "привязка файла к объекту — вне §3 отчёта 124",
    "POST /files/{}/tags": "простановка тегов файлу — вне §3 отчёта 124 (Ф3 только утверждает тег)",
    "POST /tags": "создание тега — вне §3 отчёта 124 (Ф3 только утверждает)",
    # настройка источников финансов: маршруты без потребителя, Ш-14
    "POST /tools/finance/sources": "настройка источников бюджета — вне §3 отчёта 124, потребителя нет",
    "DELETE /tools/finance/sources/{}": "удаление источника бюджета — вне §3 отчёта 124, потребителя нет",
    "POST /tools/finance/granularity": "гранулярность источников бюджета — вне §3 отчёта 124, потребителя нет",
}

ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`,;)]*)")
URL_RE = re.compile(r"(/webapp/[A-Za-z0-9_/{}\-]*)")


def _rows(path: pathlib.Path, start: str, end: str) -> list[dict[str, str]]:
    body = path.read_text(encoding="utf-8").split(start, 1)[1].split(end, 1)[0]
    out = []
    for line in body.splitlines():
        if not re.match(r"^\|\s*[ДЗСЛПФОРХ]\d+\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == len(COLUMNS), f"строка реестра не на 9 колонок: {line[:60]}"
        out.append(dict(zip(COLUMNS, cells, strict=True)))
    return out


def _norm(path: str) -> str:
    """Шаблон маршрута без имён параметров: `{id}` и `{participant_id}` — один путь."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _routes_cell(row: dict[str, str]) -> set[tuple[str, str]]:
    return {(method, _norm(path))
            for method, path in ROUTE_RE.findall(row["route"].replace("`", ""))}


@pytest.fixture(scope="module")
def registry() -> list[dict[str, str]]:
    return _rows(REGISTRY, SYSTEM_START, SYSTEM_END)


@pytest.fixture(scope="module")
def routes() -> dict[str, set[tuple[str, str]]]:
    """Маршруты tool-api из фактической схемы приложения: метод и шаблон пути."""
    sys.path.insert(0, str(ROOT / "services" / "tool-api"))
    sys.path.insert(0, str(ROOT))
    from app.main import app  # noqa: E402

    schema = app.openapi()["paths"]
    all_routes = {(method.upper(), _norm(path))
                  for path, methods in schema.items()
                  for method in methods if method != "parameters"}
    get_routes = {("GET", _norm(path)) for path, methods in schema.items() if "get" in methods}
    write_routes = {route for route in all_routes if route[0] != "GET"}
    return {"all": all_routes, "get": get_routes, "write": write_routes}


def test_реестр_совпадает_с_обоснованием(registry):
    """Критерий «47 строк»: реестр 25 §9.4 — это §3 отчёта 124, а не вторая редакция.

    Состав и порядок строк сверяются по ID и названию действия. Пути записи живут своей
    жизнью: они сверяются с кодом следующим тестом — реестр обновляется фактом.
    """
    assert len(registry) == 47, f"строк в реестре {len(registry)}, в §3 отчёта 124 — 47"
    ids = [row["id"] for row in registry]
    assert len(ids) == len(set(ids)), f"ID повторяются: {sorted({i for i in ids if ids.count(i) > 1})}"

    justified = _rows(JUSTIFICATION, JUST_START, JUST_END)
    assert [(row["id"], row["action"]) for row in registry] == \
        [(row["id"], row["action"]) for row in justified]


def test_у_каждого_действия_есть_дом(registry):
    """Критерий карты: в 92 и 25 нет действия без дома.

    Дом — непустое место, названное словами; дом из закрытого списка мест (§2 модели:
    чат, Mini App, Google Calendar, Google Sheets, скрипт).
    """
    allowed = ("чат", "Mini App", "Google Calendar", "Google Sheets", "скрипт")
    problems = []
    for row in registry:
        home = row["home"].strip()
        if not home or home == "—":
            problems.append(f"{row['id']}: дом не назван")
        elif not any(word in home for word in allowed):
            problems.append(f"{row['id']}: дом вне известных мест: {home!r}")
    assert not problems, "; ".join(problems)


def test_у_каждого_действия_есть_вход(registry):
    """У действия есть вход: кнопка, карточка или распознанное намерение.

    «—» разрешено там, где дом — сам чат (входить некуда, человек уже внутри). У экранного
    действия вход либо назван, либо лежит внутри дома и это сказано словами.
    """
    problems = []
    for row in registry:
        entrances = row["entrances"].strip()
        if not entrances:
            problems.append(f"{row['id']}: вход не назван")
        elif entrances == "—" and "чат" not in row["home"]:
            problems.append(f"{row['id']}: дом {row['home']!r}, а входа нет")
    assert not problems, "; ".join(problems)


def test_адреса_домов_и_входов_живые(registry, routes):
    """Каждый `/webapp/...`, названный в доме или входе, отдаётся приложением.

    Ловит переименование экрана: реестр и навигация 92 обещают адрес, которого нет.
    """
    problems = []
    for row in registry:
        text = (row["home"] + " " + row["entrances"]).replace("`", "")
        for url in URL_RE.findall(text):
            url = url.rstrip("/.,")
            if ("GET", _norm(url)) not in routes["get"]:
                problems.append(f"{row['id']}: адрес {url} не отдаётся приложением")
    assert not problems, "; ".join(problems)


def test_каждый_путь_записи_реестра_существует(registry, routes):
    """Путь записи в реестре — факт кода: метод и шаблон маршрута совпадают.

    Строка без пути («нет пути», «чтение …») маршрутов не содержит и проверяется только
    на осмысленность: пустой путь записи обязан быть объяснён словами.
    """
    problems = []
    for row in registry:
        cell = row["route"].replace("`", "")
        found = _routes_cell(row)
        if not found:
            if not re.search(r"нет пути|чтение", cell):
                problems.append(f"{row['id']}: путь записи не назван и не объяснён: {cell!r}")
            continue
        for route in sorted(found - routes["all"]):
            problems.append(f"{row['id']}: маршрута {route[0]} {route[1]} нет в приложении")
    assert not problems, "; ".join(problems)


def test_у_каждого_маршрута_записи_есть_строка_реестра(registry, routes):
    """Правило 6 §9.2: маршрут записи без строки реестра — сирота или забытое действие.

    Проверяются канонные пространства tool-api. Оболочки Mini App исключены: у действия
    один путь записи, экран зовёт его же. Не-действия перечислены в `NOT_AN_ACTION`
    с причиной, и список сверяется в обе стороны — устаревшая причина тоже ошибка.
    """
    covered = {route for row in registry for route in _routes_cell(row)}
    explained = {(key.split(" ", 1)[0], _norm(key.split(" ", 1)[1])) for key in NOT_AN_ACTION}

    unexplained = [f"{method} {path}" for method, path in sorted(routes["write"])
                   if path.startswith(WRITE_NAMESPACES)
                   and (method, path) not in covered
                   and (method, path) not in explained]
    stale = [f"{method} {path}" for method, path in sorted(explained)
             if (method, path) not in routes["all"]]

    assert not unexplained, (
        "маршрут записи без строки реестра и без причины: " + "; ".join(unexplained))
    assert not stale, "причина для несуществующего маршрута: " + "; ".join(stale)
