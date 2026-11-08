#!/usr/bin/env python3
"""Отправить сообщение владельцу от имени боевого бота.

  notify_owner.py --text-file /tmp/notext            # получатель — ADMIN_CHAT_ID из .env
  notify_owner.py --chat <chat_id> --text-file /tmp/notext

Зачем отдельный скрипт: локальный `scripts/notify_admin.py` намеренно ходит dev-ботом
(правило dev-контура из карты t_4aa28fe9), а писать владельцу должен боевой бот.
Скрипт запускается на сервере, где живёт прод-токен; текст приходит файлом, чтобы
не попадать ни в argv, ни в журнал.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def main() -> int:
    p = argparse.ArgumentParser(description="Сообщение владельцу через бота проекта")
    p.add_argument("--chat", default=os.environ.get("ADMIN_CHAT_ID", "").strip(),
                   help="chat_id получателя; по умолчанию берётся ADMIN_CHAT_ID из окружения")
    p.add_argument("--text-file", required=True)
    args = p.parse_args()

    if not args.chat:
        print("не задан получатель: --chat или ADMIN_CHAT_ID в окружении", file=sys.stderr)
        return 1

    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        print("TELEGRAM_TOKEN не задан", file=sys.stderr)
        return 1

    with open(args.text_file, encoding="utf-8") as fh:
        text = fh.read().strip()[:3900]
    if not text:
        print("пустой текст — отправлять нечего", file=sys.stderr)
        return 1

    data = urllib.parse.urlencode({"chat_id": args.chat, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            body = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 — сообщение не ушло, причина важнее типа
        print(f"не отправлено: {exc}", file=sys.stderr)
        return 1

    print("отправлено, message_id:", body.get("result", {}).get("message_id"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
