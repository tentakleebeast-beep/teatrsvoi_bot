#!/usr/bin/env python3
"""Сборка отчёта смоука: junit + матрица требований + сценарии + гейты → Markdown и JSON.

Зачем скрипт, а не разовый отчёт. Прошлые смоуки (`docs/reports/54`, `59`, `62`) считались
руками: числа в них нельзя пересчитать, дельту к прошлому прогону — получить, а следующая
правка начиналась с нуля. Здесь один вход — три файла, один выход — отчёт из §3.4 дизайна
`docs/development/143`: `.md` для человека и `.json` как источник истины для дельты.

    # вход: junit полного прогона `tests/` (сценарии — своим файлом, если он есть),
    # матрица от test_audit, журнал сценариев, данные уборки
    env -u PYTHONPATH .venv/bin/python scripts/smoke_report.py \\
        --junit smoke-junit-full.xml [--junit smoke-junit.xml] \\
        --matrix smoke-matrix.json --scenarios smoke-scenarios.json \\
        --cleanup smoke-cleanup.json --out docs/reports/145-smoke-2026-11-08

    # проверка готового отчёта (код 1 и список ID, если строка врёт)
    env -u PYTHONPATH .venv/bin/python scripts/smoke_report.py --check docs/reports/145-smoke-2026-11-08.json

Правила, которые держит скрипт (docs/development/143 §3.2):
  * слой А — junit **полного** прогона `tests/`, а не только `tests/smoke`: у требования
    с тестом в матрице обязан быть исход, иначе отчёт не собирается (Д1, урок L-031);
  * `пройдено` ставится только по факту прогона — статус «готово» в документе 19
    доказательством не считается и в результат не переносится;
  * `не проверено` без причины из закрытого списка — ошибка отчёта, а не пустая клетка;
  * шаг сценария без `expected` или `actual` не засчитывается: разбор падает с кодом 1,
    а не пишет «пройдено» (К9);
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
SEVERITIES = ("блокер", "важно", "мелко")
# Пороги §7 дизайна: значения по умолчанию для сценариев; P0 и P1 считаются от числа
# строк документа 19, а не от литерала (Д3: в дизайне стояло 47, фактических P0 — 48).
# P1 — «≥ 80 %» по К17: 34 строки → 27.
P1_SHARE = 0.8
SCENARIO_TARGET, SCENARIO_SKIP_MAX = 12, 3
# Сильнейшее свидетельство побеждает при склейке junit'ов: провал важнее пройденного,
# пройденное — важнее пропуска. Иначе пропуск сценария в полном наборе отменял бы факт
# прогона сценариев, а провал терялся бы за зелёным исходом другого файла.
RESULT_RANK = {"провал": 3, "пройдено": 2, "не проверено": 1}
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

def read_json(path: Path | str | None) -> dict | None:
    """JSON-объект или None, если файла нет (необязательные входы)."""
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        raise SystemExit(f"smoke_report.py: нет файла {file}")
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"smoke_report.py: {file} — не JSON-объект")
    return data


def junit_files(source) -> list[Path]:
    """Файлы слоя А из аргумента: путь, список путей (или строка через запятую).

    Слой А — это junit **полного** прогона `tests/`; прогон сценариев даёт свой файл,
    и его тоже можно передать. Возвращает все названные файлы, ничего не угадывая.
    """
    if not source:
        return []
    items = source if isinstance(source, (list, tuple, set)) else [source]
    files: list[Path] = []
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if part:
                files.append(Path(part))
    return files


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


def _checked(file: Path) -> dict[tuple[str, str], dict]:
    """Разбор junit с понятной ошибкой, если файла нет (вход отчёта, а не догадка)."""
    if not file.is_file():
        raise SystemExit(f"smoke_report.py: нет junit-файла {file}")
    return parse_junit(file)


def junit_sources(source) -> list[dict]:
    """Объём каждого источника слоя А числом: тестов, пройдено, провал, пропуск.

    Урок L-031: критерий, сшивающий два входа, называет источник каждого и его объём.
    """
    sources = []
    for file in junit_files(source):
        outcomes = _checked(file)
        sources.append({
            "path": str(file),
            "tests": len(outcomes),
            "passed": sum(1 for o in outcomes.values() if o["result"] == "пройдено"),
            "failed": sum(1 for o in outcomes.values() if o["result"] == "провал"),
            "skipped": sum(1 for o in outcomes.values() if o["result"] == "не проверено"),
        })
    return sources


def junit_outcomes(source) -> dict[tuple[str, str], dict]:
    """Склеенные исходы всех junit'ов слоя А: побеждает сильнейшее свидетельство."""
    merged: dict[tuple[str, str], dict] = {}
    for file in junit_files(source):
        for key, outcome in _checked(file).items():
            current = merged.get(key)
            if current is None or RESULT_RANK[outcome["result"]] > RESULT_RANK[current["result"]]:
                merged[key] = outcome
    return merged


def outcome_for(outcomes: dict, entry: dict) -> dict | None:
    """Исход теста: сначала по полному пути, затем по имени файла (junit бывает обоими)."""
    found = outcomes.get((entry["file"], entry["test"]))
    if found is None:
        found = outcomes.get((Path(entry["file"]).name, entry["test"]))
    return found


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
        measured = 0

        for entry in entries:
            # Сценарий сам себя доказывает шагами: его падение в junit — следствие провала
            # одного шага, и переносить это на все FR файла значит красить красным то, что
            # проверено и прошло (первый прогон дал 8 «провалов» из двух дефектов).
            if by_scenario.get(row["id"]) and entry["file"].startswith("tests/smoke/"):
                continue
            outcome = outcome_for(outcomes, entry)
            label = f"тест:{entry['file']}::{entry['test']}"
            if outcome is None:
                unmatched.append({"id": row["id"], "file": entry["file"], "test": entry["test"]})
                continue
            measured += 1
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
            # Объём входа слоя А по этой строке: сколько тестов в матрице и у скольких
            # есть исход в junit. Без этих чисел «нет автотеста» неотличимо от «не прогоняли».
            "matrix_tests": len(entries),
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
    """Порог «≥ доли от числа строк» с округлением как в дизайне 143: 34 × 0.8 = 27.2 → 27.

    Знаменатель берётся из `test_audit.requirement_rows()`, а не из литерала в дизайне:
    иначе «Факт» и «Порог» печатают разные знаменатели (Д3), и читатель видит ошибку
    машины там, где просто устаревшее число в документе.
    """
    return int(len(rows) * share + 0.5)


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


def summary(requirements: list[dict], thresholds_block: dict) -> str:
    """Итог из трёх предложений — как требует §7 дизайна."""
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
    return f"{first} {second} Слой В (прод) не выполнялся: нет ответа на V-042."


def render_markdown(report: dict) -> str:
    """Человеческое представление отчёта: снимок, числа, матрица, дельта, дефекты, риск."""
    reqs = report["requirements"]
    lines = [f"# {report['number']}. Смоук {report['date']}: матрица требований и сценарии", "",
             f"Дата: {report['date']}. Коммит `{report['commit']}`. Режим: {report['env']}. "
             f"Маркер прогона `{report.get('marker') or '—'}`. "
             f"sha256 дампа `{report.get('dump_sha256') or '—'}`.", "",
             "Отчёт собран машиной: `scripts/smoke_report.py` из junit, матрицы "
             "`scripts/test_audit.py --matrix`, журнала сценариев и гейт-скриптов. "
             "Ручных чисел в таблицах нет.", "", "## 1. Пороги приёмки", "",
             "| Критерий | Факт | Порог | Итог |", "|---|---|---|---|"]
    for check in report["thresholds"]["checks"]:
        lines.append(f"| {check['criterion']} | {check['value']} | {check['target']} | "
                     f"{'норма' if check['ok'] else 'не пройден'} |")
    lines += ["", f"Итог: {'пороги пройдены' if report['thresholds']['ok'] else 'пороги не пройдены'}.",
              "", "## 2. Слои доказательств", "",
              "| Слой | Что прогнано | Результат |", "|---|---|---|",
              f"| А. Авто | junit полного прогона `tests/`: "
              f"{sum(report['layers']['junit'].values())} тестов | "
              f"{report['layers']['junit']['passed']} пройдено, "
              f"{report['layers']['junit']['failed']} провал, "
              f"{report['layers']['junit']['skipped']} пропущено |",
              f"| Б. Сценарии | {len(report['scenarios'])} шагов в "
              f"{len({s['scenario'] for s in report['scenarios']})} сценариях | "
              f"{sum(1 for s in report['scenarios'] if s['result'] == 'пройдено')} пройдено, "
              f"{sum(1 for s in report['scenarios'] if s['result'] == 'провал')} провал |",
              "| В. Прод | не выполнялся | `не проверено`, причина `нужен прод` (V-042) |", ""]
    if report["layers"].get("junit_sources"):
        lines += ["Источники слоя А (источник и его объём — числом):", ""]
        for source in report["layers"]["junit_sources"]:
            lines.append(f"- `{source['path']}` — {source['tests']} тестов: "
                         f"{source['passed']} пройдено, {source['failed']} провал, "
                         f"{source['skipped']} пропущено")
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
    lines += ["", "## 5. Риск: P0 без доказательства", ""]
    if report["risk"]:
        lines += ["| Требование | Причина | Цена проверки |", "|---|---|---|"]
        for item in report["risk"]:
            lines.append(f"| {item['id']} | {item['cause']} | {item['cost']} |")
    else:
        lines.append("Нет: у каждого P0 есть доказательство или причина.")
    lines += ["", "## 6. Матрица требований", "",
              "| ID | Приоритет | Статус в 19 | Доказательство | Результат | Причина |",
              "|---|---|---|---|---|---|"]
    for req in reqs:
        proof = "<br>".join(req["evidence"]) if req["evidence"] else "—"
        lines.append(f"| {req['id']} | {req['priority']} | {req['status19'] or '—'} | "
                     f"{proof} | {req['result']} | {req['cause'] or '—'} |")
    lines += ["", "## 7. Итог", "", report["summary"], "",
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
        raise SystemExit("smoke_report.py: без --matrix отчёт не собрать "
                         "(scripts/test_audit.py --matrix --json > matrix.json)")
    outcomes = junit_outcomes(args.junit)
    sources = junit_sources(args.junit)
    steps = flat_steps(read_json(args.scenarios))
    cleanup = read_json(args.cleanup) or {}
    previous = read_json(args.previous) or {}
    gates = run_gates(enabled=not args.no_gates)
    date = args.date or dt.date.today().isoformat()
    requirements, unmatched = build_requirements(rows, matrix, outcomes, steps, gates, date)
    defects = build_defects(steps, requirements, outcomes, matrix)

    junit_counts = {"passed": sum(1 for o in outcomes.values() if o["result"] == "пройдено"),
                    "failed": sum(1 for o in outcomes.values() if o["result"] == "провал"),
                    "skipped": sum(1 for o in outcomes.values() if o["result"] == "не проверено")}
    report = {
        "version": REPORT_VERSION,
        "number": args.number or str(next_number(ROOT / "docs")),
        "commit": args.commit or git_head(),
        "date": date,
        "env": args.env,
        "marker": args.marker or cleanup.get("marker", ""),
        "dump_sha256": args.dump_sha or cleanup.get("dump_sha256", ""),
        "junit": ", ".join(source["path"] for source in sources),
        "layers": {"junit": junit_counts, "junit_sources": sources, "gates": gates,
                   "prod": {"executed": False, "cause": "нужен прод"}},
        "requirements": requirements,
        # Тесты, у которых нет исхода в junit: непустой список валит отчёт (Д1).
        "unmatched_tests": unmatched,
        "scenarios": steps,
        "cleanup": cleanup,
        "defects": defects,
        "delta": delta(requirements, previous or None),
        "thresholds": thresholds(requirements, steps, defects, cleanup),
        "risk": risk_section(requirements),
    }
    report["summary"] = summary(requirements, report["thresholds"])
    return report


def validate(report: dict) -> list[str]:
    """Проверка отчёта на противоречия (К9, К13, К14, Д1). Возвращает список проблем."""
    problems: list[str] = []
    for key in ("version", "commit", "date", "env", "dump_sha256", "layers",
                "requirements", "scenarios", "cleanup", "defects", "unmatched_tests"):
        if key not in report:
            problems.append(f"нет ключа {key}")
    requirements = report.get("requirements", [])
    if len(requirements) != 113:
        problems.append(f"строк матрицы {len(requirements)}, а требований в документе 19 — 113")
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
    return problems


def git_head() -> str:
    """Текущий коммит: отчёт без него нельзя сравнить с прошлым прогоном."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,  # noqa: S607
                              capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт смоука: матрица, сценарии, дельта",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--junit", action="append", default=None,
                        help="junit XML от pytest; можно несколько раз (полный прогон tests/ "
                             "обязателен, junit сценариев — по желанию)")
    parser.add_argument("--matrix", default=None, help="JSON от test_audit.py --matrix")
    parser.add_argument("--scenarios", default=None, help="JSON журнала сценариев")
    parser.add_argument("--cleanup", default=None, help="JSON от smoke_data.py check")
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
        report = read_json(args.check)
        if report is None:
            print(f"smoke_report.py: нет отчёта {args.check}")
            return 1
        problems = validate(report)
        if problems:
            print("отчёт неверен:", *[f"  - {item}" for item in problems], sep="\n")
            return 1
        print(f"отчёт верен: {len(report['requirements'])} требований, "
              f"{len(report['scenarios'])} шагов, {len(report['defects'])} дефектов")
        return 0

    if not args.out:
        parser.error("нужен --out ПРЕФИКС (docs/reports/NNN-smoke-YYYY-MM-DD) или --check ФАЙЛ")
    report = build(args)
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
