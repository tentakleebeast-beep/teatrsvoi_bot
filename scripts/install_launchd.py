#!/usr/bin/env python3
"""
Устойчивый запуск сервисов на маке через launchd (Ш-27, docs/infrastructure/35 §5c).

Проблема: `tool-api` и `tg-gateway`, поднятые `nohup`-ом внутри cron-тика, умирают
вместе с сессией — каждый следующий шаг начинался с мёртвого `/health`, уведомления
не уходили. launchd (PID 1) держит процесс сам: `RunAtLoad` + `KeepAlive`, падение
поднимается через `ThrottleInterval`, конец агентской сессии ни при чём.

  python scripts/install_launchd.py --dry-run     # показать, что будет записано
  python scripts/install_launchd.py --takeover    # поставить и снять чужие процессы
  python scripts/install_launchd.py --status
  python scripts/install_launchd.py --uninstall

Шаблоны лежат в `deploy/launchd/*.plist.template`; подстановки — `@PROJECT@`,
`@PYTHON@`, `@LOG_DIR@`, `@ENV@`. Путь проекта содержит пробел, поэтому в plist он
попадает одной строкой аргумента, а не через shell.

Агенты двух родов: сервисы (`KeepAlive` — процесс живёт постоянно) и периодические
(`StartInterval` — короткий прогон, сейчас сторож 409 `com.svoi.conflicts`).

Про `bootstrap`: он асинхронен относительно `bootout`. Сразу после снятия агента
launchctl отвечает `Bootstrap failed: 5: Input/output error`, и сервис остаётся
лежать — проверено на живом окружении. Поэтому загрузка идёт с паузой, тремя
попытками и проверкой, что процесс действительно появился.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT / "deploy" / "launchd"
# Агент на маке — всегда среда разработки: её файл `.env.dev` и dev-бот
# (карта t_4aa28fe9, docs/infrastructure/08 §1a). Прод-поллер живёт на сервере.
DEFAULT_ENVIRONMENT = "dev"
LABELS = {"tool-api": "com.svoi.tool-api", "gateway": "com.svoi.gateway"}
# Периодические агенты (StartInterval): короткий прогон скрипта, а не демон под
# KeepAlive. Сейчас один — сторож 409: без него на маке алерт не уходит вовсе,
# а конфликт поллеров в логе этого мака уже случался (t_4aa28fe9).
PERIODIC = {"conflicts": "com.svoi.conflicts"}
ALL_LABELS = {**LABELS, **PERIODIC}
# Шаблон командной строки для поиска процессов сервиса (в т.ч. поднятых мимо launchd).
PATTERNS = {
    "tool-api": "uvicorn app.main:app --app-dir services/tool-api",
    "gateway": "services/tg-gateway/app/main.py",
    "conflicts": "scripts/conflict_watch.py",
}
# Пауза между bootout и bootstrap и между попытками загрузки.
SETTLE_SECONDS = 1.5
BOOTSTRAP_ATTEMPTS = 3
START_TIMEOUT = 15.0


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "svoi"


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def domain() -> str:
    return f"gui/{os.getuid()}"


def render(text: str, project: Path = PROJECT, python: str | None = None,
           logs: Path | None = None, environment: str = DEFAULT_ENVIRONMENT) -> str:
    return (text
            .replace("@PROJECT@", str(project))
            .replace("@PYTHON@", python or sys.executable)
            .replace("@LOG_DIR@", str(logs or log_dir()))
            .replace("@ENV@", environment))


def plists(project: Path = PROJECT, python: str | None = None,
           logs: Path | None = None,
           environment: str = DEFAULT_ENVIRONMENT) -> dict[str, str]:
    """Отрендеренные plist-ы по имени сервиса."""
    result: dict[str, str] = {}
    for service in ALL_LABELS:
        template = TEMPLATES / f"{ALL_LABELS[service]}.plist.template"
        result[service] = render(template.read_text(encoding="utf-8"), project, python,
                                 logs, environment)
    return result


def processes(service: str) -> list[int]:
    """PID-ы процессов сервиса (и живых под launchd, и запущенных руками)."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", PATTERNS[service]], capture_output=True, text=True, check=False,
        ).stdout
    except OSError:
        return []
    return [int(line) for line in (out or "").split() if line.strip().isdigit()]


def is_loaded(service: str) -> bool:
    return subprocess.run(["launchctl", "print", f"{domain()}/{ALL_LABELS[service]}"],
                          capture_output=True, check=False).returncode == 0


def stop_agent(service: str) -> None:
    # ALL_LABELS, а не LABELS: у периодического сторожа (conflicts) нет записи в LABELS,
    # и `bootout` по нему — единственный способ снять загруженный агент перед переустановкой.
    subprocess.run(["launchctl", "bootout", f"{domain()}/{ALL_LABELS[service]}"],
                   capture_output=True, check=False)


def wait_running(service: str, timeout: float = START_TIMEOUT) -> bool:
    """Дождаться живого процесса после загрузки агента."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if processes(service):
            return True
        time.sleep(0.5)
    return bool(processes(service))


def load_agent(service: str, destination: Path) -> str:
    """Загрузить агента: пауза после bootout, три попытки, проверка процесса."""
    reason = ""
    for attempt in range(BOOTSTRAP_ATTEMPTS):
        if attempt:
            time.sleep(SETTLE_SECONDS)
        done = subprocess.run(["launchctl", "bootstrap", domain(), str(destination)],
                              capture_output=True, text=True, check=False)
        if done.returncode == 0 and wait_running(service):
            return f"{service}: bootstrap ok"
        reason = (done.stderr.strip() or f"код {done.returncode}").splitlines()[0]
    return f"{service}: bootstrap ошибка: {reason}"


def load_periodic(service: str, destination: Path) -> str:
    """Загрузить периодического агента: процесс живёт секунды, ждать его нечего."""
    done = subprocess.run(["launchctl", "bootstrap", domain(), str(destination)],
                          capture_output=True, text=True, check=False)
    if done.returncode == 0:
        return f"{service}: bootstrap ok (каждые 300 с)"
    reason = (done.stderr.strip() or f"код {done.returncode}").splitlines()[0]
    return f"{service}: bootstrap ошибка: {reason}"


def install(services: list[str] | None = None, dry_run: bool = False,
            takeover: bool = False, project: Path = PROJECT,
            target_dir: Path | None = None, python: str | None = None,
            logs: Path | None = None,
            environment: str = DEFAULT_ENVIRONMENT) -> list[str]:
    """Записать plist-ы и загрузить агентов. Возвращает список действий."""
    actions: list[str] = []
    target = target_dir or agents_dir()
    rendered = plists(project, python, logs, environment)
    names = [s for s in (services or list(ALL_LABELS)) if s in ALL_LABELS]

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        (logs or log_dir()).mkdir(parents=True, exist_ok=True)

    for service in names:
        destination = target / f"{ALL_LABELS[service]}.plist"
        loaded = is_loaded(service)
        periodic = service in PERIODIC
        # Процесс при незагруженном агенте — чужой: поднят руками или из тика.
        # У периодического агента прогон длится секунды, и это норма: его не трогаем.
        foreign = [] if periodic else processes(service)
        if foreign and not loaded and not takeover and not dry_run:
            actions.append(
                f"{service}: уже запущен вне launchd ({foreign}) — "
                "второй экземпляр даст 409, повтори с --takeover"
            )
            continue
        if dry_run:
            actions.append(f"{service}: записал бы {destination}")
            if foreign and not loaded:
                actions.append(f"{service}: занят процессами {foreign} (нужен --takeover)")
            continue

        plistlib.loads(rendered[service].encode("utf-8"))  # падаем до записи, если XML битый
        destination.write_text(rendered[service], encoding="utf-8")
        actions.append(f"{service}: {destination}")

        if loaded or foreign:
            stop_agent(service)
            time.sleep(SETTLE_SECONDS)
        if periodic:
            actions.append(load_periodic(service, destination))
            continue
        leftover = processes(service)
        if leftover:
            if not takeover:
                actions.append(
                    f"{service}: процессы {leftover} не снялись, нужен --takeover"
                )
                continue
            for pid in leftover:
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    continue
            actions.append(f"{service}: сняты процессы {leftover}")
            time.sleep(SETTLE_SECONDS)

        actions.append(load_agent(service, destination))
    return actions


def uninstall(target_dir: Path | None = None) -> list[str]:
    actions: list[str] = []
    target = target_dir or agents_dir()
    for service, label in ALL_LABELS.items():
        stop_agent(service)
        path = target / f"{label}.plist"
        if path.exists():
            path.unlink()
        actions.append(f"{service}: снят {label}")
    return actions


def status() -> list[str]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=False).stdout
    found = [line for line in out.splitlines() if "com.svoi" in line]
    return found or ["агенты com.svoi не загружены"]



def doctor(target_dir: Path | None = None, project: Path = PROJECT,
           python: str | None = None, logs: Path | None = None,
           environment: str = DEFAULT_ENVIRONMENT) -> list[str]:
    """Сравнить установленные plist-ы с тем, что дал бы рендер сейчас (Ш-38).

    Агент с путём прошлого места проекта или прошлого venv выглядит живым, но
    `KeepAlive` молча рестартит падение в пустоту: человек открывает лог, видит
    пустоту и решает, что сервис мёртв. Возвращает список расхождений; пустой
    список — всё совпадает с текущими путями и шаблонами.
    """
    target = target_dir or agents_dir()
    rendered = plists(project, python, logs, environment)
    keys = ("ProgramArguments", "WorkingDirectory", "EnvironmentVariables",
            "StandardOutPath", "StandardErrorPath", "StartInterval")
    problems: list[str] = []
    for service, label in ALL_LABELS.items():
        destination = target / f"{label}.plist"
        if not destination.is_file():
            problems.append(
                f"{service}: нет {destination} — агент не поднимется после входа; "
                f"поставь: scripts/install_launchd.py --takeover")
            continue
        try:
            installed = plistlib.loads(destination.read_bytes())
        except Exception as exc:                   # битый XML — тоже расхождение
            problems.append(f"{service}: {destination} не читается как plist: {exc}")
            continue
        expected = plistlib.loads(rendered[service].encode("utf-8"))
        diff = [key for key in keys if installed.get(key) != expected.get(key)]
        if diff:
            recorded = installed.get("WorkingDirectory") or "?"
            problems.append(
                f"{service}: {destination} расходится с шаблоном {diff}; "
                f"в plist путь {recorded}, сейчас {project} — "
                f"переустанови: scripts/install_launchd.py --takeover")
    return problems

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="launchd-агенты сервисов SVOI")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--takeover", action="store_true",
                        help="снять процессы, поднятые мимо launchd")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--doctor", action="store_true",
                        help="сравнить установленные plist-ы с текущими путями")
    parser.add_argument("--service", choices=list(ALL_LABELS), action="append")
    parser.add_argument("--target-dir", type=Path, default=None)
    parser.add_argument("--env", dest="environment", choices=("dev", "prod"),
                        default=DEFAULT_ENVIRONMENT,
                        help="среда в plist (на маке — только dev, t_4aa28fe9)")
    args = parser.parse_args(argv)

    if args.status:
        for line in status():
            print(line)
        return 0
    if args.doctor:
        problems = doctor(args.target_dir, environment=args.environment)
        for line in problems:
            print(line)
        if not problems:
            print("агенты launchd соответствуют шаблонам")
        return 1 if problems else 0
    if args.uninstall:
        for line in uninstall(args.target_dir):
            print(line)
        return 0

    actions = install(args.service, dry_run=args.dry_run, takeover=args.takeover,
                      target_dir=args.target_dir, environment=args.environment)
    for line in actions:
        print(line)
    if any("ошибка" in line or "не снялись" in line for line in actions):
        return 1
    if not args.dry_run:
        print("\nагенты загружены; проверка: python scripts/check_services.py --live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
