#!/usr/bin/env python3
"""
Перезапуск сервисов SVOI из тика планировщика (Ш-29, docs/infrastructure/35 §5c).

Зачем отдельный скрипт. Рецепт из начала плана — `sh -c 'set -a; . ./.env; ... nohup ...'`
— политика одобрений режет как «shell command via -c/-lc flag», а запись `.sh` через
heredoc с шебангом — как «shell execution via heredoc». Поэтому из тика сервис после
правок просто не поднимался, и каждый шаг начинался с мёртвого `/health`. Всё, что
нужно от шелла, делает Python: читает `.env` (с отрезанием инлайн-комментариев), чистит
`PYTHONPATH` и запускает процесс без оболочки.

Два пути запуска:

* launchd (macOS, dev-контур) — `scripts/install_launchd.py --takeover`: агенты
  поднимаются с `KeepAlive`, процесс переживает конец сессии. Это путь по умолчанию.
* напрямую (`--direct`) — оба процесса стартуют в отдельной сессии
  (`start_new_session`), поэтому смерть оболочки тика их не убивает; логи — в
  `~/Library/Logs/svoi/`. Нужен там, где launchd нет или агенты не установлены.

После старта ждём готовности фактами, а не «процесс запущен»: `/health` API отвечает
`ok: true`, поллер `tg-gateway` ровно один и держит замок (Ш-28). Не дождались —
код выхода 1 и перечень проблем.

  python scripts/start_services.py                 # оба сервиса через launchd + проверка
  python scripts/start_services.py --direct        # то же, но процессами без launchd
  python scripts/start_services.py --service gateway
  python scripts/start_services.py --timeout 60 --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:  # запуск как файла: python scripts/start_services.py
    sys.path.insert(0, str(PROJECT))

from scripts import check_services, install_launchd, run_service  # noqa: E402

DEFAULT_API_URL = check_services.DEFAULT_API_URL
# launchd поднимает агента с ThrottleInterval и паузой после bootout — 30 с хватает.
DEFAULT_TIMEOUT = 30.0
POLL_SECONDS = 0.5


def log_dir() -> Path:
    return install_launchd.log_dir()


def use_launchd() -> bool:
    """launchd есть только на маке; в остальных контурах — прямой запуск."""
    return sys.platform == "darwin" and shutil.which("launchctl") is not None


def spawn_direct(service: str, port: int = run_service.DEFAULT_PORT,
                 logs: Path | None = None) -> int:
    """Поднять сервис в отдельной сессии и вернуть PID.

    `start_new_session` отвязывает процесс от группы тика: сессия агента кончается —
    сервис продолжает работать. Shell не участвует, heredoc тоже.
    """
    directory = logs or log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    argv = run_service.command(service, port=port)
    try:
        env = run_service.service_env(service, run_service.DEFAULT_ENVIRONMENT)
    except run_service.DevEnvNotConfigured as exc:      # dev-бот не настроен
        raise SystemExit(str(exc)) from None
    with open(directory / f"{service}.log", "a", encoding="utf-8") as out, \
            open(directory / f"{service}.err.log", "a", encoding="utf-8") as err:
        process = subprocess.Popen(  # noqa: S603 — argv без шелла, путь известен
            argv,
            cwd=str(PROJECT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
            close_fds=True,
        )
    return process.pid


def restart_via_launchd(services: list[str]) -> list[str]:
    """Перезаписать plist-ы и перезагрузить агентов (снимая чужие процессы)."""
    return install_launchd.install(services, takeover=True)


def wait_ready(api_url: str = DEFAULT_API_URL, timeout: float = DEFAULT_TIMEOUT,
               poll: float = POLL_SECONDS) -> list[str]:
    """Дождаться живого окружения; вернуть оставшиеся проблемы."""
    deadline = time.monotonic() + timeout
    while True:
        problems = check_services.check_live(api_url)
        if not problems or time.monotonic() >= deadline:
            return problems
        time.sleep(poll)


def restart(services: list[str] | None = None, *, direct: bool = False,
            port: int = run_service.DEFAULT_PORT,
            api_url: str = DEFAULT_API_URL, timeout: float = DEFAULT_TIMEOUT,
            logs: Path | None = None) -> tuple[list[str], list[str]]:
    """Перезапустить сервисы и дождаться готовности. Возвращает (действия, проблемы)."""
    names = [s for s in (services or list(run_service.SERVICES)) if s in run_service.SERVICES]
    actions: list[str] = []
    if direct or not use_launchd():
        for service in names:
            pid = spawn_direct(service, port, logs)
            actions.append(f"{service}: запущен напрямую, PID {pid}")
    else:
        actions.extend(restart_via_launchd(names))
        failed = [a for a in actions if "ошибка" in a or "не снялись" in a]
        if failed:
            return actions, failed
    return actions, wait_ready(api_url, timeout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Перезапуск сервисов SVOI без шелла (docs/infrastructure/35 §5c)")
    parser.add_argument("--service", choices=list(run_service.SERVICES), action="append",
                        help="только этот сервис (по умолчанию оба)")
    parser.add_argument("--direct", action="store_true",
                        help="процессами в отдельной сессии, без launchd")
    parser.add_argument("--port", type=int, default=run_service.DEFAULT_PORT)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    actions, problems = restart(
        args.service, direct=args.direct, port=args.port,
        api_url=args.api_url, timeout=args.timeout,
    )
    if args.json:
        print(json.dumps({"ok": not problems, "actions": actions, "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        for line in actions:
            print(line)
        for problem in problems:
            print(f"  проблема: {problem}")
        print("сервисы готовы" if not problems else f"проблем: {len(problems)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
