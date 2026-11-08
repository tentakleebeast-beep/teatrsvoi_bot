#!/usr/bin/env python3
"""Серверный разбор обращений: вопрос и благодарность — ответ, баг — доставка (t_17f137b8).

Правило владельца 02.11.2026: на сервере НЕЛЬЗЯ разбирать баги и пожелания —
сервер не коммитит в git, работа идёт в dev-среде. Поэтому скрипт делает ровно
две вещи и решает это не сам, а маршрутом tool-api:

  * `question` — ответ модели, уведомление автору, строка в журнале;
  * `praise`   — короткая благодарность, без модели;
  * `bug`/`wish` — «Принято, передано в разработку.» и строка журнала
    `source='server'`; карту заводит dev-среда из `feedback/inbox/`.

Скрипт вызывается systemd-таймером `svoi-feedback-triage` каждые 10 минут
(`deploy/systemd/svoi-feedback-triage.*`). По умолчанию ничего не пишет — только
показывает очередь; запись — явный `--apply`.

    env -u PYTHONPATH .venv/bin/python scripts/feedback_triage.py
    env -u PYTHONPATH .venv/bin/python scripts/feedback_triage.py --apply

Код выхода: 0 — успех, 1 — отказ tool-api, 2 — нет настроек.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from _cli import api_url, apply_env, env

apply_env()

API = api_url()
TOKEN = env("TOOL_API_TOKEN")
ADMIN_CHAT = env("ADMIN_CHAT_ID")
TIMEOUT = 60


def post(path: str, payload: dict) -> dict:
    """Запрос к tool-api от имени админа: тот же ключ, что у ручного разбора."""
    if not TOKEN or not ADMIN_CHAT:
        raise RuntimeError("нет TOOL_API_TOKEN или ADMIN_CHAT_ID в .env")
    request = urllib.request.Request(
        f"{API}{path}",
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
    except urllib.error.URLError as exc:
        raise RuntimeError(f"tool-api недоступен: {exc.reason}") from exc


def summary(result: dict) -> str:
    """Короткая строка в журнал: номера и виды, без текста обращений."""
    answered = ", ".join(f"№{item['id']}" for item in result.get("answered", [])) or "нет"
    handed = ", ".join(f"№{item['id']}" for item in result.get("handed", [])) or "нет"
    skipped = ", ".join(f"№{item['id']} ({item['reason']})"
                        for item in result.get("skipped", [])) or "нет"
    return f"ответили: {answered}; передали в разработку: {handed}; ждут: {skipped}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Серверный разбор обращений")
    parser.add_argument("--apply", action="store_true",
                        help="разобрать очередь (без флага — только показать)")
    parser.add_argument("--limit", type=int, default=5, help="сколько за прогон")
    args = parser.parse_args(argv)

    result = post("/tools/feedback/triage",
                  {"limit": args.limit, "dry_run": not args.apply})
    print(summary(result))
    for item in result.get("answered", []):
        print(f"  ответ №{item['id']}: {item.get('text') or 'будет ответ'}")
    if not args.apply:
        print("показать без записи: --apply")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
