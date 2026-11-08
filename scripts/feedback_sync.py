#!/usr/bin/env python3
"""Мост прод ↔ dev для разбора обращений.

Зачем: разбор идёт на этой машине (здесь удобнее и понятнее), а живые обращения
появляются в прод-базе. Прямого доступа к прод-базе нет, поэтому ходим через
tool-api на проде — тот же путь, которым пользуется Mini App.

Правила, которые здесь зашиты:
1. Локальную базу не имитируем: обращения не копируются в dev-БД, иначе ответы
   уйдут в пустоту — авторов и журнала `feedback_answer` в dev-базе нет.
2. Источник истины — прод. `pull` только читает, `decide` пишет решение в прод.
3. Сеть до прода нестабильна (трафик идёт через VPN): каждый запрос повторяется
   с экспоненциальной паузой. Пропущенные обращения не теряются — они остаются
   в очереди на проде, и следующий тик их снова увидит.

  env -u PYTHONPATH .venv/bin/python scripts/feedback_sync.py pull
  env -u PYTHONPATH .venv/bin/python scripts/feedback_sync.py decide --id 41 \
      --status planned --text "Принято в работу, задача поставлена" --question-id V-028

Код выхода: 0 — успех, 1 — прод ответил отказом, 2 — нет настроек,
3 — сеть недоступна после всех попыток, 4 — в прод нет ни одного обращения.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

DEFAULT_TIMEOUT = 20
DEFAULT_RETRIES = 4
BACKOFF_START = 5.0        # секунды; дальше 5, 10, 20, 40
STATE_FILE = ROOT / ".state" / "feedback_sync.json"


def load_env() -> dict[str, str]:
    """Читает .env без сторонних зависимостей: только KEY=VALUE и комментарии."""
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def settings(env: dict[str, str]) -> tuple[str, str, str]:
    base = (os.getenv("PROD_API_URL") or env.get("PROD_API_URL")
            or "https://svoi-theater-samara.online").rstrip("/")
    # У прода СВОЙ сервисный токен (генерируется при развёртывании): локальный
    # TOOL_API_TOKEN подходит только для dev-tool-api и прод его отвергает (401).
    token = (os.getenv("PROD_TOOL_API_TOKEN") or env.get("PROD_TOOL_API_TOKEN")
             or os.getenv("TOOL_API_TOKEN") or env.get("TOOL_API_TOKEN") or "")
    chat = os.getenv("ADMIN_CHAT_ID") or env.get("ADMIN_CHAT_ID") or ""
    return base, token, chat


def request(url: str, token: str, chat: str, payload: dict | None = None) -> dict:
    """Запрос к проду с ретраями: сеть до него периодически отваливается."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "X-Actor-Chat-Id": chat}
    if data is not None:
        headers["Content-Type"] = "application/json"

    delay = BACKOFF_START
    last_error = ""
    for attempt in range(1, DEFAULT_RETRIES + 1):
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:              # прод ответил, но отказом
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise SystemExit(
                    f"прод отказал в доступе ({e.code}): проверьте PROD_TOOL_API_TOKEN "
                    f"в .env — это токен из /opt/svoi/.env на сервере, а не локальный. "
                    f"Ответ прода: {body}") from e
            raise SystemExit(f"прод ответил {e.code}: {body}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = str(e)
            if attempt < DEFAULT_RETRIES:
                print(f"сеть: попытка {attempt} не прошла ({last_error}); пауза {delay:.0f} с",
                      file=sys.stderr)
                time.sleep(delay)
                delay *= 2
    raise SystemExit(f"сеть до прода недоступна после {DEFAULT_RETRIES} попыток: {last_error}")


def cmd_pull(env: dict[str, str], args) -> int:
    base, token, chat = settings(env)
    if not token or not chat:
        print("нет TOOL_API_TOKEN или ADMIN_CHAT_ID в .env", file=sys.stderr)
        return 2

    data = request(f"{base}/api/tools/feedback?open_only=true&limit={args.limit}",
                   token, chat)
    rows = data.get("data") or []
    if not rows:
        print("open=0")
        return 4

    snapshot = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "count": len(rows), "data": rows}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    if args.json:
        print(json.dumps({"ok": True, "count": len(rows), "data": rows},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"открытых обращений: {len(rows)}")
    for row in rows:
        text = " ".join((row.get("text") or "").split())[:110]
        print(f"  №{row.get('id')} | {row.get('kind')} | {row.get('author') or '-'} | "
              f"{row.get('created_at')} | {text}")
    return 0


def cmd_decide(env: dict[str, str], args) -> int:
    base, token, chat = settings(env)
    if not token or not chat:
        print("нет TOOL_API_TOKEN или ADMIN_CHAT_ID в .env", file=sys.stderr)
        return 2

    payload = {"feedback_id": args.id, "status": args.status,
               "resolution": args.text, "source": "agent"}
    if args.question_id:
        payload["question_id"] = args.question_id
    if args.commit:
        payload["commit_sha"] = args.commit
    if args.no_notify:
        payload["notify"] = False

    result = request(f"{base}/api/tools/feedback/decide", token, chat, payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Мост обращений прод → dev")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull", help="что открыто на проде")
    pull.add_argument("--limit", type=int, default=50)
    pull.add_argument("--json", action="store_true", help="машинный вывод для задания")
    pull.set_defaults(func=cmd_pull)

    decide = sub.add_parser("decide", help="записать решение в прод и ответить автору")
    decide.add_argument("--id", type=int, required=True, dest="id")
    decide.add_argument("--status", required=True,
                        choices=["seen", "planned", "done", "rejected"])
    decide.add_argument("--text", default="")
    decide.add_argument("--question-id", default=None, dest="question_id")
    decide.add_argument("--commit", default=None, dest="commit")
    decide.add_argument("--no-notify", action="store_true", dest="no_notify")
    decide.set_defaults(func=cmd_decide)

    args = parser.parse_args()
    try:
        return args.func(load_env(), args)
    except SystemExit as e:
        if isinstance(e.code, int):
            return e.code or 1
        print(str(e.code), file=sys.stderr)     # текст уже готов — показываем человеку
        return 3


if __name__ == "__main__":
    sys.exit(main())
