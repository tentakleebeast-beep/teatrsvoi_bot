"""
Версия живой сборки и время старта процесса (FR-H12, L-005).

Зачем: 28.10.2026 три дефекта смоука были записаны на старую сборку, потому что
никто не сверял живой процесс с HEAD. `/health` теперь отдаёт `git_sha` и `uptime`,
а `scripts/health_agent.py` сравнивает sha с HEAD и пишет админу в очередь.

Чтение sha без вызова git: процесс может работать в контейнере без git и без
исходников. Порядок: переменная окружения GIT_SHA → `.git/HEAD` → `.git/packed-refs`
→ "unknown". Ошибки чтения не роняют сервис: версия — диагностика, не логика.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

UNKNOWN = "unknown"


def _read_ref(git_dir: Path, ref: str) -> str | None:
    """refs/heads/main → sha из файла или из packed-refs."""
    plain = git_dir / ref
    if plain.is_file():
        value = plain.read_text(encoding="utf-8").strip()
        return value or None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("^"):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def read_git_sha(root: str | Path, env: dict | None = None) -> str:
    """Короткий sha (12 знаков) сборки, которая лежит в `root`."""
    env = os.environ if env is None else env
    forced = (env.get("GIT_SHA") or "").strip()
    if forced:
        return forced[:12]
    git_dir = Path(root) / ".git"
    if git_dir.is_file():                      # worktree: gitdir: /path/to/.git/worktrees/x
        marker = git_dir.read_text(encoding="utf-8").strip()
        if marker.startswith("gitdir:"):
            git_dir = Path(marker.split(":", 1)[1].strip())
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return UNKNOWN
    if head.startswith("ref:"):
        sha = _read_ref(git_dir, head.split(":", 1)[1].strip())
    else:
        sha = head or None
    if not sha or len(sha) < 7 or any(c not in "0123456789abcdef" for c in sha.lower()):
        return UNKNOWN
    return sha[:12]


def project_root() -> Path:
    """services/tool-api/app/version.py → корень репозитория."""
    return Path(__file__).resolve().parents[3]


def uptime_seconds(started: datetime, moment: datetime) -> int:
    """Секунды с запуска; отрицательное время (часы назад) считаем нулём."""
    return max(0, int((moment - started).total_seconds()))


def format_uptime(seconds: int) -> str:
    """Для человека: 3д 4ч 5м, без секунд, если процесс живёт больше минуты."""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}д {hours}ч {minutes}м"
    if hours:
        return f"{hours}ч {minutes}м"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"
