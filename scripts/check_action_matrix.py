#!/usr/bin/env python3
"""Проверка матрицы «действие участника × интерфейс» (docs/reports/121).

Матрица — инвентарь аудита: какие действия человек совершает по документации и где
каждое из них разрешено (чат, Mini App, оба, нигде). Данные лежат в JSON рядом с
отчётом, отчёт содержит те же таблицы; расхождение между ними — ошибка.

Проверки:
  * каждое требование `FR-*` из `docs/development/19` есть в JSON ровно один раз,
    приоритет, статус и строка-источник совпадают с документом — требования не переписаны;
  * каждое требование с действием участника имеет хотя бы одну строку действия,
    а требование без строки объяснено словом «почему»;
  * каждое действие имеет интерфейс из закрытого списка, автора, статус и ссылку на
    источник, которая указывает на существующий файл и существующую строку;
  * если у действия задан `anchor` (ожидаемый текст строки), одна из ссылок источника
    обязана указывать ровно на строку с этим текстом: проверка «строка существует» не
    ловит дрейф нумерации, а проверка по тексту ловит;
  * каждый сценарий `S-*` из `docs/development/20` присутствует с актором и интерфейсом;
  * таблицы в отчёте совпадают с JSON (пересобираются этой же командой с `--write`).

Запуск: env -u PYTHONPATH .venv/bin/python scripts/check_action_matrix.py [--write] [--json]
  --write  пересобрать таблицы в отчёте из JSON;
  --json   напечатать сводку в JSON (для машинного чтения).
Код выхода 1 при любой ошибке.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC19 = ROOT / "docs/development/19-functional-requirements.md"
DOC20 = ROOT / "docs/development/20-scenarios.md"
REPORT = ROOT / "docs/reports/121-action-interface-matrix.md"
DATA = ROOT / "docs/reports/121-action-interface-matrix.json"

INTERFACES = ("чат", "Mini App", "оба", "нигде")
WHO = ("участник", "организатор", "админ", "владелец", "педагог")
MARKERS = {
    "summary": ("<!-- summary:begin -->", "<!-- summary:end -->"),
    "matrix": ("<!-- matrix:begin -->", "<!-- matrix:end -->"),
    "requirements": ("<!-- requirements:begin -->", "<!-- requirements:end -->"),
    "scenarios": ("<!-- scenarios:begin -->", "<!-- scenarios:end -->"),
}


class MatrixError(Exception):
    pass


def read_requirements() -> list[dict]:
    """Строки требований из docs/19: основные таблицы (5 колонок) и дополнения (3)."""
    out = []
    pat4 = re.compile(r"^\|\s*(FR-[A-Z]+\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|")
    pat3 = re.compile(r"^\|\s*(FR-[A-Z]+\d+)\s*\|([^|]*)\|([^|]*)\|\s*$")
    for line_no, line in enumerate(DOC19.read_text(encoding="utf-8").splitlines(), 1):
        m = pat4.match(line)
        if m:
            out.append({"id": m.group(1), "text": m.group(2).strip(),
                        "priority": m.group(3).strip(), "status": m.group(4).strip(),
                        "line": line_no})
            continue
        m = pat3.match(line)
        if m:
            out.append({"id": m.group(1), "text": m.group(2).strip(), "priority": "—",
                        "status": m.group(3).strip(), "line": line_no})
    return out


def read_scenarios() -> list[dict]:
    out = []
    for line_no, line in enumerate(DOC20.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"^#{2,3} S-(\d+)", line)
        if m:
            out.append({"id": f"S-{m.group(1)}", "line": line_no})
    return out


def check_source(source: str) -> str | None:
    m = re.match(r"^(docs/[^#]+)#L(\d+)", source)
    if not m:
        return f"источник не в формате docs/<файл>#L<строка>: {source!r}"
    path = ROOT / m.group(1)
    if not path.is_file():
        return f"файла источника нет: {m.group(1)}"
    total = len(path.read_text(encoding="utf-8").splitlines())
    line = int(m.group(2))
    if not 1 <= line <= total:
        return f"строки {line} нет в {m.group(1)} (всего {total})"
    return None


def check_anchor(sources: list[str], anchor: str) -> str | None:
    """Хотя бы одна ссылка источника ведёт на строку с ожидаемым текстом.

    `check_source` проверяет только, что строка с таким номером существует, поэтому
    дрейф нумерации (92 правили соседние карты) проходил молча. `anchor` — короткий
    кусок текста, по которому видно, что строка про это действие.
    """
    for source in sources:
        m = re.match(r"^(docs/[^#]+)#L(\d+)", source)
        if not m:
            continue
        path = ROOT / m.group(1)
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        line = int(m.group(2))
        if 1 <= line <= len(lines) and anchor in lines[line - 1]:
            return None
    return f"ни одна ссылка источника не указывает на строку с текстом {anchor!r}"


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    doc_reqs = read_requirements()
    doc_scen = read_scenarios()
    data_reqs = data.get("requirements", [])
    actions = data.get("actions", [])
    scen = data.get("scenarios", [])

    doc_ids = [r["id"] for r in doc_reqs]
    data_ids = [r["id"] for r in data_reqs]
    if len(doc_ids) != len(data_ids):
        errors.append(f"требований в docs/19: {len(doc_ids)}, в JSON: {len(data_ids)}")
    if set(doc_ids) != set(data_ids):
        errors.append(f"расхождение ID: нет в JSON {sorted(set(doc_ids) - set(data_ids))}, "
                      f"лишние {sorted(set(data_ids) - set(doc_ids))}")
    if len(data_ids) != len(set(data_ids)):
        dup = [i for i, n in Counter(data_ids).items() if n > 1]
        errors.append(f"дубли требований в JSON: {dup}")

    by_doc = {r["id"]: r for r in doc_reqs}
    for r in data_reqs:
        d = by_doc.get(r["id"])
        if not d:
            continue
        for field in ("priority", "status", "text"):
            if r.get(field) != d[field]:
                errors.append(f"{r['id']}: поле {field} расходится с docs/19 "
                              f"(JSON {r.get(field)!r}, документ {d[field]!r})")
        if r.get("source", "") != f"docs/development/19-functional-requirements.md#L{d['line']}":
            errors.append(f"{r['id']}: источник {r.get('source')!r}, ожидался L{d['line']}")

    action_ids = [a["id"] for a in actions]
    if len(action_ids) != len(set(action_ids)):
        errors.append(f"дубли действий: {[i for i, n in Counter(action_ids).items() if n > 1]}")

    req_ids = {r["id"] for r in data_reqs}
    scen_ids = {s["id"] for s in scen}
    used: dict[str, list[str]] = {}
    for a in actions:
        where = f"действие {a['id']}"
        if not a.get("action"):
            errors.append(f"{where}: пустое название")
        if not a.get("who") or any(w not in WHO for w in a["who"]):
            errors.append(f"{where}: автор вне списка {WHO}: {a.get('who')}")
        if a.get("interface") not in INTERFACES:
            errors.append(f"{where}: интерфейс вне списка {INTERFACES}: {a.get('interface')!r}")
        if not a.get("status"):
            errors.append(f"{where}: пустой статус")
        if not a.get("sources"):
            errors.append(f"{where}: нет источника")
        for s in a.get("sources", []):
            problem = check_source(s)
            if problem:
                errors.append(f"{where}: {problem}")
        anchor = str(a.get("anchor") or "").strip()
        if anchor:
            problem = check_anchor(a.get("sources", []), anchor)
            if problem:
                errors.append(f"{where}: {problem}")
        for rid in a.get("requirements", []):
            if rid not in req_ids:
                errors.append(f"{where}: ссылка на неизвестное требование {rid}")
            used.setdefault(rid, []).append(a["id"])
        for sid in a.get("scenarios", []):
            if sid not in scen_ids:
                errors.append(f"{where}: ссылка на неизвестный сценарий {sid}")

    for r in data_reqs:
        linked = used.get(r["id"], [])
        if bool(r.get("participant_action")) != bool(linked):
            errors.append(f"{r['id']}: participant_action={r.get('participant_action')}, "
                          f"а строк-действий {len(linked)}")
        if linked and r.get("actions") != linked:
            errors.append(f"{r['id']}: список actions в требовании {r.get('actions')} "
                          f"не совпадает с обратными ссылками {linked}")
        if not linked and not str(r.get("reason", "")).strip():
            errors.append(f"{r['id']}: нет строки действия и нет объяснения (reason)")

    doc_sids = [s["id"] for s in doc_scen]
    if set(doc_sids) != scen_ids:
        errors.append(f"сценарии: нет в JSON {sorted(set(doc_sids) - scen_ids)}, "
                      f"лишние {sorted(scen_ids - set(doc_sids))}")
    for s in scen:
        if not s.get("actor") or not s.get("action"):
            errors.append(f"{s['id']}: нет актора или действия")
        if s.get("interface") not in INTERFACES:
            errors.append(f"{s['id']}: интерфейс вне списка: {s.get('interface')!r}")
        problem = check_source(s.get("source", ""))
        if problem:
            errors.append(f"{s['id']}: {problem}")
        for aid in s.get("actions", []):
            if aid not in action_ids:
                errors.append(f"{s['id']}: ссылка на неизвестное действие {aid}")

    return errors


def esc(text: str) -> str:
    return text.replace("|", "\\|")


def summary_table(data: dict) -> str:
    actions = data["actions"]
    iface = Counter(a["interface"] for a in actions)
    who: Counter[str] = Counter()
    for a in actions:
        for w in a["who"]:
            who[w] += 1
    req_total = len(data["requirements"])
    req_action = sum(1 for r in data["requirements"] if r["participant_action"])
    lines = [
        "| Показатель | Значение |",
        "|---|---:|",
        f"| Строк-действий | {len(actions)} |",
        f"| Требований в docs/19 | {req_total} |",
        f"| Из них с действием участника | {req_action} |",
        f"| Сценариев в docs/20 | {len(data['scenarios'])} |",
        "",
        "| Интерфейс сейчас | Действий |",
        "|---|---:|",
    ]
    for name in INTERFACES:
        lines.append(f"| {name} | {iface.get(name, 0)} |")
    lines += ["", "| Кто | Действий |", "|---|---:|"]
    for name in WHO:
        lines.append(f"| {name} | {who.get(name, 0)} |")
    return "\n".join(lines)


def matrix_table(data: dict) -> str:
    lines = [
        "| Действие | Кто | Интерфейс сейчас | Источник | Статус требования |",
        "|---|---|---|---|---|",
    ]
    for a in data["actions"]:
        sources = "<br>".join(esc(s) for s in a["sources"])
        status = esc(a["status"])
        if a.get("note"):
            status += f"<br><small>{esc(a['note'])}</small>"
        lines.append(f"| {esc(a['action'])} | {', '.join(a['who'])} | {a['interface']} | "
                     f"{sources} | {status} |")
    return "\n".join(lines)


def requirements_table(data: dict) -> str:
    lines = [
        "| Требование | Приоритет | Статус | Почему без строки | Источник |",
        "|---|---|---|---|---|",
    ]
    for r in data["requirements"]:
        if r["participant_action"]:
            continue
        lines.append(f"| {r['id']} | {r['priority']} | {esc(r['status'])} | "
                     f"{esc(r.get('reason', ''))} | {esc(r['source'])} |")
    return "\n".join(lines)


def scenarios_table(data: dict) -> str:
    lines = [
        "| Сценарий | Актор | Действие в матрице | Интерфейс | Статус | Источник |",
        "|---|---|---|---|---|---|",
    ]
    for s in data["scenarios"]:
        lines.append(f"| {s['id']} | {s['actor']} | {esc(s['action'])} | {s['interface']} | "
                     f"{esc(s['status'])} | {esc(s['source'])} |")
    return "\n".join(lines)


def rendered(data: dict) -> dict[str, str]:
    return {
        "summary": summary_table(data),
        "matrix": matrix_table(data),
        "requirements": requirements_table(data),
        "scenarios": scenarios_table(data),
    }


def replace_region(text: str, name: str, body: str) -> str:
    begin, end = MARKERS[name]
    if begin not in text or end not in text:
        raise MatrixError(f"в отчёте нет маркеров {begin} … {end}")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{body}\n{end}{tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка матрицы действий и интерфейсов")
    parser.add_argument("--write", action="store_true", help="пересобрать таблицы в отчёте")
    parser.add_argument("--json", action="store_true", help="сводка в JSON")
    args = parser.parse_args()

    data = json.loads(DATA.read_text(encoding="utf-8"))
    errors = validate(data)
    if errors:
        for e in errors:
            print(f"ОШИБКА: {e}", file=sys.stderr)
        return 1

    blocks = rendered(data)
    report = REPORT.read_text(encoding="utf-8")
    if args.write:
        for name, body in blocks.items():
            report = replace_region(report, name, body)
        REPORT.write_text(report, encoding="utf-8")
        print(f"обновлены таблицы в {REPORT.relative_to(ROOT)}")
    for name, body in blocks.items():
        begin, end = MARKERS[name]
        current = report.split(begin, 1)[1].split(end, 1)[0].strip()
        if current != body.strip():
            print(f"ОШИБКА: таблица «{name}» в отчёте расходится с JSON; "
                  f"запустите --write", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps({
            "actions": len(data["actions"]),
            "requirements": len(data["requirements"]),
            "requirements_with_action": sum(1 for r in data["requirements"]
                                            if r["participant_action"]),
            "scenarios": len(data["scenarios"]),
            "by_interface": dict(Counter(a["interface"] for a in data["actions"])),
        }, ensure_ascii=False))
    else:
        print(f"матрица: {len(data['actions'])} действий, "
              f"{len(data['requirements'])} требований "
              f"({sum(1 for r in data['requirements'] if r['participant_action'])} с действием), "
              f"{len(data['scenarios'])} сценариев — расхождений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
