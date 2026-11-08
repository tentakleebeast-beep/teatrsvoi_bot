"""Бэкап БД: таймер ежедневный, ротация держит 7 дампов (V-027, docs/infrastructure/110)."""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
UNITS = PROJECT / "deploy" / "systemd"
BACKUP = PROJECT / "scripts" / "backup.sh"


def _fake_docker(tmp_path: Path) -> Path:
    """pg_dump отдаёт «дамп» в stdout, pg_restore --list читает stdin и молчит."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *pg_dump*) echo PGDUMP; exit 0 ;;\n"
        "  *pg_restore*) cat >/dev/null; exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return fake_bin


def _run_backup(tmp_path: Path, backups: Path, **extra_env: str) -> subprocess.CompletedProcess:
    fake_bin = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BACKUP_DIR": str(backups),
        "BACKUP_KEEP": "7",
        "STORAGE_BACKUP": "0",
        "CONTAINER": "fake-postgres",
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(BACKUP)], env=env, capture_output=True, text=True, check=False
    )


def test_юнит_бэкапа_запускает_скрипт_от_пользователя_svoi():
    text = (UNITS / "svoi-backup.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/svoi/scripts/backup.sh" in text
    assert "User=svoi" in text
    assert "EnvironmentFile=/opt/svoi/.env" in text
    assert "Type=oneshot" in text


def test_таймер_ежедневный_в_0330():
    text = (UNITS / "svoi-backup.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 03:30:00" in text
    # Persistent=true: пропущенный из-за выключения запуск догоняется после старта
    assert "Persistent=true" in text
    assert "WantedBy=timers.target" in text


def test_ротация_оставляет_семь_последних_дампов(tmp_path: Path):
    """8 старых дампов + новый прогон → в каталоге ровно 7, самые старые удалены."""
    backups = tmp_path / "backups"
    backups.mkdir()
    old = [
        "svoi_2026-10-20_0330.dump",
        "svoi_2026-10-21_0330.dump",
        "svoi_2026-10-22_0330.dump",
        "svoi_2026-10-23_0330.dump",
        "svoi_2026-10-24_0330.dump",
        "svoi_2026-10-25_0330.dump",
        "svoi_2026-10-26_0330.dump",
        "svoi_2026-10-27_0330.dump",
    ]
    for name in old:
        (backups / name).write_bytes(b"dump")

    result = _run_backup(tmp_path, backups)
    assert result.returncode == 0, result.stderr

    left = sorted(p.name for p in backups.glob("svoi_*.dump"))
    assert len(left) == 7, left
    assert "svoi_2026-10-20_0330.dump" not in left
    assert "svoi_2026-10-21_0330.dump" not in left
    assert "svoi_2026-10-27_0330.dump" in left
    assert "дампов в каталоге: 7 (храним 7)" in result.stdout


def test_ротация_уважает_меньший_предел(tmp_path: Path):
    """BACKUP_KEEP переопределяется: предел не зашит в код."""
    backups = tmp_path / "backups"
    backups.mkdir()
    for name in ("svoi_2026-10-25_0330.dump", "svoi_2026-10-26_0330.dump"):
        (backups / name).write_bytes(b"dump")

    result = _run_backup(tmp_path, backups, BACKUP_KEEP="2")
    assert result.returncode == 0, result.stderr
    assert len(list(backups.glob("svoi_*.dump"))) == 2
    assert not (backups / "svoi_2026-10-25_0330.dump").exists()


def test_старый_mtime_не_удаляет_дамп_из_семёрки(tmp_path: Path):
    """Регрессия ревью: удаление по `-mtime` уносило дамп, который новее всех по имени.

    Семь ежедневных дампов, у самого нового испорчен `mtime` (скопирован/восстановлен
    из архива). Ротация считает имена, поэтому он остаётся, а набор не падает ниже 7.
    """
    backups = tmp_path / "backups"
    backups.mkdir()
    today = date.today()
    names = [
        (today - timedelta(days=offset)).strftime("svoi_%Y-%m-%d_0330.dump")
        for offset in range(6, -1, -1)
    ]
    for name in names:
        (backups / name).write_bytes(b"dump")
    newest = backups / names[-1]
    stale = datetime(today.year - 1, 1, 1).timestamp()
    os.utime(newest, (stale, stale))

    result = _run_backup(tmp_path, backups)
    assert result.returncode == 0, result.stderr

    left = sorted(p.name for p in backups.glob("svoi_*.dump"))
    assert len(left) == 7, left
    assert newest.name in left, "дамп с испорченным mtime удалён, хотя он новее всех по имени"
    # ротация всё равно сработала: удалён самый старый по имени, а не по mtime
    assert names[0] not in left, left
