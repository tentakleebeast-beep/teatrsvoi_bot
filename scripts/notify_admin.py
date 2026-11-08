#!/usr/bin/env python3
"""Короткая сводка админу в Telegram — через бота проекта, а не через шлюзы Hermes.

Зачем отдельный скрипт: доставка задания планировщика Hermes (`deliver: telegram:...`)
требует настроенного Telegram-шлюза Hermes, которого в этом проекте нет — бот живёт
на своём aiogram-поллере. Поэтому сводку отправляет сам бот, тем же токеном,
которым работает с людьми.

  env -u PYTHONPATH .venv/bin/python scripts/notify_admin.py "Итерация 42
  Сделано: ...
  Дальше: ..."
  ... --dry-run        # показать, что уйдёт, и не отправлять
  ... --to 12345       # другому адресату
  echo "текст" | ... scripts/notify_admin.py     # текст со stdin

Код выхода: 0 — отправлено, 1 — отказ Telegram, 2 — нет токена/адресата или пусто.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))

from run_service import DevEnvNotConfigured, service_env  # noqa: E402

LIMIT = 4000          # запас до лимита Telegram 4096
TIMEOUT = 15
# Сводку шлёт dev-бот: локальный скрипт не должен говорить от имени боевого
# (карта t_4aa28fe9). Нет DEV_BOT_TOKEN — понятный отказ, а не прод-токен.
ENVIRONMENT = "dev"


def build_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Окружение скрипта: файл dev-среды и dev-бот."""
    return service_env("gateway", ENVIRONMENT, base)


def env_or_empty() -> dict[str, str]:
    """Окружение для разбора аргументов: без dev-конфига адресат берётся из аргументов.

    Guard остаётся в `build_env` (и срабатывает до отправки), но разбор `--to`
    не должен падать из-за отсутствующего `.env.dev`.
    """
    try:
        return build_env()
    except (SystemExit, DevEnvNotConfigured):
        return {}


def send(text: str, token: str, chat_id: int, timeout: int = TIMEOUT) -> dict:
    """Отправляет сообщение и возвращает ответ Bot API."""
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text[:LIMIT],
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def compose(argv: list[str] | None = None) -> tuple[str, int, bool]:
    """Разбирает аргументы: текст (аргументы или stdin), адресат, признак dry-run."""
    parser = argparse.ArgumentParser(description="Сводка админу через бота проекта")
    parser.add_argument("text", nargs="*", help="текст сводки")
    parser.add_argument("--to", type=int, default=None, help="chat_id получателя")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    text = " ".join(args.text).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    env = env_or_empty()
    chat_id = args.to or int(env.get("ADMIN_CHAT_ID", "0") or 0)
    return text, chat_id, args.dry_run


def main(argv: list[str] | None = None) -> int:
    text, chat_id, dry_run = compose(argv)
    try:
        env = build_env()
    except DevEnvNotConfigured as exc:      # среда разработки не настроена
        print(exc, file=sys.stderr)
        return exc.code
    token = env.get("TELEGRAM_TOKEN", "").strip()

    if not text:
        print("пустой текст сводки", file=sys.stderr)
        return 2
    if not token or not chat_id:
        print("нет TELEGRAM_TOKEN или ADMIN_CHAT_ID", file=sys.stderr)
        return 2
    if dry_run:
        print(f"[dry-run] {chat_id}: {text}")
        return 0

    try:
        response = send(text, token, chat_id)
    except Exception as exc:  # noqa: BLE001
        print(f"не отправлено: {exc}", file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(f"не отправлено: {response}", file=sys.stderr)
        return 1

    print(f"отправлено в {chat_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
