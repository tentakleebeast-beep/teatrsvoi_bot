#!/usr/bin/env python3
"""Очередь разбора обратной связи (задание «разбор обратной связи», 30.10.2026).

Зачем отдельный скрипт: задание планировщика идёт каждые 10 минут и каждый раз
начинается с чистого контекста. Ему нужны три операции без догадок:

  list       — какие обращения ещё не разобраны (нет строки в `feedback_answer`);
  answer     — ответить автору и записать ответ в журнал (status done/planned/...);
  question   — записать открытый вопрос в docs/reports/74-questions.md (V-NNN)
               и отметить обращение как посмотренное, не отправляя автору
               полуготовый ответ.

Журнал `feedback_answer` (db/init/047) — единственный признак «разобрано»:
ответ хранится и в `feedback.resolution`, но только журнал помнит, что разбор
уже был, и не даёт задавать один и тот же вопрос каждые 10 минут.

  env -u PYTHONPATH .venv/bin/python scripts/feedback_queue.py list
  env -u PYTHONPATH .venv/bin/python scripts/feedback_queue.py answer --id 35 \
      --status done --text "Исправлено: ..." --commit 1a2b3c4
  env -u PYTHONPATH .venv/bin/python scripts/feedback_queue.py question --id 35 \
      --text "Делать ли ...? Варианты: ..."

Код выхода: 0 — успех, 1 — отказ tool-api или ошибка разбора, 2 — нет настроек.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

import psycopg
from _cli import api_url, apply_env, dsn, env, now_local
from _questions import QUESTIONS, append_question, next_question_id
from psycopg.rows import dict_row

apply_env()

DB = dsn()
API = api_url()
TOKEN = env("TOOL_API_TOKEN")
ADMIN_CHAT = env("ADMIN_CHAT_ID")

OPEN_STATUSES = ("new", "seen", "planned")
TIMEOUT = 20

# Нумерация и запись строки вынесены в `_questions`: у файла вопросов два писателя
# (разбор обратной связи и конвейер листа «Чего не умеем», V-035), и копия нумерации
# в двух местах разошлась бы молча. Имена оставлены доступными отсюда.
__all__ = ("append_question", "next_question_id")


def today_local() -> str:
    """Дата в файле вопросов: местная, без зоны — файл читает человек."""
    return now_local(naive=True).strftime("%d.%m.%Y")


def queue(conn, limit: int = 5) -> list[dict]:
    """Неразобранные обращения, старые первыми.

    Признак «разобрано» — строка в `feedback_answer`. Поэтому повторный прогон
    задания не отвечает дважды и не задаёт один вопрос каждые 10 минут.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("""
        select f.id, f.kind, f.status, f.text, f.context, f.created_at,
               f.participant_id,
               coalesce(p.display_name, 'участник') as author,
               p.tg_chat_id is not null as author_reachable,
               (extract(epoch from now() - f.created_at) / 60)::int as age_minutes
          from feedback f left join participant p on p.id = f.participant_id
         where f.status = any(%s)
           and not exists (select 1 from feedback_answer a where a.feedback_id = f.id)
         order by f.created_at, f.id
         limit %s""", (list(OPEN_STATUSES), limit)).fetchall()
    for row in rows:
        row["created_at"] = row["created_at"].isoformat()
    return rows


def decide(payload: dict) -> dict:
    """Разбор через API: смена статуса, журнал, уведомление автору."""
    if not TOKEN or not ADMIN_CHAT:
        raise RuntimeError("нет TOOL_API_TOKEN или ADMIN_CHAT_ID в .env")
    request = urllib.request.Request(
        f"{API}/tools/feedback/decide",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}",
                 "X-Actor-Chat-Id": str(ADMIN_CHAT)})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"tool-api {exc.code}: {detail[:300]}") from exc


def answer(feedback_id: int, status: str, text: str, commit: str | None,
           notify: bool) -> dict:
    return decide({"feedback_id": feedback_id, "status": status,
                   "resolution": text, "source": "agent",
                   "commit_sha": commit, "notify": notify})


def ask_question(feedback_id: int, text: str, source: str | None,
                 dry_run: bool) -> dict:
    """Вопрос в файл + запись разбора без уведомления автору.

    Файл пишется первым: если tool-api недоступен, файл откатывается, и вопрос
    не остаётся в документе без следа в журнале.
    """
    original = QUESTIONS.read_text(encoding="utf-8")
    updated, question_id = append_question(
        original, text, source or f"обращение №{feedback_id}", today_local())
    if dry_run:
        added = [line for line in updated.splitlines() if question_id in line]
        return {"ok": True, "dry_run": True, "question_id": question_id,
                "row": added[0] if added else ""}
    QUESTIONS.write_text(updated, encoding="utf-8")
    try:
        result = decide({"feedback_id": feedback_id, "status": "seen",
                         "resolution": f"{question_id}: {text}", "source": "agent",
                         "question_id": question_id, "notify": False})
    except Exception:
        QUESTIONS.write_text(original, encoding="utf-8")
        raise
    return {"ok": True, "question_id": question_id, "api": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Очередь разбора обратной связи")
    parser.add_argument("command", nargs="?", default="list",
                        choices=("list", "answer", "question"))
    parser.add_argument("--id", type=int, help="номер обращения")
    parser.add_argument("--status", default="done",
                        choices=("seen", "planned", "done", "rejected"))
    parser.add_argument("--text", default="", help="ответ автору или текст вопроса")
    parser.add_argument("--commit", default=None, help="хеш коммита с доработкой")
    parser.add_argument("--source", default=None,
                        help="откуда вопрос (по умолчанию «обращение №N»)")
    parser.add_argument("--no-notify", action="store_true",
                        help="не отправлять ответ автору (обращение закрывается тихо)")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "list":
        with psycopg.connect(DB, row_factory=dict_row) as conn:
            rows = queue(conn, args.limit)
        print(json.dumps({"ok": True, "count": len(rows), "data": rows},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    if args.id is None:
        parser.error("нужен --id")
    if not args.text.strip():
        parser.error("нужен --text")

    if args.command == "answer":
        result = answer(args.id, args.status, args.text.strip(), args.commit,
                        not args.no_notify)
    else:
        result = ask_question(args.id, args.text.strip(), args.source, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
