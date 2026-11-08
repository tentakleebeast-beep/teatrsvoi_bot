"""
Страж одного экземпляра `tg-gateway` (Ш-28, docs/infrastructure/35 §5b).

Polling у Telegram: один токен — один поллер. Второй процесс не просто стоит без работы:
он получает `409 Conflict` и «перетягивает» на себя апдейты, поэтому бот мигает,
кнопки не отвечают, формы обрываются — в `docs/infrastructure/35` §5b это описано
как авария, а не как мелочь. launchd/`systemd` держат один экземпляр сами, но ручной
запуск (отладка, `nohup`, старый процесс после `--takeover` без снятия) этому не мешает.

Замок — advisory `flock` на файле: ядро снимает его само при завершении процесса, так
что «залипших» замков не бывает. PID внутри файла нужен только для сообщения человеку.
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

DEFAULT_LOCK = Path.home() / ".local" / "state" / "svoi" / "tg-gateway.lock"

# Открытые файлы замка. Держим ссылку, иначе сборщик мусора закроет описание
# и снимет замок — процесс проработал бы до первого gc.
HELD: list = []


class AlreadyRunning(RuntimeError):
    """Замок занят: поллер уже запущен в другом процессе."""


def lock_path() -> Path:
    """Путь замка: `SVOI_GATEWAY_LOCK`, иначе `~/.local/state/svoi/tg-gateway.lock`."""
    override = os.getenv("SVOI_GATEWAY_LOCK", "").strip()
    return Path(override).expanduser() if override else DEFAULT_LOCK


def owner_pid(path: Path | str | None = None) -> int | None:
    """PID, записанный в файле замка (None, если файла нет или он пуст)."""
    target = Path(path) if path is not None else lock_path()
    try:
        text = target.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def acquire(path: Path | str | None = None) -> Path:
    """Взять замок или бросить `AlreadyRunning`. Возвращает путь замка."""
    target = Path(path) if path is not None else lock_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target, "a+", encoding="utf-8")  # noqa: SIM115 — живёт до конца процесса
    except OSError as exc:
        raise SystemExit(f"tg-gateway: не удалось открыть замок {target}: {exc}") from None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            raise
        holder = owner_pid(target)
        raise AlreadyRunning(
            "tg-gateway уже запущен"
            + (f" (PID {holder})" if holder else "")
            + f": замок {target} занят. Второй поллер получит 409 Conflict и потеряет "
            "сообщения — оставьте один экземпляр "
            "(scripts/install_launchd.py --takeover или launchctl kickstart -k)."
        ) from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    os.fsync(handle.fileno())
    HELD.append(handle)
    return target


def release() -> None:
    """Отпустить замок (нужно тестам; в бою замок снимает ядро при выходе)."""
    while HELD:
        HELD.pop().close()
