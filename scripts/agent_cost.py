#!/usr/bin/env python3
"""Стоимость 1000 сообщений в каждом режиме агента — счётом, а не на глаз (карта A-4).

Зачем. «Агентный режим дороже» — верное, но бесполезное утверждение: непонятно,
на сколько и от чего зависит. Здесь считается цена по факту замера A-0
(`docs/reports/112-agent-eval-run-01.11.2026.json`): у каждого ответа в прогоне
записаны `tokens_in` и `tokens_out`, а цены и курс лежат в `config/agent-models.yaml`.

Что считается:

    $/сообщение = (tokens_in × цена_вход + tokens_out × цена_выход) / 1 000 000
    ₽/1000      = $/сообщение × 1000 × курс_ЦБ

Средние берутся по 30 вопросам набора (а не по одному удачному), поэтому цифра
описывает обычный день бота, а не лучший случай.

Режимы:
    single     — один вызов модели (как было до агентного режима), цифры из замера;
    agent      — цикл «модель → инструмент → модель», цифры из замера;
    assistant  — classifier + agent: дешёвый классификатор сценария, затем исполнитель.
                 Цена классификатора не оценивается, а меряется тем же промптом, что
                 работает в бою (`--measure-classifier`, 30 вызовов, ~$0,0002).

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/agent_cost.py
    env -u PYTHONPATH .venv/bin/python scripts/agent_cost.py --json out.json
    env -u PYTHONPATH .venv/bin/python scripts/agent_cost.py --measure-classifier \\
        --json docs/reports/113-classifier-run-04.11.2026.json
    env -u PYTHONPATH .venv/bin/python scripts/agent_cost.py --check

`--check` пересчитывает цифры из сохранённых прогонов и сверяет их с
`docs/reports/113-agent-cost.json`: расхождение означает, что в отчёт попали числа
не из этого замера. Коды выхода: 0 — посчитано и сходится, 1 — расхождение или
негодные данные, 2 — нет ключа/провайдер недоступен (только для замера классификатора).

Тест: `tests/unit/test_agent_cost.py`.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
from datetime import datetime

import httpx
from _cli import ROOT, add_tool_api_path, apply_env, parser

apply_env()
add_tool_api_path()

from app import llm  # noqa: E402
from app.agently import loop, models  # noqa: E402

RUN_PATH = ROOT / "docs" / "reports" / "112-agent-eval-run-01.11.2026.json"
CLASSIFIER_PATH = ROOT / "docs" / "reports" / "113-classifier-run-04.11.2026.json"
EXPECTED_PATH = ROOT / "docs" / "reports" / "113-agent-cost.json"
SET_PATH = ROOT / "scripts" / "agent_eval_set.json"

TABLE_HEADER = ("| модель | режим | токенов вход | токенов выход | $/1000 | ₽/1000 |")
TABLE_ALIGN = "|---|---|---|---|---|---|"


def _load(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидался объект JSON")
    return data


def _price(model: str, prices: dict[str, tuple[float, float]]) -> tuple[float, float]:
    if model not in prices:
        raise ValueError(f"нет цены для модели {model} в config/agent-models.yaml")
    return prices[model]


def message_cost_usd(tokens_in: float, tokens_out: float,
                     price: tuple[float, float]) -> float:
    """Цена одного сообщения в долларах по измеренным токенам."""
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


def per_1000(tokens_in: float, tokens_out: float, price: tuple[float, float],
             usd_rub: float) -> dict:
    usd = message_cost_usd(tokens_in, tokens_out, price) * 1000
    return {"usd_per_1000": usd, "rub_per_1000": usd * usd_rub}


def run_mean(run: dict) -> dict:
    """Средние токены на сообщение и стоимость всего прогона по его ответам."""
    answers = run.get("answers") or []
    if not answers:
        raise ValueError(f"в прогоне {run.get('model')}/{run.get('mode')} нет ответов")
    tokens_in = statistics.fmean(float(item.get("tokens_in") or 0) for item in answers)
    tokens_out = statistics.fmean(float(item.get("tokens_out") or 0) for item in answers)
    return {"count": len(answers), "tokens_in": tokens_in, "tokens_out": tokens_out}


def analyse_run(run: dict, prices: dict, usd_rub: float) -> dict:
    model = str(run.get("model"))
    price = _price(model, prices)
    mean = run_mean(run)
    money = per_1000(mean["tokens_in"], mean["tokens_out"], price, usd_rub)
    total_usd = sum(message_cost_usd(float(item.get("tokens_in") or 0),
                                     float(item.get("tokens_out") or 0), price)
                    for item in run["answers"])
    metrics = run.get("metrics") or {}
    return {"model": model, "mode": str(run.get("mode")), "count": mean["count"],
            "tokens_in": round(mean["tokens_in"], 1),
            "tokens_out": round(mean["tokens_out"], 1),
            "usd_per_1000": round(money["usd_per_1000"], 4),
            "rub_per_1000": round(money["rub_per_1000"], 2),
            "run_cost_usd": round(total_usd, 6),
            "adequate": round(float(metrics.get("adequate") or 0), 3),
            "p95": metrics.get("p95")}


def analyse_classifier(doc: dict, prices: dict, usd_rub: float) -> dict:
    model = str(doc.get("model"))
    price = _price(model, prices)
    measured = doc.get("measured") or []
    if not measured:
        raise ValueError("в замере классификатора нет вызовов")
    tokens_in = statistics.fmean(float(item.get("tokens_in") or 0) for item in measured)
    tokens_out = statistics.fmean(float(item.get("tokens_out") or 0) for item in measured)
    money = per_1000(tokens_in, tokens_out, price, usd_rub)
    latencies = [float(item.get("latency") or 0) for item in measured]
    return {"model": model, "mode": "classifier", "count": len(measured),
            "tokens_in": round(tokens_in, 1), "tokens_out": round(tokens_out, 1),
            "usd_per_1000": round(money["usd_per_1000"], 4),
            "rub_per_1000": round(money["rub_per_1000"], 2),
            "p95": round(statistics.quantiles(latencies, n=20)[-1], 2) if latencies else None}


def combine(rows: list[dict], classifier: dict) -> list[dict]:
    """Режим `assistant` = классификатор + исполнитель: цены складываются."""
    result = []
    for row in rows:
        if row["mode"] != "agent":
            continue
        result.append({
            "model": row["model"], "mode": "assistant",
            "count": row["count"],
            "tokens_in": round(row["tokens_in"] + classifier["tokens_in"], 1),
            "tokens_out": round(row["tokens_out"] + classifier["tokens_out"], 1),
            "usd_per_1000": round(row["usd_per_1000"] + classifier["usd_per_1000"], 4),
            "rub_per_1000": round(row["rub_per_1000"] + classifier["rub_per_1000"], 2),
            "classifier_model": classifier["model"],
            "adequate": row["adequate"], "p95": row["p95"]})
    return result


def table(rows: list[dict]) -> str:
    lines = [TABLE_HEADER, TABLE_ALIGN]
    for row in rows:
        lines.append("| {model} | {mode} | {tokens_in} | {tokens_out} | {usd} | {rub} |".format(
            model=row["model"], mode=row["mode"], tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"], usd=row["usd_per_1000"], rub=row["rub_per_1000"]))
    return "\n".join(lines)


def measure_classifier(json_path: pathlib.Path | None) -> int:
    """Прогнать классификатор по набору A-0 и записать измеренные токены.

    Промпт берётся из `loop.classifier_payload` — тот же, что уходит в бою. Иначе
    мерялась бы другая строка, и цена классификатора была бы выдуманной.
    """
    if not llm.is_configured():
        print("Нет LLM_API_KEY: замер классификатора невозможен", file=sys.stderr)
        return 2
    questions = _load(SET_PATH).get("questions") or []
    if not questions:
        print(f"{SET_PATH}: нет вопросов", file=sys.stderr)
        return 1
    measured = []
    with httpx.Client(timeout=llm.timeout()) as client:
        for question in questions:
            payload = loop.classifier_payload(str(question.get("text") or ""))
            started = datetime.now()
            try:
                response = client.post(
                    llm.chat_url(),
                    headers={"Authorization": f"Bearer {llm.api_key()}",
                             "Content-Type": "application/json"},
                    json=payload)
            except httpx.HTTPError as exc:
                print(f"провайдер недоступен: {exc.__class__.__name__}", file=sys.stderr)
                return 2
            latency = (datetime.now() - started).total_seconds()
            if response.status_code >= 400:
                print(f"провайдер вернул {response.status_code}", file=sys.stderr)
                return 2
            data = response.json()
            usage = data.get("usage") or {}
            measured.append({"question": question.get("id"),
                             "tokens_in": int(usage.get("prompt_tokens") or 0),
                             "tokens_out": int(usage.get("completion_tokens") or 0),
                             "latency": round(latency, 2)})
    document = {"ran_at": datetime.now().date().isoformat(),
                "model": models.role_model(models.ROLE_CLASSIFIER),
                "mode": "classifier", "count": len(measured), "measured": measured}
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
        print(f"замер классификатора записан: {json_path}")
    tokens_in = statistics.fmean(item["tokens_in"] for item in measured)
    tokens_out = statistics.fmean(item["tokens_out"] for item in measured)
    print(f"классификатор {document['model']}: "
          f"{tokens_in:.1f} вход / {tokens_out:.1f} выход токенов на сообщение")
    return 0


def build(run_path: pathlib.Path, classifier_path: pathlib.Path | None) -> dict:
    prices = models.prices()
    rates = models.rates()
    if not prices:
        raise ValueError("в config/agent-models.yaml нет цен: считать нечем")
    if not rates["usd_rub_source"]:
        raise ValueError("в config/agent-models.yaml не указан источник курса")
    run_doc = _load(run_path)
    rows = [analyse_run(run, prices, rates["usd_rub"])
            for run in run_doc.get("runs") or []]
    result: dict = {"source_run": str(run_path.relative_to(ROOT)),
                    "ran_at": run_doc.get("ran_at"),
                    "usd_rub": rates["usd_rub"],
                    "usd_rub_date": rates["usd_rub_date"],
                    "usd_rub_source": rates["usd_rub_source"],
                    "rows": rows}
    if classifier_path is not None and classifier_path.is_file():
        classifier = analyse_classifier(_load(classifier_path), prices, rates["usd_rub"])
        result["classifier"] = classifier
        result["classifier_source"] = str(classifier_path.relative_to(ROOT))
        rows = rows + combine(rows, classifier)
        result["rows"] = rows
    return result


def check(result: dict, expected_path: pathlib.Path) -> int:
    """Сверка посчитанного с сохранёнными цифрами отчёта."""
    if not expected_path.is_file():
        print(f"нет файла ожиданий: {expected_path}", file=sys.stderr)
        return 1
    expected = _load(expected_path)
    key = lambda row: (row["model"], row["mode"])  # noqa: E731
    actual = {key(row): row for row in result["rows"]}
    wanted = {key(row): row for row in expected.get("rows") or []}
    problems = []
    for name in sorted(set(actual) | set(wanted), key=str):
        if name not in actual:
            problems.append(f"{name}: есть в отчёте, нет в расчёте")
        elif name not in wanted:
            problems.append(f"{name}: есть в расчёте, нет в отчёте")
        else:
            for field in ("tokens_in", "tokens_out", "usd_per_1000", "rub_per_1000"):
                left, right = actual[name][field], wanted[name][field]
                if abs(float(left) - float(right)) > 0.011:
                    problems.append(f"{name} {field}: {left} != {right}")
    if problems:
        for problem in problems:
            print(f"РАСХОЖДЕНИЕ: {problem}", file=sys.stderr)
        return 1
    print(f"сходится: {len(actual)} строк, курс {result['usd_rub']} "
          f"({result['usd_rub_date']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = parser(__doc__ or "")
    ap.add_argument("--run", metavar="PATH",
                    help="сохранённый прогон замера A-0 (по умолчанию "
                         "docs/reports/112-agent-eval-run-01.11.2026.json)")
    ap.add_argument("--classifier", metavar="PATH",
                    help="замер классификатора; по умолчанию "
                         "docs/reports/113-classifier-run-02.11.2026.json, если он есть")
    ap.add_argument("--json", metavar="PATH", help="куда записать расчёт в JSON")
    ap.add_argument("--check", action="store_true",
                    help="сверить расчёт с docs/reports/113-agent-cost.json")
    ap.add_argument("--against", metavar="PATH", help="файл ожиданий для --check")
    ap.add_argument("--measure-classifier", action="store_true",
                    help="прогнать классификатор по набору A-0 и записать токены")
    args = ap.parse_args(argv)
    if getattr(args, "measure_classifier", False):
        return measure_classifier(pathlib.Path(args.json) if args.json else None)
    run_path = pathlib.Path(args.run) if args.run else RUN_PATH
    classifier_path = pathlib.Path(args.classifier) if args.classifier else CLASSIFIER_PATH
    if not run_path.is_file():
        print(f"нет прогона замера: {run_path}", file=sys.stderr)
        return 1
    try:
        result = build(run_path, classifier_path)
    except (ValueError, KeyError) as exc:
        print(f"не посчитать: {exc}", file=sys.stderr)
        return 1
    print(table(result["rows"]))
    print(f"\nКурс: {result['usd_rub']} ₽/$ ({result['usd_rub_date']}), "
          f"источник: {result['usd_rub_source']}")
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"расчёт записан: {args.json}")
    if args.check:
        return check(result, pathlib.Path(args.against) if args.against else EXPECTED_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
