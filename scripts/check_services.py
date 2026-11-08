#!/usr/bin/env python3
"""
Проверка юнитов планировщика и сервисов (docs/infrastructure/35, C-11).

  python scripts/check_services.py                 # проверить deploy/systemd
  python scripts/check_services.py --dir DIR       # другая папка юнитов
  python scripts/check_services.py --json          # машинный вывод
  python scripts/check_services.py --live          # живые сервисы: /health и процессы

Что проверяется:
  * у каждого `*.service` есть секции [Unit] и [Service], поля ExecStart,
    WorkingDirectory, EnvironmentFile;
  * у каждого `*.timer` есть секция [Timer], поле OnCalendar и парный `*.service`;
  * скрипт из ExecStart существует в репозитории (путь `/opt/svoi/...` переводится
    в корень проекта), python-интерпретатор и модуль `-m` не проверяются;
  * обязательный набор юнитов из REQUIRED присутствует.

Дополнительно `--live` (Ш-27) проверяет не файлы, а живое окружение: `/health`
tool-api отвечает `ok: true`, процесс tg-gateway ровно один. Юниты в порядке, а
сервисы лежат — это тоже провал: именно так начинался каждый шаг из мёртвого API.

С Ш-28 проверяется ещё и замок поллера: процесс, поднятый в обход замка (до правки
или руками из старой инструкции), не остановит второй экземпляр, а Telegram отдаст
апдейты случайному из них. Замок и PID-файл — `~/.local/state/svoi/tg-gateway.lock`
(переопределяется `SVOI_GATEWAY_LOCK`).

Код выхода 1, если есть хоть одна проблема. Проверка статическая: работает и на macOS,
где нет systemd, и на сервере — по тем же файлам репозитория.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEPLOY_PREFIXES = ("/opt/svoi/",)

# Юниты, без которых уведомления и присмотр за сервисом не работают.
REQUIRED = [
    "svoi-tool-api.service",
    "svoi-gateway.service",
    "svoi-notify-plan.service",
    "svoi-notify-plan.timer",
    "svoi-notify-send.service",
    "svoi-notify-send.timer",
    "svoi-feedback-digest.service",
    "svoi-feedback-digest.timer",
    "svoi-feedback-triage.service",
    "svoi-feedback-triage.timer",
    "svoi-invariants.service",
    "svoi-invariants.timer",
    "svoi-watchdog.service",
    "svoi-watchdog.timer",
    "svoi-health.service",
    "svoi-health.timer",
    # t_40ec6e15: домен может быть недоступен из РФ, пока сервер жив, — мерить надо снаружи.
    "svoi-geo-check.service",
    "svoi-geo-check.timer",
    "svoi-backup.service",
    "svoi-backup.timer",
    # t_4aa28fe9: конфликт поллеров виден только в логе — сторож обязателен.
    "svoi-conflicts.service",
    "svoi-conflicts.timer",
]


@dataclass
class Report:
    checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


DEFAULT_API_URL = os.getenv("TOOL_API_URL", "http://127.0.0.1:8000")
GATEWAY_PATTERN = "services/tg-gateway/app/main.py"
GATEWAY_LOCK = Path(
    os.getenv("SVOI_GATEWAY_LOCK", "").strip()
    or Path.home() / ".local" / "state" / "svoi" / "tg-gateway.lock"
)
# Сколько ждать ответа /health: сервис локальный, дольше 3 секунд — уже авария.
HEALTH_TIMEOUT = 3.0


def check_health(api_url: str = DEFAULT_API_URL, timeout: float = HEALTH_TIMEOUT) -> list[str]:
    """`/health` tool-api: должен ответить 200 и `ok: true`."""
    url = api_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return [f"{url}: нет ответа ({exc})"]
    if not isinstance(payload, dict) or not payload.get("ok"):
        return [f"{url}: сервис ответил без ok=true"]
    return []


def service_logs(service: str, logs: Path | None = None) -> list[Path]:
    """Файлы логов сервиса: launchd пишет их в SVOI_LOG_DIR."""
    from scripts import run_service
    directory = logs or run_service.log_dir()
    return sorted(p for p in directory.glob(f"{service}*.log") if p.is_file())


def check_logs(service: str, logs: Path | None = None) -> list[str]:
    """Живой процесс не оставил ни строки в логах — это провал проверки (Ш-39)."""
    files = service_logs(service, logs)
    if not files:
        return []
    if any(path.stat().st_size > 0 for path in files):
        return []
    names = ", ".join(path.name for path in files)
    return [f"{service}: логи {names} пусты при живом процессе — сервис работает, "
            "но следов не оставляет; проверьте stream логирования"]


def gateway_pids() -> list[int]:
    """PID-ы процессов tg-gateway (polling терпит только один)."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", GATEWAY_PATTERN], capture_output=True, text=True, check=False,
        ).stdout
    except OSError:
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]




def lock_owner() -> int | None:
    """PID, записанный в замке поллера (None: файла нет или он пуст)."""
    try:
        text = GATEWAY_LOCK.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def plist_drift() -> list[str]:
    """Агенты launchd указывают на прошлый путь проекта или старый venv (Ш-38).

    Только macOS: на сервере сервисы держит systemd, plist-ов там нет.
    """
    if sys.platform != "darwin":
        return []
    if str(PROJECT) not in sys.path:          # запуск файлом: sys.path[0] = scripts/
        sys.path.insert(0, str(PROJECT))
    from scripts import install_launchd

    return install_launchd.doctor()


def check_live(api_url: str = DEFAULT_API_URL, timeout: float = HEALTH_TIMEOUT) -> list[str]:
    """Живое окружение сервиса: API отвечает, поллер один и держит замок.

    Здесь только то, что одинаково на любой машине и в CI. Состояние launchd
    (установленные plist-ы, их расхождение с шаблоном, логи) — отдельная проверка
    `check_local_agents()`: она говорит про конкретный ноутбук, а не про сервис,
    и в CI на Linux была бы всегда красной (INT-005).
    """
    problems = check_health(api_url, timeout)
    pids = gateway_pids()
    if not pids:
        problems.append("tg-gateway не запущен: апдейты Telegram никто не читает")
        return problems
    if len(pids) > 1:
        problems.append(
            f"tg-gateway: экземпляров {len(pids)} ({', '.join(map(str, pids))}) — "
            "Telegram отдаёт апдейты по одному, остальные получают 409"
        )
        return problems
    if len(pids) == 1:
        owner = lock_owner()
        if owner is None:
            problems.append(
                f"tg-gateway (PID {pids[0]}) запущен без замка {GATEWAY_LOCK}: "
                "процесс поднят до Ш-28 или в обход launchd — второй экземпляр он не "
                "остановит, перезапустите (scripts/install_launchd.py --takeover)"
            )
        elif owner != pids[0]:
            problems.append(
                f"замок {GATEWAY_LOCK} держит PID {owner}, а живой поллер — PID {pids[0]}: "
                "запуск шёл мимо замка, экземпляры могут размножиться"
            )
    return problems


def check_local_agents() -> list[str]:
    """Состояние launchd на этой машине: plist-ы, их дрейф, агенты и логи.

    Только для локального контура macOS: на сервере сервисы держит systemd (логи в
    journald), в CI установленных агентов нет вовсе. Поэтому вызывается из `--live`,
    а не из `check_live()`, — иначе проверка сервиса зависела бы от ноутбука.
    """
    problems = plist_drift()
    problems.extend(check_agents())
    if sys.platform == "darwin":
        problems.extend(check_logs("tool-api"))
        problems.extend(check_logs("gateway"))
    return problems


def repo_path(command: str) -> Path | None:
    """Путь из ExecStart → файл в репозитории (None, если это не файл проекта)."""
    for prefix in DEPLOY_PREFIXES:
        if command.startswith(prefix):
            return PROJECT / command[len(prefix):]
    if command.startswith("/"):
        return None
    return PROJECT / command


def check_unit(path: Path) -> list[str]:
    problems: list[str] = []
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        return [f"{path.name}: не читается как unit-файл: {exc}"]

    timer = path.suffix == ".timer"
    if not parser.has_section("Unit"):
        problems.append(f"{path.name}: нет секции [Unit]")
    if timer:
        if not parser.has_section("Timer"):
            problems.append(f"{path.name}: нет секции [Timer]")
        elif not parser.get("Timer", "OnCalendar", fallback="").strip():
            problems.append(f"{path.name}: в [Timer] нет OnCalendar")
    else:
        if not parser.has_section("Service"):
            problems.append(f"{path.name}: нет секции [Service]")
        else:
            for key in ("ExecStart", "WorkingDirectory", "EnvironmentFile"):
                if not parser.get("Service", key, fallback="").strip():
                    problems.append(f"{path.name}: в [Service] нет {key}")
            exec_start = parser.get("Service", "ExecStart", fallback="").strip()
            tokens = [t for t in exec_start.split() if not t.startswith("-")]
            if len(tokens) >= 2 and tokens[1].endswith(".py"):
                target = repo_path(tokens[1])
                if target is not None and not target.is_file():
                    problems.append(
                        f"{path.name}: ExecStart ссылается на отсутствующий файл "
                        f"{tokens[1]}"
                    )
    return problems


def check_dir(directory: Path) -> Report:
    report = Report()
    if not directory.is_dir():
        report.errors.append(f"нет папки юнитов: {directory}")
        return report

    units = sorted(directory.glob("*.service")) + sorted(directory.glob("*.timer"))
    names = {unit.name for unit in units}
    if not units:
        report.errors.append(f"в {directory} нет unit-файлов")

    for unit in units:
        report.checked.append(unit.name)
        report.errors.extend(check_unit(unit))
        if unit.suffix == ".timer":
            service = unit.with_suffix(".service").name
            if service not in names:
                report.errors.append(
                    f"{unit.name}: нет парного {service} — таймеру нечего запускать"
                )

    for required in REQUIRED:
        if required not in names:
            report.errors.append(f"нет обязательного юнита {required}")
    return report



def check_agents(target_dir=None) -> list[str]:
    """Устаревшие launchd plist-ы: путь проекта или venv из прошлого места (Ш-38).

    plist с прежним путём не падает заметно: launchd рестартит процесс в пустоту,
    а живой сервис просто не поднимается. Поэтому --live предупреждает об этом.
    """
    try:
        from scripts import install_launchd
    except ImportError:                     # запуск из чужого каталога — не повод падать
        return []
    return install_launchd.doctor(target_dir)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка unit-файлов SVOI")
    parser.add_argument(
        "--dir",
        default=os.getenv("SVOI_UNITS_DIR", str(PROJECT / "deploy" / "systemd")),
        help="папка с *.service и *.timer",
    )
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    parser.add_argument("--live", action="store_true",
                        help="проверить живые сервисы: /health и процессы")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    args = parser.parse_args(argv)

    report = check_dir(Path(args.dir))
    if args.live:
        report.checked.append("live: /health, процессы tg-gateway")
        report.errors.extend(check_live(args.api_url))
        if sys.platform == "darwin":
            # launchd-агенты и их логи — свойство этой машины, не сервиса (INT-005)
            report.checked.append("live: launchd-агенты и логи (macOS)")
            report.errors.extend(check_local_agents())
    if args.json:
        print(json.dumps(
            {"ok": report.ok, "checked": report.checked, "errors": report.errors},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"проверено юнитов: {len(report.checked)}")
        for error in report.errors:
            print(f"  ошибка: {error}")
        print("юниты в порядке" if report.ok else f"проблем: {len(report.errors)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
