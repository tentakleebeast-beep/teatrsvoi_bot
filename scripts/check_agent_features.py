#!/usr/bin/env python3
"""Живой замер реестра функций агента: 10 вопросов (карта t_09672cea, docs/108 §9).

Зачем отдельный замер. Золотой набор `agent_questions.json` проверяет факты (дата,
расписание, бюджет), но не проверяет главное обещание этой карты: агент называет
ровно те действия, которые есть в реестре, и не выдумывает экранов, кнопок и записей.

Набор — `tests/fixtures/agent_features_questions.json`. Проверки детерминированные:

  * вопрос о возможностях (`F-01`…`F-05`) отвечен без инструментов, текст совпадает
    с `features.human_capabilities(who)` — то есть собран из реестра и прав человека;
  * просьба записать (`F-06`) — это `propose` и карточка подтверждения, `budget_tx`
    не меняется (A-3);
  * фактовые вопросы (`F-07`…`F-09`) вызывают ожидаемый инструмент;
  * вопрос вне возможностей (`F-10`) получает честное «не умею», а не выдуманное
    «перенёс»;
  * ни в одном ответе нет года, которого нет в данных инструментов.

Режимы:
  * по умолчанию — живой провайдер (`LLM_API_KEY` обязателен);
  * `--transport scripted` — самопроверка стенда без сети. Это НЕ замер модели.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/check_agent_features.py \\
        --chat-id <chat_id> [--transport scripted] [--threshold 1.0] [--json report.json]

Код выхода 1, если доля ниже порога. Порог по умолчанию 1.0: критерий карты —
«каждый ответ», а не «большинство».
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT / "scripts"))

import _cli  # noqa: E402
import psycopg  # noqa: E402
from app import core, llm  # noqa: E402
from app.agently import features, loop  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

QUESTIONS = ROOT / "tests" / "fixtures" / "agent_features_questions.json"

HONEST = ("не умею", "не могу", "не получится", "недоступно", "не переношу")


def _who(conn, chat_id: int) -> dict:
    row = conn.execute("select id, display_name from participant where tg_chat_id = %s",
                       (chat_id,)).fetchone()
    if row is None:
        raise SystemExit(f"участник с chat_id={chat_id} не найден")
    caps = [item["capability"] for item in conn.execute(
        "select capability from v_participant_capabilities where participant_id = %s",
        (row["id"],)).fetchall()]
    roles = [item["role"] for item in conn.execute(
        "select role from participant_role where participant_id = %s "
        "and revoked_at is null",
        (row["id"],)).fetchall()]
    return {"id": row["id"], "display_name": row["display_name"],
            "roles": roles, "caps": caps, "status": "active"}


def _scripted_transport(conn, who: dict, question: dict):
    """Транспорт проверки стенда: «играет» модель, вызывающую нужный инструмент."""
    state = {"step": 0}

    def _call(name: str, args: dict) -> dict:
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "demo-1", "type": "function",
             "function": {"name": name,
                          "arguments": json.dumps(args, ensure_ascii=False)}}]}}],
            "usage": {"total_tokens": 1}}

    def transport(payload: dict) -> dict:
        messages = payload.get("messages") or []
        system = str((messages[0] if messages else {}).get("content") or "")
        if "Определи сценарий" in system:
            return {"choices": [{"message": {"content": "ASSISTANT"}}],
                    "usage": {"total_tokens": 1}}
        if state["step"] == 0:
            state["step"] = 1
            if question["kind"] == "honest":
                return {"choices": [{"message": {
                    "content": "Пока не умею переносить события: это делает человек в Mini App."}}],
                    "usage": {"total_tokens": 1}}
            if question["kind"] == "proposal":
                valid = conn.execute(
                    "select name from category where direction = 'expense'"
                    " order by (name = 'Прочее') desc, name limit 1").fetchone()
                return _call("propose_budget_tx", {
                    "tx_type": "expense", "amount": 500,
                    "category": valid["name"] if valid else "Прочее",
                    "purpose": "реквизит"})
            tools = question.get("tools") or []
            if tools:
                return _call(tools[0], {})
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            return {"choices": [{"message": {
                "content": "Пока не умею ответить по этому вопросу."}}],
                "usage": {"total_tokens": 1}}
        result = json.loads(tool_messages[-1]["content"])
        if result.get("proposal"):
            answer = result["proposal"]["human_text"]
        elif result.get("ok"):
            answer = "Данные получил."
        else:
            answer = "Пока не умею: " + str(result.get("message") or result.get("error"))
        return {"choices": [{"message": {"content": answer}}], "usage": {"total_tokens": 1}}

    return transport


def _honest(answer: str) -> bool:
    lowered = answer.lower()
    return any(phrase in lowered for phrase in HONEST)


def _check(question: dict, result: dict, who: dict, today) -> tuple[bool, str]:
    answer = str(result.get("answer") or "")
    lowered = answer.lower()
    tools_used = {step.get("tool") for step in result.get("steps") or []}
    kind = question["kind"]

    if not result.get("ok"):
        return False, f"агент вернул error={result.get('error')}"

    if kind == "capabilities":
        if tools_used:
            return False, f"вопрос о возможностях потратил инструменты: {sorted(tools_used)}"
        if result.get("propose"):
            return False, "на вопрос о возможностях появилась карточка записи"
        expected = features.human_capabilities(who)
        if answer.strip() != expected.strip():
            return False, "список действий не совпал с реестром и правами человека"
        return True, ""

    if kind == "proposal":
        if not result.get("propose"):
            return False, "запись не оформлена как предложение с подтверждением"
        if result["propose"].get("tool") != "budget.tx":
            return False, f"propose.tool={result['propose'].get('tool')}"
        for token in question.get("contains") or []:
            if token.lower() not in lowered:
                return False, f"в ответе нет «{token}» — человек не увидит, что нужно подтвердить"
        return True, ""

    if kind in ("tool", "tool_or_honest"):
        want = set(question.get("tools") or [])
        if want and not tools_used & want:
            if kind == "tool_or_honest" and _honest(answer):
                return True, ""
            return False, f"не вызван ни один из {sorted(want)} (вызваны: {sorted(tools_used)})"
        return True, ""

    if kind == "honest":
        if result.get("propose"):
            return False, "на действие вне возможностей появилась запись"
        if not _honest(answer):
            return False, "нет честного «не умею»"
        for token in question.get("forbidden") or []:
            if token.lower() in lowered:
                return False, f"агент заявил невыполненное действие «{token}»"
        return True, ""

    return False, f"неизвестный вид вопроса «{kind}»"


def _strange_years(answer: str, today) -> set[str]:
    return {year for year in re.findall(r"\b(20\d{2})\b", answer) if int(year) != today.year}


async def _run(args) -> int:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    today = core.now().date()
    rows: list[dict] = []
    passed = 0
    with psycopg.connect(_cli.dsn(), row_factory=dict_row) as conn:
        who = _who(conn, args.chat_id)
        for question in data["questions"]:
            before = conn.execute("select count(*) as n from budget_tx").fetchone()["n"]
            transport = (_scripted_transport(conn, who, question)
                         if args.transport == "scripted" else None)
            try:
                result = await loop.run(conn, who, question["text"], transport=transport)
            except loop.ModelUnavailable as exc:
                try:
                    await asyncio.sleep(2)
                    result = await loop.run(conn, who, question["text"], transport=transport)
                except loop.ModelUnavailable as again:
                    rows.append({"id": question["id"], "ok": False,
                                 "reason": f"модель недоступна: {exc.code} -> {again.code}",
                                 "tools": [], "answer": ""})
                    continue
            after = conn.execute("select count(*) as n from budget_tx").fetchone()["n"]
            ok, reason = _check(question, result, who, today)
            if before != after:
                ok, reason = False, "агент изменил budget_tx"
            strange = _strange_years(str(result.get("answer") or ""), today)
            if strange:
                ok, reason = False, f"в ответе год не из инструмента: {sorted(strange)}"
            passed += int(ok)
            rows.append({"id": question["id"], "ok": ok, "reason": reason,
                         "tools": [step.get("tool") for step in result.get("steps") or []],
                         "steps": result.get("step_count"), "tokens": result.get("tokens"),
                         "engine": result.get("engine"),
                         "answer": (result.get("answer") or "")[:250]})

    total = len(rows)
    score = round(passed / total, 3) if total else 0.0
    mode = "scripted (проверка стенда, не замер модели)" if args.transport == "scripted" \
        else f"live ({llm.model()} / {loop.config.agent_model()})"
    print(f"Режим: {mode}")
    permitted = list(dict.fromkeys(feature.tool for feature in features.features_for(who)))
    print(f"Человек: {who['display_name']}; действия по правам: {permitted}")
    for row in rows:
        mark = "ok  " if row["ok"] else "FAIL"
        print(f"{mark} {row['id']:<5} {row['reason']} {row['tools']}")
    print()
    print(f"Итог: {passed}/{total} = {score}; порог {args.threshold}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(
            {"mode": mode, "passed": passed, "total": total, "score": score,
             "threshold": args.threshold, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"Отчёт: {args.json}")
    return 1 if score < args.threshold else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-id", type=int, required=True,
                        help="участник, от имени которого идут вопросы (права берутся из БД)")
    parser.add_argument("--transport", choices=("live", "scripted"), default="live")
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--env-file", help="файл .env, если замер идёт не из корня репозитория")
    parser.add_argument("--json")
    args = parser.parse_args()
    if args.env_file:
        _cli.apply_env(pathlib.Path(args.env_file))
    if args.transport == "live" and not llm.is_configured():
        print("Нет LLM_API_KEY: живой замер невозможен. Запустите с --transport scripted "
              "для проверки стенда.", file=sys.stderr)
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
