#!/usr/bin/env python3
"""
Сторож конфликта поллера: `TelegramConflictError` в логе гейтвея → алерт админу.

Зачем: 30.10.2026 на боевом токене работали два поллера, гейтвей выдал 1457 ошибок
`409 Conflict`, бот молчал четыре часа. Ошибка видна только в логе, а в лог никто
не смотрит: нужен сторож, который сам скажет админу «прямо сейчас два процесса
едят один bot token».

Правила сторожа:
  * маркеры — `TelegramConflictError` и `Conflict: terminated by other getUpdates`;
  * один алерт на эпизод: повтор в пределах `--episode-hours` (6 ч) не отправляется,
    иначе лежащий поллер завалит админа сообщениями каждые 5 минут;
  * в сообщении — хост, откуда сигнал (на проде и на маке это разные машины),
    число строк, первая и последняя метка времени и источник;
  * отправка прямая, а не через очередь `notification`: это аварийный сигнал,
    тихие часы и лимит «3 в час» к нему не применяются (как у аварийного резерва
    `scripts/alerts.send_telegram`).

Источники:
  * `--source files` — файлы логов launchd (`~/Library/Logs/svoi/gateway*.log*`);
  * `--source journal` — journald на сервере (`journalctl -u svoi-gateway`);
  * `--source auto` (по умолчанию) — journal, если он есть, иначе файлы.

Окно свежести одинаковое у обоих источников — последние `JOURNAL_MINUTES` минут.
Файлы при этом отбираются по метке времени в строке, а не «последние N строк»:
`gateway.log` не ротируется до 5 МБ (`run_service.rotate_logs`), поэтому в хвосте
месяцами лежат строки старого эпизода, и счёт по строкам давал ложный алерт
(05.11.2026: конфликтов не было с 01.11, а сторож звонил каждые 6 часов). Строка
без метки времени отбрасывается: у неё нет доказательства свежести.

  python scripts/conflict_watch.py                     # посмотреть, не отправляя
  python scripts/conflict_watch.py --apply             # отправить админу, если есть что
  python scripts/conflict_watch.py --apply --json      # то же, машинный вывод
  python scripts/conflict_watch.py --log-dir /tmp/logs --source files --apply

Код выхода: 0 — конфликта нет или алерт отправлен; 1 — конфликт есть, но алерт не
ушёл (без `--apply` или Telegram отказал). Тест: tests/unit/test_conflict_watch.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT / "scripts") not in sys.path:      # запуск файлом: python scripts/conflict_watch.py
    sys.path.insert(0, str(PROJECT / "scripts"))

MARKERS = (
    "TelegramConflictError",
    "Conflict: terminated by other getUpdates",
)
DEFAULT_UNIT = "svoi-gateway"
DEFAULT_FILES = ("gateway.log", "gateway.err.log", "gateway.log.1", "gateway.err.log.1")
EPISODE_HOURS = 6.0
JOURNAL_MINUTES = 30

# Метка времени в строке лога: гейтвей пишет `%(asctime)s` («2026-11-01 10:00:00,123»),
# journald с `-o short-iso` — ISO-8601 с зоной.
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# Сколько байт хвоста читать, чтобы понять возраст файла, не читая его целиком.
TAIL_PROBE_BYTES = 64 * 1024
TITLE = "Конфликт поллера Telegram"


def log_dir() -> Path:
    """Каталог логов launchd (SVOI_LOG_DIR переопределяет, как в run_service)."""
    override = os.getenv("SVOI_LOG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Logs" / "svoi"


def state_path() -> Path:
    """Файл состояния сторожа: помнит, когда об эпизоде уже сообщили."""
    override = os.getenv("SVOI_CONFLICT_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "svoi" / "conflict-watch.json"


def is_conflict(line: str) -> bool:
    """Есть ли в строке лога маркер конфликта поллеров."""
    return any(marker in line for marker in MARKERS)


def conflicts(lines: list[str]) -> list[str]:
    """Только строки конфликта, в порядке появления."""
    hits: list[str] = [line for line in lines if is_conflict(line)]
    return hits


def stamp(line: str) -> str:
    """Метка времени из строки лога: «2026-11-01 10:00:00» (пустая, если нет)."""
    found = STAMP.search(line)
    return f"{found.group(1)} {found.group(2)}" if found else ""


def stamp_moment(line: str) -> datetime | None:
    """Метка строки как локальное «настенное» время (`%(asctime)s` пишет без зоны)."""
    found = STAMP.search(line)
    if not found:
        return None
    try:
        return datetime.strptime(f"{found.group(1)} {found.group(2)}", STAMP_FORMAT)
    except ValueError:      # кривая метка (31.02, 25:00) — то же, что метки нет
        return None


def local_wall(now: datetime) -> datetime:
    """`now` в локальном настенном времени — в той же шкале, что метки в файле."""
    return now.astimezone().replace(tzinfo=None) if now.tzinfo else now


def fresh(lines: list[str], now: datetime, minutes: int = JOURNAL_MINUTES) -> list[str]:
    """Строки, чья метка попадает в последние `minutes` минут до `now`.

    Строка без метки отбрасывается: свежесть доказать нечем. Метка из будущего —
    тоже (это опечатка или чужой лог, а не сигнал «прямо сейчас»).
    """
    wall = local_wall(now)
    edge = wall - timedelta(minutes=minutes)
    keep: list[str] = []
    for line in lines:
        moment = stamp_moment(line)
        if moment is None or not edge <= moment <= wall:
            continue
        keep.append(line)
    return keep


def newest_moment(lines: list[str]) -> datetime | None:
    """Самая поздняя метка в наборе строк (None — меток нет)."""
    moments = [moment for moment in map(stamp_moment, lines) if moment is not None]
    return max(moments) if moments else None


def tail_text(path: Path, size: int = TAIL_PROBE_BYTES) -> str:
    """Последние `size` байт файла: возраст лога виден по хвосту, без чтения всего."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        length = handle.tell()
        handle.seek(max(0, length - size))
        return handle.read().decode("utf-8", errors="replace")


def read_files(paths: list[Path], now: datetime | None = None,
               minutes: int = JOURNAL_MINUTES) -> list[str]:
    """Свежие строки логов; отсутствующие файлы пропускаются (ротация, сервис не работал).

    Два слоя защиты от старых строк: сначала смотрим хвост файла — если в нём нет
    ни одной метки моложе окна, файл не читается совсем (быстрый выход), затем
    каждая строка фильтруется по собственной метке.
    """
    now = now or datetime.now(UTC)
    edge = local_wall(now) - timedelta(minutes=minutes)
    lines: list[str] = []
    for path in paths:
        try:
            newest = newest_moment(tail_text(path).splitlines())
        except OSError:
            continue
        if newest is None or newest < edge:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines.extend(fresh(text.splitlines(), now, minutes))
    return lines


def hostname() -> str:
    """Хост, с которого уходит алерт: на проде и на маке живут разные поллеры."""
    try:
        return socket.gethostname().split(".")[0] or "unknown"
    except OSError:
        return "unknown"


def read_journal(unit: str = DEFAULT_UNIT, minutes: int = JOURNAL_MINUTES,
                 run=subprocess.run) -> list[str]:
    """Строки журнала юнита за последние `minutes` минут (сервер)."""
    argv = ["journalctl", "-u", unit, "--since", f"-{int(minutes)}min",
            "-o", "short-iso", "--no-pager", "-n", "500"]
    try:
        done = run(argv, capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return (done.stdout or "").splitlines()


def has_journal() -> bool:
    """Есть ли journalctl на этой машине (на маке его нет — берём файлы)."""
    return shutil.which("journalctl") is not None


def collect(source: str = "auto", logs: Path | None = None,
            files: tuple[str, ...] | None = None,
            unit: str = DEFAULT_UNIT, journal=read_journal,
            now: datetime | None = None) -> tuple[list[str], str]:
    """Строки источника и его описание: (строки, откуда читали)."""
    if source == "journal" or (source == "auto" and has_journal()):
        return journal(unit), f"journalctl -u {unit}"
    directory = logs or log_dir()
    paths = [directory / name for name in (files or DEFAULT_FILES)]
    return read_files(paths, now=now), str(directory)


def load_state(path: Path) -> dict:
    """Состояние сторожа; битый или отсутствующий файл — пустое состояние."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict) -> None:
    """Записать состояние; ошибка файловой системы не мешает алерту."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def already_alerted(state: dict, now: datetime, episode_hours: float = EPISODE_HOURS) -> str:
    """Когда об эпизоде уже сообщали (пустая строка, если эпизод новый)."""
    raw = str(state.get("alerted_at") or "")
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if now - moment < timedelta(hours=episode_hours):
        return moment.astimezone().strftime("%d.%m %H:%M")
    return ""


def build_message(hits: list[str], source: str, host: str | None = None) -> str:
    """Текст алерта: что случилось, сколько, где и что делать."""
    host = host or hostname()
    first = next((stamp(line) for line in hits if stamp(line)), "")
    last = next((stamp(line) for line in reversed(hits) if stamp(line)), "")
    window = ""
    if first and last:
        window = f" ({first} — {last[11:]})" if first[:10] == last[:10] else f" ({first} — {last})"
    return "\n".join([
        f"{TITLE}: {host}",
        "· на один bot token приходится больше одного поллера: 409 Conflict, апдейты теряются",
        f"· строк с конфликтом: {len(hits)}{window}",
        f"· источник: {source}",
        "· что сделать: оставить одного поллера (scripts/check_services.py --live),",
        "  лишний процесс снять, локальные launchd-агенты держать на dev-боте",
    ])


def deliver(text: str) -> bool:
    """Прямая отправка админу (аварийный канал, мимо тихих часов)."""
    from alerts import send_telegram  # noqa: PLC0415 — модуль скриптов, путь добавлен выше

    return send_telegram(text)


def load_environment(environment: str | None = None) -> str:
    """Окружение сторожа: dev — из `.env.dev` (dev-бот), иначе — из того, что дали.

    На сервере токен и `ADMIN_CHAT_ID` приходят из `EnvironmentFile=/opt/svoi/.env`,
    поэтому `--env prod` ничего не читает. На маке launchd не умеет EnvironmentFile
    без секрета в plist, поэтому dev-ветка берёт окружение так же, как
    `scripts/run_service.py`: `.env.dev` + guard «нет DEV_BOT_TOKEN → отказ».
    """
    if environment != "dev":
        return ""
    from run_service import build_env, dev_env, env_file_for, load_env  # noqa: PLC0415

    path = env_file_for("dev")
    values = load_env(path)
    env = build_env(environment="dev")
    env.update(dev_env(values))          # DevEnvNotConfigured — текст уже человеческий
    os.environ.update(env)
    return str(path)


def check(source: str = "auto", logs: Path | None = None, files: tuple[str, ...] | None = None,
          unit: str = DEFAULT_UNIT, state: Path | None = None, now: datetime | None = None,
          episode_hours: float = EPISODE_HOURS, apply: bool = False,
          journal=read_journal, send=deliver, host: str | None = None) -> dict:
    """Найти конфликт, решить, нужен ли алерт, и (по `apply`) отправить его."""
    now = now or datetime.now(UTC)
    lines, where = collect(source, logs, files, unit, journal=journal, now=now)
    hits = conflicts(lines)
    result: dict = {"found": len(hits), "source": where, "host": host or hostname(),
                    "alerted": False, "skipped": "", "text": ""}
    if not hits:
        return result
    path = state if state is not None else state_path()
    repeated = already_alerted(load_state(path), now, episode_hours)
    if repeated:
        result["skipped"] = f"об эпизоде уже сообщали {repeated}"
        return result
    text = build_message(hits, where, result["host"])
    result["text"] = text
    if not apply:
        result["skipped"] = "без --apply"
        return result
    result["alerted"] = bool(send(text))
    if result["alerted"]:
        save_state(path, {"alerted_at": now.isoformat(), "host": result["host"],
                          "lines": len(hits), "source": where})
    else:
        result["skipped"] = "Telegram не принял сообщение"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сторож конфликта поллера Telegram")
    parser.add_argument("--source", choices=("auto", "files", "journal"), default="auto")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--unit", default=DEFAULT_UNIT, help="юнит journald (сервер)")
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--episode-hours", type=float, default=EPISODE_HOURS)
    parser.add_argument("--apply", action="store_true", help="отправить алерт админу")
    parser.add_argument("--env", choices=("dev", "prod"), default=None,
                        help="dev — взять бота и админа из .env.dev (мак); "
                             "prod — окружение уже задано юнитом systemd")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.env == "dev":
        from run_service import DevEnvNotConfigured  # noqa: PLC0415

        try:
            path = load_environment("dev")
        except DevEnvNotConfigured as exc:
            print(f"среда разработки не настроена: {exc}", file=sys.stderr)
            return 2
        print(f"окружение: {path} (dev-бот)")

    result = check(source=args.source, logs=args.log_dir, unit=args.unit,
                   state=args.state, episode_hours=args.episode_hours, apply=args.apply,
                   send=deliver)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["found"]:
        print(result["text"])
        verdict = "отправлен" if result["alerted"] else "не отправлен — " + (result["skipped"] or "?")
        print(f"\n[алерт: {verdict}]")
        if not result["alerted"] and result["skipped"] == "Telegram не принял сообщение":
            print("проверьте ADMIN_CHAT_ID и что админ хоть раз написал боту /start: "
                  "без этого Telegram не даёт боту писать первым")
    else:
        print(f"конфликта поллеров нет (источник: {result['source']})")
    return 0 if not result["found"] or result["alerted"] else 1


if __name__ == "__main__":
    sys.exit(main())
