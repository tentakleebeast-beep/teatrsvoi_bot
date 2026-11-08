#!/usr/bin/env python3
"""Сборка отчёта смоука: junit + матрица требований + сценарии + гейты → Markdown и JSON.

Зачем скрипт, а не разовый отчёт. Прошлые смоуки (`docs/reports/54`, `59`, `62`) считались
руками: числа в них нельзя пересчитать, дельту к прошлому прогону — получить, а следующая
правка начиналась с нуля. Здесь один вход — три файла, один выход — отчёт из §3.4 дизайна
`docs/development/143`: `.md` для человека и `.json` как источник истины для дельты.

    # вход: три junit слоя А (имя слоя = какой прогон это был), матрица от test_audit,
    # журнал сценариев, данные уборки, проверки прогона (К5/К7)
    env -u PYTHONPATH .venv/bin/python scripts/smoke_report.py \\
        --junit core=smoke-junit-core.xml --junit webapp=smoke-junit-webapp.xml \\
        --junit smoke=smoke-junit-smoke.xml \\
        --matrix smoke-matrix.json --scenarios smoke-scenarios.json \\
        --cleanup smoke-cleanup.json --checks smoke-checks.json \\
        --out docs/reports/145-smoke-2026-11-08

    # проверка готового отчёта (код 1 и список ID, если строка врёт)
    env -u PYTHONPATH .venv/bin/python scripts/smoke_report.py --check docs/reports/145-smoke-2026-11-08.json

Правила, которые держит скрипт (docs/development/143 §3.2, docs/plans/146 §2–§4):
  * слой А — это три прогона, которыми гейт объявляет дерево зелёным: `core`
    (`tests/` без браузера и стенда), `webapp` (браузер chromium), `smoke` (сценарии
    на стенде). Слой передаётся именем: `--junit core=файл`; в JSON — `layers.junit[]`,
    в Markdown — строка на слой (146 §2.2). Пропавший файл слоя — код 2;
  * каждая уникальная пара `файл::тест` матрицы обязана иметь исход в сведённом junit:
    иначе отчёт не собирается (код 1, число пропавших и имена), а строка требования
    не получает «нет автотеста» (146 §2.4, Д1, урок L-031);
  * `пройдено` ставится только по факту прогона — статус «готово» в документе 19
    доказательством не считается и в результат не переносится;
  * `не проверено` без причины из закрытого списка — ошибка отчёта, а не пустая клетка;
  * шаг сценария без `expected` или `actual` не засчитывается: разбор падает с кодом 1,
    а не пишет «пройдено» (К9);
  * проверки прогона (К5 — `prepare` при остатках, К7 — два прогона в один день) —
    обязательный вход `--checks`: проверка без строки в отчёте не «молчит», а валит сборку
    (`run_checks` пуст или исход вне списка — ошибка, а не пустая клетка);
  * приоритет не выдумывается: строки дополнений идут как `не задан` и в проценты
    не входят (V-044, К18);
  * слой В (прод) до ответа на V-042 не выполняется: прод-колонка — `не проверено`
    с причиной `нужен прод` (К12).

Коды выхода: 0 — отчёт собран и пороги §7 пройдены; 1 — порог нарушен или отчёт
непротиворечив; 2 — неверные аргументы.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import test_audit  # noqa: E402  — один парсер требований на проект

REPORT_VERSION = 1
CAUSES = (
    "нет автотеста",
    "нужен живой Telegram",
    "нужен Google Sheets/Calendar",
    "нужен прод",
    "нужен LLM (недетерминизм)",
    "нет данных в копии",
    "снято решением V-NNN",
)
RESULTS = ("пройдено", "провал", "не проверено", "не проверяется")
# Исход проверки прогона: «не проверяется» сюда не входит — проверка К5/К7 либо
# выполнена (пройдено/провал), либо названа непроверенной с причиной из §3.3.
RUN_CHECK_RESULTS = ("пройдено", "провал", "не проверено")
SEVERITIES = ("блокер", "важно", "мелко")
# Слои слоя А: имена, под которыми junit называются на входе (`--junit core=файл`).
# Три прогона, а не один: гейт `check_done.py` объявляет дерево зелёным именно этими
# запусками, и отчёт обязан показывать тот же объём (docs/plans/146 §2.2).
LAYERS = ("core", "webapp", "smoke")
LAYER_TITLES = {
    "core": "tests/ без браузера и стенда",
    "webapp": "tests/webapp, браузер chromium",
    "smoke": "сценарии СМ-1…СМ-12, стенд",
}
# Слой в отчёте — это про то, чем доказано требование; вход без имени слоя допустим
# (старые вызовы), но объём и файл всё равно печатаются.
SCENARIO_TARGET, SCENARIO_SKIP_MAX = 12, 3
# Порог P1 — формула от числа P1 в документе 19, а не литерал (Д2/Д3, К17):
# N строк P1 → ⌊0.8 × N⌋ доказанных; сейчас 34 → 27 (docs/plans/146 §3.2).
P1_SHARE = 0.8
# Причина непроверки по коду причины из окружения/фикстуры (журнал шага может не назвать её).
SKIP_CAUSE_MAP = (
    (re.compile(r"telegram|chat_id|доставк", re.IGNORECASE), "нужен живой Telegram"),
    (re.compile(r"google|sheets|calendar|таблиц", re.IGNORECASE), "нужен Google Sheets/Calendar"),
    (re.compile(r"прод|prod", re.IGNORECASE), "нужен прод"),
    (re.compile(r"llm|модел", re.IGNORECASE), "нужен LLM (недетерминизм)"),
)
# Цена проверки по причине непроверки — для раздела риска (К16).
COST = {
    "нет автотеста": "тест или шаг сценария, 10–20 мин",
    "нужен живой Telegram": "прогон на dev-боте с тестовым chat_id, 15 мин",
    "нужен Google Sheets/Calendar": "тестовые файлы Google и прогон вручную, 1 ч",
    "нужен прод": "слой В после ответа на V-042, 1 ч",
    "нужен LLM (недетерминизм)": "3 прогона подряд и сверка формулировок, 30 мин",
    "нет данных в копии": "добавить данные в набор §6.2 или шаг в сценарий, 30 мин",
    "снято решением V-NNN": "ничего: требование снято",
}
GATE_SCRIPTS = (
    ("scripts/check_invariants.py", ("check_invariants",)),
    ("scripts/check_registers.py", ("check_registers",)),
    ("scripts/check_deadcode.py", ("check_deadcode",)),
)


# --- чтение входов -------------------------------------------------------------

class InputError(Exception):
    """Вход отчёта негоден (код 2): слоя нет, слой назван дважды, исходы противоречат.

    Отличие от «порог не пройден» (код 1): здесь отчёт не собирается вовсе, потому что
    вход противоречив. Смешать это с состоянием прогона значит выдать ошибку машины
    за факт смоука.
    """


def read_json(path: Path | str | None) -> dict | None:
    """JSON-объект или None, если файла нет (необязательные входы)."""
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        raise InputError(f"нет файла {file}")
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InputError(f"{file} — не JSON-объект")
    return data


def junit_inputs(source) -> list[tuple[str, Path]]:
    """Входы слоя А: `слой=путь` (или просто путь — тогда слой не назван).

    Слой А — не один junit, а три прогона, которыми гейт объявляет дерево зелёным
    (docs/plans/146 §2.2): `core`, `webapp`, `smoke`. Имя слоя нужно, чтобы в отчёте было
    видно, чем именно доказано требование и какой это был прогон.
    """
    if not source:
        return []
    items = source if isinstance(source, (list, tuple, set)) else [source]
    inputs: list[tuple[str, Path]] = []
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if not part:
                continue
            layer, sep, path = part.partition("=")
            if sep:
                inputs.append((layer.strip(), Path(path.strip())))
            else:
                inputs.append(("", Path(part)))
    return inputs


def parse_junit(file: Path) -> dict[tuple[str, str], dict]:
    """Исходы одного junit-файла: `(файл теста, имя теста) → {result, message}`.

    Ключ — по `file` и `name` атрибутам; параметризованные тесты срезаются до имени
    функции, иначе `test_x[a-b]` не нашёл бы строку матрицы `test_x`. Один тест —
    одна запись: иначе счётчики в отчёте удвоятся.
    """
    tree = ET.parse(file)  # noqa: S314 — локальный файл собственного прогона
    outcomes: dict[tuple[str, str], dict] = {}
    for case in tree.getroot().iter("testcase"):
        name = case.get("name", "").split("[")[0]
        shown = case.get("file") or case.get("classname", "").replace(".", "/")
        shown = shown if shown.endswith(".py") else f"{shown}.py"
        failure = case.find("failure") if case.find("failure") is not None else case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            outcome = {"result": "провал", "message": (failure.get("message") or "").strip()}
        elif skipped is not None:
            outcome = {"result": "не проверено", "message": (skipped.get("message") or "").strip()}
        else:
            outcome = {"result": "пройдено", "message": ""}
        outcomes[(shown, name)] = outcome
    return outcomes


def count_cases(file: Path) -> dict:
    """Сколько тестов в junit как есть: параметризованные случаи считаются по одному.

    Склейка для сверки с матрицей режет параметризацию (одна функция — одна строка), но
    объём прогона в отчёте должен совпадать с `pytest`: 2944 случая — это 2944, а не 2357.
    """
    if not file.is_file():
        raise InputError(f"нет junit-файла {file}")
    tree = ET.parse(file)  # noqa: S314 — локальный файл собственного прогона
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    for case in tree.getroot().iter("testcase"):
        counts["tests"] += 1
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts


def junit_layers(source) -> list[dict]:
    """Объём каждого слоя числом: `{layer, file, tests, passed, failed, skipped}`.

    Урок L-031: критерий, сшивающий два входа, называет источник каждого и его объём.
    Пропавший файл слоя — ошибка входа, а не «ноль тестов»: иначе потерянный junit
    выглядит как прогон без тестов (146 §2.2).
    """
    layers: list[dict] = []
    seen: set[str] = set()
    for layer, file in junit_inputs(source):
        if layer and layer not in LAYERS:
            raise InputError(f"неизвестный слой {layer!r}: допустимы {', '.join(LAYERS)}")
        if layer and layer in seen:
            raise InputError(f"слой {layer} назван дважды: у слоя один файл")
        seen.add(layer)
        if not file.is_file():
            raise InputError(f"нет junit-файла слоя {layer or '—'}: {file}")
        layers.append({"layer": layer, "file": str(file), **count_cases(file)})
    return layers


def junit_outcomes(source) -> dict[tuple[str, str], dict]:
    """Склеенные исходы слоёв слоя А по правилу docs/plans/146 §2.3.

    1. тест исполнялся один раз — берётся его исход;
    2. в одном слое пропуск, в другом исполненный исход — побеждает исполненный
       (webapp-тесты в `core`-прогоне пропущены по `SVOI_WEBAPP_TESTS`, это норма);
    3. исполнялся дважды с разными исходами — ошибка входа: в junit попали разные
       деревья, и «пройдено» из одного счёта не может доказывать прогон другого.
    """
    merged: dict[tuple[str, str], dict] = {}
    where: dict[tuple[str, str], str] = {}
    for layer, file in junit_inputs(source):
        if not file.is_file():
            raise InputError(f"нет junit-файла слоя {layer or '—'}: {file}")
        for key, outcome in parse_junit(file).items():
            current = merged.get(key)
            if current is None:
                merged[key] = outcome
                where[key] = layer or str(file)
                continue
            if current["result"] == outcome["result"]:
                continue
            if outcome["result"] == "не проверено":
                continue
            if current["result"] == "не проверено":
                merged[key] = outcome
                where[key] = layer or str(file)
                continue
            raise InputError(
                f"тест {key[0]}::{key[1]} исполнялся дважды с разными исходами "
                f"({where[key]}: {current['result']}, {layer or file}: {outcome['result']}) — "
                f"в junit попали разные деревья")
    return merged


def outcome_for(outcomes: dict, entry: dict) -> dict | None:
    """Исход теста: сначала по полному пути, затем по имени файла (junit бывает обоими)."""
    found = outcomes.get((entry["file"], entry["test"]))
    if found is None:
        found = outcomes.get((Path(entry["file"]).name, entry["test"]))
    return found


def test_pairs(matrix: dict) -> list[tuple[str, str]]:
    """Уникальные пары `файл::тест` матрицы: одна пара может доказывать несколько требований."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for items in matrix.values():
        for entry in items:
            pair = (entry["file"], entry["test"])
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
    return pairs


def coverage(matrix: dict, outcomes: dict) -> dict:
    """Полнота слоя А: сколько пар матрицы имеют исход в сведённом junit (146 §2.4).

    Это главное правило Д1: «тест есть, исхода нет» — не свойство требования, а дыра
    во входе, и отчёт с такой дырой не собирается.
    """
    pairs = test_pairs(matrix)
    missing = [{"file": file, "test": test} for file, test in pairs
               if outcome_for(outcomes, {"file": file, "test": test}) is None]
    return {"pairs": len(pairs), "covered": len(pairs) - len(missing), "missing": missing}


def skip_cause(message: str) -> str:
    """Причина непроверки из сообщения junit — только из закрытого списка §3.3."""
    for pattern, cause in SKIP_CAUSE_MAP:
        if pattern.search(message or ""):
            return cause
    if "стенд" in (message or ""):
        return "нет данных в копии"
    return "нет данных в копии"


def flat_steps(scenarios: dict | None) -> list[dict]:
    """Шаги всех сценариев одной таблицей: сценарий, шаг, ожидаемое, фактическое, исход."""
    if not scenarios:
        return []
    steps: list[dict] = []
    for scenario in scenarios.get("scenarios", []):
        for step in scenario.get("steps", []):
            steps.append({
                "scenario": scenario.get("id", ""),
                "title": scenario.get("title", ""),
                "roles": scenario.get("roles", []),
                "domain": scenario.get("domain", ""),
                "step": step.get("step", ""),
                "expected": step.get("expected", ""),
                "actual": step.get("actual", ""),
                "evidence": step.get("evidence", ""),
                "result": step.get("result", ""),
                "cause": step.get("cause", ""),
                "requirements": [r for r in step.get("requirements", []) if r.startswith("FR-")],
                "severity": step.get("severity") or "важно",
            })
    return steps


# --- проверки прогона (К5, К7) -------------------------------------------------

def run_checks(payload: dict | None) -> list[dict]:
    """Проверки прогона из `--checks`: id, что проверялось, ожидалось, факт, исход, доказательство.

    Зачем отдельный вход. К5 (`prepare` при остатках прошлого прогона) и К7 (два прогона
    в один день) — не строки матрицы требований и не шаги сценария, но критерии приёмки
    прогона. Их вывод раньше жил в рабочем каталоге стенда и в отчёт не попадал: карта
    сдавалась без К5/К7, хотя доказательства были сняты (доработка Д5). Поэтому проверка,
    названная в `--checks`, попадает в отчёт машинно, а её отсутствие — ошибка сборки.

    Исход — фактический: `провал` остаётся провалом с ссылкой на дефект, а не «зелёным»,
    потому что остальные числа прогона сошлись.
    """
    if not payload:
        return []
    checks = []
    for entry in payload.get("checks", []):
        checks.append({
            "id": str(entry.get("id", "")).strip(),
            "title": str(entry.get("title", "")).strip(),
            "expected": str(entry.get("expected", "")).strip(),
            "actual": str(entry.get("actual", "")).strip(),
            "command": str(entry.get("command", "")).strip(),
            "evidence": str(entry.get("evidence", "")).strip(),
            "result": str(entry.get("result", "")).strip(),
            # Причина нужна только непроверенной проверке (§3.3), дефект — проваленной:
            # иначе «провал» повисает без адреса, куда идти за починкой.
            "cause": str(entry.get("cause", "")).strip(),
            "defect": str(entry.get("defect", "")).strip(),
        })
    return checks


# --- гейты ---------------------------------------------------------------------

def run_gates(enabled: bool = True) -> list[dict]:
    """Прогон гейт-скриптов без pytest: код возврата и последняя строка вывода."""
    if not enabled:
        return []
    python = sys.executable
    gates = []
    for script, needles in GATE_SCRIPTS:
        path = ROOT / script
        if not path.is_file():
            continue
        proc = subprocess.run([python, str(path)], cwd=ROOT, capture_output=True,
                              text=True, timeout=1800, check=False)  # noqa: S603
        tail = [line for line in (proc.stdout or proc.stderr).splitlines() if line.strip()]
        gates.append({"script": script, "code": proc.returncode,
                      "line": tail[-1][:200] if tail else "", "covers": list(needles)})
    return gates


def gate_evidence(row: dict, gates: list[dict]) -> list[str]:
    """Скрипт засчитывается требованию, если он назван в колонке «Подтверждение» и прошёл."""
    found = []
    for gate in gates:
        if gate["code"] == 0 and gate["script"] in (row.get("confirm") or ""):
            found.append(f"скрипт:{gate['script']}")
    return found


# --- матрица -------------------------------------------------------------------

def build_requirements(rows: list[dict], matrix: dict, outcomes: dict,
                       steps: list[dict], gates: list[dict], date: str) -> tuple[list[dict], list[dict]]:
    """Строки матрицы: доказательства с датой прогона, результат, причина, дефект.

    Возвращает (строки, непарные тесты). Второе — тесты, которые есть в матрице, но
    исхода в junit не имеют: их нельзя выдать за «нет автотеста» (Д1), поэтому отчёт
    с непустым списком не собирается.
    """
    by_scenario: dict[str, list[dict]] = {}
    for step in steps:
        for req_id in step["requirements"]:
            by_scenario.setdefault(req_id, []).append(step)

    requirements = []
    unmatched: list[dict] = []
    for row in rows:
        evidence: list[str] = []
        causes: list[str] = []
        failed = False
        passed = False
        entries = matrix.get(row["id"], [])
        considered = 0
        measured = 0

        for entry in entries:
            considered += 1
            outcome = outcome_for(outcomes, entry)
            label = f"тест:{entry['file']}::{entry['test']}"
            if outcome is None:
                unmatched.append({"id": row["id"], "file": entry["file"], "test": entry["test"]})
                continue
            measured += 1
            # Сценарий доказывает требование шагами: провал его junit-теста — следствие
            # провала одного шага, и переносить это на все FR файла значит красить красным
            # то, что проверено и прошло (первый прогон дал 8 «провалов» из двух дефектов).
            # Зелёный или пропущенный исход теста сценарию не мешает и в доказательство идёт.
            scenario_proven = (by_scenario.get(row["id"])
                               and entry["file"].startswith("tests/smoke/"))
            if scenario_proven and outcome["result"] == "провал":
                # Провал теста-сценария виден в доказательстве, но строку не красит:
                # отнести его ко всем FR файла значит пометить провалом то, что прошло
                # (первый прогон дал 8 мнимых провалов из двух дефектов). Сам провал
                # уходит дефектом шага сценария — с требованием и картой держателя.
                evidence.append(f"{label} ({date}, провал сценария — отнесён к дефекту шага)")
                continue
            if outcome["result"] == "пройдено":
                evidence.append(f"{label} ({date})")
                passed = True
            elif outcome["result"] == "провал":
                evidence.append(f"{label} ({date}, провал)")
                failed = True
            else:
                cause = skip_cause(outcome.get("message", ""))
                evidence.append(f"{label} ({date}, не проверено: {cause})")
                causes.append(cause)

        for step in by_scenario.get(row["id"], []):
            label = f"сценарий:{step['scenario']}/{step['step']}"
            if step["result"] == "провал":
                evidence.append(f"{label} (провал)")
                failed = True
            elif step["result"] == "пропущено":
                cause = step["cause"] if step["cause"] in CAUSES else "нет данных в копии"
                evidence.append(f"{label} (не проверено: {cause})")
                causes.append(cause)
            elif step["result"] == "пройдено":
                evidence.append(f"{label} ({date})")
                passed = True

        for label in gate_evidence(row, gates):
            evidence.append(f"{label} ({date})")
            passed = True

        status = row.get("status", "")
        if status in ("отменено", "убрано"):
            result, cause = "не проверяется", "снято решением V-NNN"
        elif failed:
            result, cause = "провал", ""
        elif passed:
            result, cause = "пройдено", ""
        elif causes:
            result, cause = "не проверено", causes[0]
        else:
            result, cause = "не проверено", "нет автотеста"

        requirements.append({
            "id": row["id"],
            "text": row["text"],
            "priority": row["priority"],
            "status19": status,
            "line": row.get("line"),
            "evidence": evidence,
            "result": result,
            "cause": cause,
            # Объём входа слоя А по этой строке: сколько тестов матрицы ждут исхода в junit
            # и у скольких он есть. Тесты, доказанные шагами сценария, сюда не входят.
            "matrix_tests": considered,
            "matrix_measured": measured,
            # слой В до ответа на V-042 не выполняется (К12): колонка всегда «нужен прод».
            "prod": "не проверено",
            "prod_cause": "нужен прод",
        })
    return requirements, unmatched


def build_defects(steps: list[dict], requirements: list[dict], outcomes: dict,
                  matrix: dict) -> list[dict]:
    """Дефекты: провал шага сценария и провал теста — с требованием, шагом и доказательством."""
    defects: list[dict] = []
    for step in steps:
        if step["result"] != "провал":
            continue
        for req_id in step["requirements"] or ["—"]:
            defects.append({
                "requirement": req_id,
                "scenario": step["scenario"],
                "step": step["step"],
                "expected": step["expected"],
                "actual": step["actual"],
                "evidence": step["evidence"],
                "severity": step["severity"] if step["severity"] in SEVERITIES else "важно",
            })
    failed_tests = [req_id for req_id, entries in matrix.items()
                    if any((outcome_for(outcomes, entry) or {}).get("result") == "провал"
                           for entry in entries)]
    for requirement in requirements:
        if requirement["id"] not in failed_tests:
            continue
        broken = [label for label in requirement["evidence"]
                  if label.startswith("тест:") and "провал" in label]
        if not broken:
            continue
        defects.append({
            "requirement": requirement["id"],
            "scenario": "",
            "step": broken[0].split(" (")[0],
            "expected": "тест проходит",
            "actual": "тест упал в junit",
            "evidence": broken[0],
            "severity": "важно",
        })
    for number, defect in enumerate(defects, start=1):
        defect["id"] = f"Д-{number:02d}"
    return defects


# --- дельта, пороги, отчёт -----------------------------------------------------

def delta(current: list[dict], previous: dict | None) -> dict:
    """Дельта к прошлому прогону по строкам матрицы (К15)."""
    if not previous:
        return {"note": "первый прогон, дельты нет", "added": [], "changed": [], "removed": []}
    before = {row["id"]: row for row in previous.get("requirements", [])}
    now = {row["id"]: row for row in current}
    added = sorted(set(now) - set(before))
    removed = sorted(set(before) - set(now))
    changed = []
    for req_id in sorted(set(now) & set(before)):
        old, new = before[req_id], now[req_id]
        if (old.get("result"), old.get("cause")) != (new["result"], new["cause"]):
            changed.append({"id": req_id, "was": old.get("result"), "was_cause": old.get("cause", ""),
                            "now": new["result"], "now_cause": new["cause"]})
    return {"previous": previous.get("date", ""), "added": added, "changed": changed,
            "removed": removed}


def share_target(rows: list[dict], share: float) -> int:
    """Порог «≥ доли от числа строк» — округление вниз, как зафиксировано в 146 §3.2.

    `⌊0.8 × 34⌋ = 27` — это число из К17 и из дизайна 143 §7; `⌊0.8 × 40⌋ = 32`.
    Округление вниз выбрано явно: 27/34 = 79,4 %, и «строгие 80 %» давали бы 28 —
    тогда напечатанный порог разошёлся бы с критерием приёмки (Д2).
    Знаменатель берётся из `test_audit.requirement_rows()`, а не из литерала в дизайне:
    иначе «Факт» и «Порог» печатают разные знаменатели (Д3), и читатель видит ошибку
    машины там, где просто устаревшее число в документе.
    """
    return int(len(rows) * share)


def thresholds(requirements: list[dict], steps: list[dict], defects: list[dict],
               cleanup: dict | None) -> dict:
    """Пороги §7 дизайна: числа печатает машина, а не человек (К17).

    Строки приоритета `не задан` (24 требования без шкалы) из процентов исключены (К18).
    """
    p0 = [r for r in requirements if r["priority"] == "P0"]
    p1 = [r for r in requirements if r["priority"] == "P1"]
    p1_target = share_target(p1, P1_SHARE)
    p0_ok = sum(1 for r in p0 if r["result"] in ("пройдено", "не проверяется")
                or (r["result"] == "не проверено" and r["cause"] in CAUSES))
    p1_ok = sum(1 for r in p1 if r["result"] == "пройдено")
    scenario_ids = {step["scenario"] for step in steps}
    skipped = {step["scenario"] for step in steps if step["result"] == "пропущено"}
    blockers = sum(1 for d in defects if d["severity"] == "блокер")
    cleanup = cleanup or {}
    leftovers = sum(int(value) for value in (cleanup.get("counts") or {}).values())
    month_ok = cleanup.get("month_total_restored", None)
    checks = [
        # Знаменатель порога = знаменатель факта = число строк P0 в документе 19.
        {"criterion": "P0 с доказательством или причиной", "value": f"{p0_ok}/{len(p0)}",
         "target": f"{len(p0)}/{len(p0)}", "ok": p0_ok == len(p0)},
        {"criterion": "P1 с доказательством", "value": f"{p1_ok}/{len(p1)}",
         "target": f">= {p1_target}/{len(p1)}", "ok": p1_ok >= p1_target},
        {"criterion": "сценарии", "value": f"{len(scenario_ids)}/{SCENARIO_TARGET}",
         "target": f"{SCENARIO_TARGET}/{SCENARIO_TARGET}",
         "ok": len(scenario_ids) == SCENARIO_TARGET},
        {"criterion": "пропуски сценариев", "value": len(skipped),
         "target": f"<= {SCENARIO_SKIP_MAX}", "ok": len(skipped) <= SCENARIO_SKIP_MAX},
        {"criterion": "блокеры", "value": blockers, "target": "0", "ok": blockers == 0},
        {"criterion": "остатки по маркеру", "value": leftovers, "target": "0",
         "ok": leftovers == 0},
        {"criterion": "итог месяца вернулся к исходному", "value": str(month_ok),
         "target": "true", "ok": month_ok is True},
        {"criterion": "сообщений живым людям", "value": cleanup.get("messages_to_people", 0),
         "target": "0", "ok": int(cleanup.get("messages_to_people", 0) or 0) == 0},
        {"criterion": "записей на проде вне разрешённых видов",
         "value": cleanup.get("prod_writes", 0), "target": "0",
         "ok": int(cleanup.get("prod_writes", 0) or 0) == 0},
    ]
    return {"checks": checks, "ok": all(check["ok"] for check in checks),
            "not_measured": [c["criterion"] for c in checks
                             if c["criterion"].startswith("итог месяца") and month_ok is None]}


def risk_section(requirements: list[dict]) -> list[dict]:
    """Каждый P0 без доказательства — с причиной и ценой проверки (К16)."""
    return [{"id": r["id"], "cause": r["cause"], "cost": COST.get(r["cause"], "не оценена"),
             "text": r["text"]} for r in requirements
            if r["priority"] == "P0" and r["result"] == "не проверено"]


def source_block(rows: list[dict], matrix: dict) -> dict:
    """Числа §7 из одного парсера документа 19 (docs/plans/146 §4.1).

    Печатаются вместе с командой пересчёта и сверяются `--check` повторным разбором:
    расхождение напечатанного и посчитанного — ошибка отчёта, а не «число не понравилось».
    """
    by_priority = Counter(row["priority"] for row in rows)
    pairs = test_pairs(matrix)
    return {
        "document": "docs/development/19-functional-requirements.md",
        "revision": requirements_revision(),
        "rows": len(rows),
        "p0": by_priority["P0"],
        "p1": by_priority["P1"],
        "p2": by_priority["P2"],
        "unset": by_priority["не задан"],
        "with_tests": sum(1 for items in matrix.values() if items),
        "test_refs": sum(len(items) for items in matrix.values()),
        "unique_pairs": len(pairs),
        "command": "env -u PYTHONPATH .venv/bin/python scripts/test_audit.py --matrix --json",
    }


def requirements_revision() -> str:
    """Дата актуальности документа 19 из его шапки («Актуально на **04.11.2026**»)."""
    try:
        head = test_audit.REQUIREMENTS_DOC.read_text(encoding="utf-8")[:2000]
    except OSError:
        return "—"
    match = re.search(r"Актуально на \*\*(\d{2}\.\d{2}\.\d{4})\*\*", head)
    return match.group(1) if match else "—"


def source_problems(report: dict) -> list[str]:
    """`--check`: напечатанные числа §7 против повторного разбора документа 19 (146 §4.2)."""
    stated = report.get("source") or {}
    if not stated:
        return ["нет ключа source: числа §7 напечатаны без источника и пересчёта"]
    counted = source_block(test_audit.requirement_rows(), test_audit.matrix())
    problems = []
    for key in ("rows", "p0", "p1", "p2", "unset", "with_tests", "test_refs", "unique_pairs"):
        if stated.get(key) != counted[key]:
            problems.append(f"источник: {key} в отчёте {stated.get(key)!r}, "
                            f"пересчёт по документу 19 даёт {counted[key]}")
    return problems


def summary(requirements: list[dict], thresholds_block: dict,
            run_checks: list[dict] | None = None) -> str:
    """Итог: три предложения по матрице и строка о проверках прогона (К5, К7)."""
    measured = sum(1 for r in requirements if r["result"] == "пройдено")
    failed = [r["id"] for r in requirements if r["result"] == "провал"]
    unknown = sum(1 for r in requirements if r["result"] == "не проверено")
    first = (f"Проверено {measured} из {len(requirements)} требований, "
             f"не проверено {unknown}, провалов {len(failed)}")
    first += f" ({', '.join(failed[:5])}{'…' if len(failed) > 5 else ''})." if failed else "."
    second = ("Пороги §7 пройдены." if thresholds_block["ok"] else
              "Пороги §7 не пройдены: " + "; ".join(
                  f"{c['criterion']} {c['value']} (нужно {c['target']})"
                  for c in thresholds_block["checks"] if not c["ok"]) + ".")
    third = "Слой В (прод) не выполнялся: нет ответа на V-042."
    if run_checks:
        # Проверки прогона называются фактическим исходом: провал К7 не прячется за тем,
        # что остальные числа прогона сошлись (Д5).
        third = "Проверки прогона: " + "; ".join(
            f"{check['id']} {check['result']}" for check in run_checks) + ". " + third
    return f"{first} {second} {third}"


def render_markdown(report: dict) -> str:
    """Человеческое представление отчёта: снимок, числа, матрица, дельта, дефекты, риск."""
    reqs = report["requirements"]
    lines = [f"# {report['number']}. Смоук {report['date']}: матрица требований и сценарии", "",
             f"Дата: {report['date']}. Коммит `{report['commit']}`. Режим: {report['env']}. "
             f"Маркер прогона `{report.get('marker') or '—'}`. "
             f"sha256 дампа `{report.get('dump_sha256') or '—'}`.", "",
             "Отчёт собран машиной: `scripts/smoke_report.py` из junit, матрицы "
             "`scripts/test_audit.py --matrix`, журнала сценариев, проверок прогона и "
             "гейт-скриптов. Ручных чисел в таблицах нет.", "", "## 1. Пороги приёмки", "",
             "| Критерий | Факт | Порог | Итог |", "|---|---|---|---|"]
    for check in report["thresholds"]["checks"]:
        lines.append(f"| {check['criterion']} | {check['value']} | {check['target']} | "
                     f"{'норма' if check['ok'] else 'не пройден'} |")
    lines += ["", f"Итог: {'пороги пройдены' if report['thresholds']['ok'] else 'пороги не пройдены'}.",
              "", "## 2. Слои доказательств", ""]
    source = report.get("source") or {}
    if source:
        lines += [f"Источник чисел §7: `{source['document']}` (ревизия {source['revision']}): "
                  f"{source['rows']} строк, P0 {source['p0']}, P1 {source['p1']}, "
                  f"P2 {source['p2']}, не задан {source['unset']}; требований с тестом "
                  f"{source['with_tests']}; ссылок «файл::тест» {source['test_refs']} "
                  f"({source['unique_pairs']} уникальных пар). Пересчёт: "
                  f"`{source['command']}`.", ""]
    coverage = report.get("coverage") or {}
    if coverage:
        missing = coverage.get("missing") or []
        lines += [f"Слой А: {coverage['covered']}/{coverage['pairs']} пар «файл::тест» "
                  f"матрицы имеют исход в junit" +
                  (f", пропало {len(missing)}" if missing else "") + ".", ""]
    lines += ["| Слой | Что прогнано | Результат |", "|---|---|---|"]
    for layer in report["layers"]["junit"]:
        title = LAYER_TITLES.get(layer["layer"], "junit")
        lines.append(f"| А. Авто ({layer['layer'] or '—'}: {title}) | `{layer['file']}`: "
                     f"{layer['tests']} тестов | {layer['passed']} пройдено, "
                     f"{layer['failed']} провал, {layer['skipped']} пропущено |")
    lines += [f"| Б. Сценарии | {len(report['scenarios'])} шагов в "
              f"{len({s['scenario'] for s in report['scenarios']})} сценариях | "
              f"{sum(1 for s in report['scenarios'] if s['result'] == 'пройдено')} пройдено, "
              f"{sum(1 for s in report['scenarios'] if s['result'] == 'провал')} провал |",
              "| В. Прод | не выполнялся | `не проверено`, причина `нужен прод` (V-042) |", ""]
    if coverage and coverage.get("missing"):
        lines += ["Пары без исхода (первая двадцатка) — прогон неполный:",
                  ""]
        for item in coverage["missing"][:20]:
            lines.append(f"- `{item['file']}::{item['test']}`")
        lines.append("")
    if report["layers"].get("gates"):
        lines += ["Гейт-скрипты:", ""]
        for gate in report["layers"]["gates"]:
            lines.append(f"- `{gate['script']}` — код {gate['code']}, {gate['line']}")
        lines.append("")
    lines += ["## 3. Дельта к прошлому прогону", ""]
    delta = report["delta"]
    if delta.get("note"):
        lines.append(delta["note"])
    else:
        lines.append(f"Прошлый прогон: {delta['previous']}. Добавлено строк: "
                     f"{len(delta['added'])}, изменилось: {len(delta['changed'])}, "
                     f"исчезло: {len(delta['removed'])}.")
        for item in delta["added"]:
            lines.append(f"- добавлено: {item}")
        for item in delta["removed"]:
            lines.append(f"- исчезло: {item}")
        for item in delta["changed"]:
            lines.append(f"- {item['id']}: {item['was']} ({item['was_cause']}) → "
                         f"{item['now']} ({item['now_cause']})")
    lines += ["", "## 4. Дефекты", ""]
    if report["defects"]:
        lines += ["| ID | Требование | Сценарий | Шаг | Ожидалось | Факт | Серьёзность |",
                  "|---|---|---|---|---|---|---|"]
        for defect in report["defects"]:
            lines.append(f"| {defect['id']} | {defect['requirement']} | "
                         f"{defect['scenario'] or '—'} | {defect['step']} | "
                         f"{defect['expected']} | {defect['actual']} | {defect['severity']} |")
        lines += ["", "Доказательства:", ""]
        for defect in report["defects"]:
            lines.append(f"- {defect['id']}: `{defect['evidence']}`")
    else:
        lines.append("Дефектов не найдено: провалов шагов и тестов нет.")
    lines += ["", "## 5. Проверки прогона (К5, К7)", ""]
    checks = report.get("run_checks") or []
    if checks:
        lines += ["| Проверка | Что проверялось | Ожидалось | Факт | Итог | Доказательство |",
                  "|---|---|---|---|---|---|"]
        for check in checks:
            lines.append(f"| {check['id']} | {check['title']} | {check['expected']} | "
                         f"{check['actual']} | {check['result']} | `{check['evidence']}` |")
        broken = [check for check in checks if check["result"] == "провал"]
        if broken:
            lines += ["", "Проваленные проверки (фактический исход, не «зелёный»):", ""]
            for check in broken:
                lines.append(f"- {check['id']}: {check['defect']}")
    else:
        lines.append("Проверки прогона не переданы: отчёт не должен собираться без них "
                     "(`--checks`), см. `docs/development/144` §5.")
    lines += ["", "## 6. Риск: P0 без доказательства", ""]
    if report["risk"]:
        lines += ["| Требование | Причина | Цена проверки |", "|---|---|---|"]
        for item in report["risk"]:
            lines.append(f"| {item['id']} | {item['cause']} | {item['cost']} |")
    else:
        lines.append("Нет: у каждого P0 есть доказательство или причина.")
    lines += ["", "## 7. Матрица требований", "",
              "| ID | Приоритет | Статус в 19 | Доказательство | Результат | Причина |",
              "|---|---|---|---|---|---|"]
    for req in reqs:
        proof = "<br>".join(req["evidence"]) if req["evidence"] else "—"
        lines.append(f"| {req['id']} | {req['priority']} | {req['status19'] or '—'} | "
                     f"{proof} | {req['result']} | {req['cause'] or '—'} |")
    lines += ["", "## 8. Итог", "", report["summary"], "",
              "Слой В: не проверялся, нужен ответ на V-042 (прод — только чтение).", ""]
    return "\n".join(lines)


def next_number(docs: Path) -> int:
    """Следующий свободный номер отчёта в docs/ (сквозная нумерация, docs/README)."""
    numbers = []
    for path in docs.rglob("*.md"):
        match = re.match(r"^(\d+)[-_]", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers) + 1 if numbers else 1


def build(args: argparse.Namespace) -> dict:
    """Собирает отчёт из входов и возвращает его как словарь."""
    rows = test_audit.requirement_rows()
    matrix = read_json(args.matrix) or {}
    if not matrix:
        raise InputError("без --matrix отчёт не собрать "
                         "(scripts/test_audit.py --matrix --json > matrix.json)")
    outcomes = junit_outcomes(args.junit)
    layers = junit_layers(args.junit)
    steps = flat_steps(read_json(args.scenarios))
    cleanup = read_json(args.cleanup) or {}
    previous = read_json(args.previous) or {}
    checks = run_checks(read_json(args.checks))
    gates = run_gates(enabled=not args.no_gates)
    date = args.date or dt.date.today().isoformat()
    requirements, unmatched = build_requirements(rows, matrix, outcomes, steps, gates, date)
    defects = build_defects(steps, requirements, outcomes, matrix)
    commit = args.commit or git_head()
    # Ш-06: отчёт обязан воспроизводиться из названного коммита. Если генератора в нём
    # нет, отчёт собран из другого дерева — это ошибка входа, а не «старый коммит».
    ensure_generator(commit)

    report = {
        "version": REPORT_VERSION,
        "number": args.number or str(next_number(ROOT / "docs")),
        "commit": commit,
        "date": date,
        "env": args.env,
        "marker": args.marker or cleanup.get("marker", ""),
        "dump_sha256": args.dump_sha or cleanup.get("dump_sha256", ""),
        "junit": ", ".join(layer["file"] for layer in layers),
        # Числа §7 — из одного парсера документа 19, вместе с командой пересчёта (146 §4).
        "source": source_block(rows, matrix),
        "layers": {"junit": layers, "gates": gates,
                   "prod": {"executed": False, "cause": "нужен прод"}},
        # Полнота слоя А: сколько пар матрицы имеют исход (146 §2.4, Д1).
        "coverage": coverage(matrix, outcomes),
        "requirements": requirements,
        # Тесты, у которых нет исхода в junit: непустой список валит отчёт (Д1).
        "unmatched_tests": unmatched,
        "scenarios": steps,
        "run_checks": checks,
        "cleanup": cleanup,
        "defects": defects,
        "delta": delta(requirements, previous or None),
        "thresholds": thresholds(requirements, steps, defects, cleanup),
        "risk": risk_section(requirements),
    }
    report["summary"] = summary(requirements, report["thresholds"], checks)
    return report


def validate(report: dict) -> list[str]:
    """Проверка отчёта на противоречия (К9, К13, К14, Д1). Возвращает список проблем."""
    problems: list[str] = []
    for key in ("version", "commit", "date", "env", "dump_sha256", "layers",
                "requirements", "scenarios", "cleanup", "defects", "unmatched_tests"):
        if key not in report:
            problems.append(f"нет ключа {key}")
    requirements = report.get("requirements", [])
    expected_rows = len(test_audit.requirement_rows())
    if len(requirements) != expected_rows:
        problems.append(f"строк матрицы {len(requirements)}, а требований в документе 19 — "
                        f"{expected_rows}")
    # Полнота слоя А (146 §2.4): у каждой пары «файл::тест» матрицы обязан быть исход.
    coverage_block = report.get("coverage")
    if coverage_block is None:
        problems.append("нет ключа coverage: полнота слоя А не проверена — отчёт собран "
                        "без сверки матрицы с junit")
    elif coverage_block.get("missing"):
        missing = coverage_block["missing"]
        problems.append(
            f"слой А: {coverage_block.get('covered', 0)}/{coverage_block.get('pairs', 0)} пар "
            f"матрицы с исходом, пропало {len(missing)}: "
            + ", ".join(f"{item.get('file')}::{item.get('test')}" for item in missing[:10]))
    # Д1: тест есть в матрице, исхода в junit нет — прогон неполный, и строка
    # «нет автотеста» у такого требования — не факт, а дыра во входе.
    for item in report.get("unmatched_tests", []):
        problems.append(f"{item.get('id')}: тест {item.get('file')}::{item.get('test')} есть "
                        f"в матрице, исхода в junit нет — прогон неполный")
    seen: set[str] = set()
    for req in requirements:
        req_id = req.get("id", "?")
        if req_id in seen:
            problems.append(f"{req_id}: строка повторяется")
        seen.add(req_id)
        if req.get("result") not in RESULTS:
            problems.append(f"{req_id}: неизвестный результат {req.get('result')!r}")
        if "matrix_tests" not in req:
            problems.append(f"{req_id}: нет объёма тестов в матрице — отчёт собран "
                            f"без сверки с junit")
        elif req["matrix_tests"] and not req.get("matrix_measured"):
            problems.append(f"{req_id}: в матрице {req['matrix_tests']} тестов, ни одного "
                            f"исхода в junit — «нет автотеста» при наличии теста недопустимо")
        if req.get("result") == "не проверено":
            if req.get("cause") not in CAUSES:
                problems.append(f"{req_id}: причина {req.get('cause')!r} вне закрытого списка §3.3")
            if req.get("cause") == "нет автотеста" and req.get("matrix_tests"):
                problems.append(f"{req_id}: «нет автотеста», хотя тест в матрице есть")
        if req.get("result") == "провал" and not any(
                d.get("requirement") == req_id for d in report.get("defects", [])):
            problems.append(f"{req_id}: провал без записи в defects[]")
    for step in report.get("scenarios", []):
        label = f"сценарий {step.get('scenario')}, шаг {step.get('step')}"
        for field in ("expected", "actual", "evidence"):
            if not str(step.get(field) or "").strip():
                problems.append(f"{label}: пустое поле {field}")
        if step.get("result") not in ("пройдено", "провал", "пропущено"):
            problems.append(f"{label}: неизвестный исход {step.get('result')!r}")
        if step.get("result") == "пропущено" and step.get("cause") not in CAUSES:
            problems.append(f"{label}: пропуск без причины из §3.3")
    for defect in report.get("defects", []):
        if defect.get("severity") not in SEVERITIES:
            problems.append(f"{defect.get('id')}: серьёзность {defect.get('severity')!r}")
        for field in ("requirement", "step", "expected", "actual", "evidence"):
            if not str(defect.get(field) or "").strip():
                problems.append(f"{defect.get('id')}: пустое поле {field}")
    # К5/К7: молчание недопустимо. Отчёт без раздела проверок прогона — такой же дефект,
    # как строка «нет автотеста» при наличии теста: критерий есть, а доказательства нет.
    if "run_checks" not in report:
        problems.append("нет ключа run_checks: проверки прогона (К5, К7) не переданы")
    elif not report["run_checks"]:
        problems.append("run_checks пуст: проверки прогона К5/К7 не названы ни исходом, "
                        "ни причиной §3.3")
    for check in report.get("run_checks") or []:
        check_id = check.get("id") or "без id"
        if check.get("result") not in RUN_CHECK_RESULTS:
            problems.append(f"проверка {check_id}: неизвестный исход {check.get('result')!r}")
        for field in ("id", "title", "expected", "actual", "evidence"):
            if not str(check.get(field) or "").strip():
                problems.append(f"проверка {check_id}: пустое поле {field}")
        if check.get("result") == "не проверено" and check.get("cause") not in CAUSES:
            problems.append(f"проверка {check_id}: причина {check.get('cause')!r} "
                            f"вне закрытого списка §3.3")
        if check.get("result") == "провал" and not str(check.get("defect") or "").strip():
            problems.append(f"проверка {check_id}: провал без ссылки на дефект "
                            f"(требование и карта)")
    return problems


def git_head() -> str:
    """Текущий коммит: отчёт без него нельзя сравнить с прошлым прогоном."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,  # noqa: S607
                              capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def ensure_generator(commit: str) -> None:
    """Ш-06: отчёт воспроизводится только из коммита, в котором есть его генератор.

    Д4: отчёт 145 стоял с `commit: 1cffb24`, а `scripts/smoke_report.py` появился позже
    (`c4c92c2`) — то есть собран из рабочего дерева, и повторить его из названного коммита
    было нельзя. Проверка делает такой отчёт непечатаемым: нет генератора в коммите —
    нет отчёта (код 2).
    """
    if not commit:
        raise InputError("не назван коммит сборки: --commit или git HEAD")
    proc = subprocess.run(["git", "cat-file", "-e", f"{commit}:scripts/smoke_report.py"],  # noqa: S607
                          cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise InputError(f"в коммите {commit} нет scripts/smoke_report.py — отчёт собран "
                         f"не из того дерева; закоммить генератор и пересобрать")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт смоука: матрица, сценарии, дельта",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--junit", action="append", default=None,
                        help="junit XML слоя А как `слой=путь` (слои: core, webapp, smoke); "
                             "можно несколько раз")
    parser.add_argument("--matrix", default=None, help="JSON от test_audit.py --matrix")
    parser.add_argument("--scenarios", default=None, help="JSON журнала сценариев")
    parser.add_argument("--cleanup", default=None, help="JSON от smoke_data.py check")
    parser.add_argument("--checks", default=None,
                        help="JSON проверок прогона К5/К7 (фактический вывод команд)")
    parser.add_argument("--previous", default=None, help="JSON прошлого прогона (дельта)")
    parser.add_argument("--out", default=None, help="префикс отчёта без расширения")
    parser.add_argument("--check", default=None, help="проверить готовый отчёт .json")
    parser.add_argument("--env", default="стенд svoi_smoke", help="режим прогона")
    parser.add_argument("--date", default=None, help="дата прогона (по умолчанию сегодня)")
    parser.add_argument("--commit", default=None, help="коммит (по умолчанию HEAD)")
    parser.add_argument("--marker", default=None, help="маркер прогона")
    parser.add_argument("--dump-sha", default=None, help="sha256 дампа стенда")
    parser.add_argument("--number", default=None, help="номер отчёта (NNN)")
    parser.add_argument("--no-gates", action="store_true", help="не запускать гейт-скрипты")
    args = parser.parse_args(argv)

    if args.check:
        try:
            report = read_json(args.check)
        except InputError as error:
            print(f"smoke_report.py: {error}")
            return 1
        if report is None:
            print(f"smoke_report.py: нет отчёта {args.check}")
            return 1
        problems = validate(report) + source_problems(report)
        if problems:
            print("отчёт неверен:", *[f"  - {item}" for item in problems], sep="\n")
            return 1
        print(f"отчёт верен: {len(report['requirements'])} требований, "
              f"{len(report['scenarios'])} шагов, {len(report['defects'])} дефектов")
        return 0

    if not args.out:
        parser.error("нужен --out ПРЕФИКС (docs/reports/NNN-smoke-YYYY-MM-DD) или --check ФАЙЛ")
    try:
        report = build(args)
    except InputError as error:
        print(f"smoke_report.py: вход негоден, отчёт не собран: {error}")
        return 2
    problems = validate(report)
    if problems:
        # К9: журнал без ожидаемого/фактического — ошибка отчёта, а не «пройдено».
        print("отчёт не собран, ошибки входа:", *[f"  - {item}" for item in problems], sep="\n")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(f"отчёт: {out.with_suffix('.md')}")
    print(f"json:  {out.with_suffix('.json')}")
    print(report["summary"])
    return 0 if report["thresholds"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
