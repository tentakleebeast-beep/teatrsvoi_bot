#!/usr/bin/env python3
"""Записать разбор обращения в прод.

  answer_feedback.py --id 41 --status seen [--question V-031]
  (текст ответа приходит на stdin)

Зачем: разбор обращений с рабочей машины не проходит — сети до прода нет. Пишем
через тот же канал, что деплой (GitHub Actions по SSH). Текст передаётся stdin,
чтобы не экранировать его в SQL и не светить в argv.

Пишет строку в feedback_answer (журнал разборов) и обновляет feedback.status.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    p = argparse.ArgumentParser(description="Записать разбор обращения")
    p.add_argument("--id", required=True, type=int)
    p.add_argument("--status", required=True, choices=("seen", "planned", "done", "rejected"))
    p.add_argument("--question", default=None)
    p.add_argument("--source", default="agent", choices=("agent", "human"))
    args = p.parse_args()

    text = sys.stdin.read().strip()
    if not text:
        print("пустой текст ответа — отказ", file=sys.stderr)
        return 1

    import psycopg  # noqa: PLC0415 — нужен только на сервере

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 1

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select id from feedback where id = %s", (args.id,))
        if cur.fetchone() is None:
            print(f"обращения {args.id} нет", file=sys.stderr)
            return 1
        cur.execute(
            "insert into feedback_answer (feedback_id, source, status, text, question_id) "
            "values (%s, %s, %s, %s, %s)",
            (args.id, args.source, args.status, text, args.question))
        cur.execute(
            "update feedback set status = %s, resolution = %s, "
            "resolved_at = case when %s in ('done','rejected') then now() else resolved_at end "
            "where id = %s",
            (args.status, args.status, args.status, args.id))
        conn.commit()
        cur.execute("select count(*) from feedback_answer where feedback_id = %s", (args.id,))
        total = cur.fetchone()[0]

    print(f"обращение {args.id}: статус {args.status}, ответов в журнале {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
