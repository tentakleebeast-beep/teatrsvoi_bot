#!/usr/bin/env python3
"""Замер агентного режима на золотом наборе из 30 вопросов (A-1, docs/development/108 §6).

Зачем: «стало лучше» — не критерий. Нужна доля адекватных ответов цифрой и
воспроизводимый прогон, чтобы сравнивать модели и промпты на одном наборе.

Набор — `tests/fixtures/agent_questions.json`: вопросы из обращений и падений.
Проверки детерминированные, глазами не судим:
  * вызван ожидаемый инструмент (если вопрос требует фактов);
  * в ответе есть обязательные подстроки (дата считается на момент прогона);
  * в ответе нет запрещённых подстрок (выдуманный год);
  * просьба записать — это `propose`, и `budget_tx` не меняется;
  * общий ответ без предложения записи, если запись не просили.

Режимы:
  * по умолчанию — живой провайдер (`LLM_API_KEY` обязателен);
  * `--transport rules` — самопроверка стенда без сети: транспорт отвечает
    детерминированными правилами. Это НЕ замер модели, а проверка того, что
    набор и подсчёт работают. В отчёте такой прогон помечается явно.

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/check_agent_questions.py \
        --chat-id <chat_id> [--transport rules] [--threshold 0.8] [--json report.json]

Код выхода 1, если доля ниже порога.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))
sys.path.insert(0, str(ROOT / "scripts"))

import _cli  # noqa: E402
import psycopg  # noqa: E402
from app import core, llm  # noqa: E402
from app.agently import loop, registry  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

QUESTIONS = ROOT / "tests" / "fixtures" / "agent_questions.json"


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
    """Транспорт проверки стенда: сам «играет» модель, вызывающую нужный инструмент.

    Он не измеряет качество модели — он проверяет, что набор вопросов, цикл,
    инструменты и подсчёт работают вместе. В отчёте такой режим помечается явно.
    """
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
            tools = question.get("tools") or []
            if tools:
                return _call(tools[0], {})
            if question.get("propose"):
                amount = loop.rules.detect_amount(question["text"]) or 1000
                tx_type = loop.rules.detect_type(question["text"]) or "expense"
                category = loop.rules.detect_category(question["text"]) or "Прочее"
                valid = conn.execute(
                    "select name from category where name = %s and direction = %s",
                    (category, tx_type)).fetchone()
                if valid is None:
                    valid = conn.execute(
                        "select name from category where direction = %s order by name limit 1",
                        (tx_type,)).fetchone()
                category = valid["name"] if valid else ""
                return _call("propose_budget_tx", {
                    "tx_type": tx_type, "amount": amount, "category": category,
                    "purpose": category})
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            return {"choices": [{"message": {"content": "Отвечаю без инструментов."}}],
                    "usage": {"total_tokens": 1}}
        result = json.loads(tool_messages[-1]["content"])
        if "human" in result:
            answer = f"Сегодня {result['human']}."
        elif result.get("proposal"):
            answer = result["proposal"]["human_text"]
        elif result.get("ok"):
            answer = "Данные получил."
        else:
            answer = "Данных нет: " + str(result.get("message") or result.get("error"))
        return {"choices": [{"message": {"content": answer}}], "usage": {"total_tokens": 1}}

    return transport


MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _date_forms(token: str) -> list[str]:
    """Равнозначные записи даты: 01.11.2026, 1.11.2026, 1 ноября 2026, 2026-11-01.

    Проверяем смысл, а не оформление: человеку важно, что дата взята из `now`,
    а не то, поставила ли модель ведущий ноль.
    """
    try:
        day = datetime.strptime(token, "%d.%m.%Y")
    except ValueError:
        return [token]
    return [token, f"{day.day}.{day.month:02d}.{day.year}", f"{day.day}.{day.month}.{day.year}",
            f"{day.day} {MONTHS_GEN[day.month - 1]} {day.year}", day.date().isoformat()]


def _check(question: dict, result: dict, today) -> tuple[bool, str]:
    answer = str(result.get("answer") or "")
    lowered = answer.lower()
    tools_used = {step.get("tool") for step in result.get("steps") or []}
    want = question.get("tools") or []
    if want and not tools_used & set(want):
        return False, f"не вызван ни один из инструментов {want} (вызваны: {sorted(tools_used)})"
    for token in question.get("contains") or []:
        expected = (token.replace("{today}", today.isoformat())
                    .replace("{today_dmy}", today.strftime("%d.%m.%Y"))
                    .replace("{today_weekday}", loop.WEEKDAYS_RU[today.weekday()]))
        if not any(form.lower() in lowered for form in _date_forms(expected)):
            return False, f"в ответе нет «{expected}»"
    for token in question.get("forbidden") or []:
        if token.lower() in lowered:
            return False, f"в ответе выдуманный «{token}»"
    # Год не из инструмента — тот самый дефект «27 октября 2023 года».
    strange = {year for year in re.findall(r"\b(20\d{2})\b", answer) if int(year) != today.year}
    if strange:
        return False, f"в ответе год не из инструмента: {sorted(strange)}"
    has_propose = bool(result.get("propose"))
    if bool(question.get("propose")) != has_propose:
        return False, f"propose={has_propose}, ожидалось {bool(question.get('propose'))}"
    if not result.get("ok"):
        return False, f"агент вернул error={result.get('error')}"
    return True, ""


async def _run(args) -> int:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    today = core.now().date()
    dsn = _cli.dsn()
    rows: list[dict] = []
    passed = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        who = _who(conn, args.chat_id)
        for question in data["questions"]:
            before = conn.execute("select count(*) as n from budget_tx").fetchone()["n"]
            transport = (_scripted_transport(conn, who, question)
                         if args.transport == "scripted" else None)
            try:
                result = await loop.run(conn, who, question["text"], transport=transport)
            except loop.ModelUnavailable as exc:
                # Провайдер моргнул (таймаут/5xx) — это не качество ответа. Одна повторная
                # попытка, чтобы сетевой сбой не портил замер модели.
                try:
                    await asyncio.sleep(2)
                    result = await loop.run(conn, who, question["text"], transport=transport)
                except loop.ModelUnavailable as again:
                    rows.append({"id": question["id"], "ok": False,
                                 "reason": f"модель недоступна: {exc.code} -> {again.code}",
                                 "tools": [], "answer": ""})
                    continue
            after = conn.execute("select count(*) as n from budget_tx").fetchone()["n"]
            ok, reason = _check(question, result, today)
            if before != after:
                ok, reason = False, "агент изменил budget_tx"
            passed += int(ok)
            rows.append({"id": question["id"], "ok": ok, "reason": reason,
                         "tools": [step.get("tool") for step in result.get("steps") or []],
                         "steps": result.get("step_count"), "tokens": result.get("tokens"),
                         "answer": (result.get("answer") or "")[:200]})

    total = len(rows)
    score = round(passed / total, 3) if total else 0.0
    mode = "scripted (проверка стенда, не замер модели)" if args.transport == "scripted" \
        else f"live ({llm.model()} / {loop.config.agent_model()})"
    print(f"Режим: {mode}")
    print(f"Инструменты у участника: {[tool.name for tool in registry.available_tools(who)]}")
    for row in rows:
        mark = "ok  " if row["ok"] else "FAIL"
        print(f"{mark} {row['id']:<10} {row['reason']} {row['tools']}")
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
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--json")
    args = parser.parse_args()
    if args.transport == "live" and not llm.is_configured():
        print("Нет LLM_API_KEY: живой замер невозможен. Запустите с --transport scripted "
              "для проверки стенда.", file=sys.stderr)
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
