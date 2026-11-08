"""Тесты защиты основной ветки: `.githooks/pre-commit` и `.githooks/pre-push`.

Правило docs/development/26 §7a п.9: воркер не коммитит и не пушит в `main` — её
собирает `worker-review` (§7 п.8). Контекст воркера определяется переменными Hermes
(`HERMES_KANBAN_TASK`, `HERMES_PROFILE=worker-*`); у человека их нет, и ему хуки
ничего не запрещают. Профиль `worker-review` — исключение: он и собирает `main`,
поэтому проверяется до общего `worker-*` и перебивает `HERMES_KANBAN_TASK`.

Тесты герметичны: временный git-репозиторий и bare-репозиторий в `tmp_path`,
копии хуков, глобальная git-конфигурация отрезана, Postgres не нужен
(`SVOI_INVARIANTS_SCRIPT` — заглушка), ruff подменяется абсолютным путём из venv.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".githooks"
RUFF = ROOT / ".venv" / "bin" / "ruff"


def _run(repo: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _head(repo: Path, ref: str = "HEAD", env: dict[str, str] | None = None) -> str:
    env = env or _base_env(repo.parent)
    return _run(repo, "rev-parse", ref, env=env).stdout.strip()


def _base_env(tmp_path: Path, *, worker: bool = False) -> dict[str, str]:
    """Окружение git без чужой конфигурации и без переменных Hermes по умолчанию."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    invariants = tmp_path / "ok_invariants.py"
    invariants.write_text('print("Все проверки пройдены.")\n', encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
        "SVOI_INVARIANTS_SCRIPT": str(invariants),
    }
    if RUFF.is_file():
        env["SVOI_RUFF"] = str(RUFF)
    if worker:
        env["HERMES_KANBAN_TASK"] = "1"
        env["HERMES_PROFILE"] = "worker-feature"
    return env


def _commit(repo: Path, env: dict[str, str], message: str = "docs: правка") -> subprocess.CompletedProcess[str]:
    (repo / "README.md").write_text(f"# демо\n\n{message}\n", encoding="utf-8")
    _run(repo, "add", "README.md", env=env)
    return _run(repo, "commit", "-q", "-m", message, env=env)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Временный репозиторий на ветке main с установленными хуками."""
    path = tmp_path / "repo"
    path.mkdir()
    env = _base_env(tmp_path)
    _run(path, "init", "-q", env=env)
    _run(path, "symbolic-ref", "HEAD", "refs/heads/main", env=env)
    _run(path, "config", "user.email", "hook@test.local", env=env)
    _run(path, "config", "user.name", "Hook Test", env=env)
    _run(path, "config", "core.hooksPath", ".githooks", env=env)

    hooks = path / ".githooks"
    hooks.mkdir()
    for name in ("pre-commit", "pre-push"):
        shutil.copy2(HOOKS / name, hooks / name)
        (hooks / name).chmod(0o755)
    assert _run(path, "config", "--get", "core.hooksPath", env=env).stdout.strip() == ".githooks"

    (path / "README.md").write_text("# демо\n", encoding="utf-8")
    _run(path, "add", "README.md", env=env)
    _run(path, "commit", "-q", "--no-verify", "-m", "init", env=env)
    assert _run(path, "symbolic-ref", "--short", "HEAD", env=env).stdout.strip() == "main"
    return path


@pytest.fixture()
def remote(repo: Path, tmp_path: Path) -> Path:
    """Bare-репозиторий как origin; main уже опубликован."""
    bare = tmp_path / "remote.git"
    env = _base_env(tmp_path)
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _run(repo, "remote", "add", "origin", str(bare), env=env)
    pushed = _run(repo, "push", "-q", "origin", "main", env=env)
    assert pushed.returncode == 0, pushed.stderr
    return bare


# --- pre-commit ----------------------------------------------------------------


def test_worker_commit_to_main_blocked(repo: Path, tmp_path: Path) -> None:
    """S-1: воркер с HERMES_KANBAN_TASK=1 не коммитит в main — код 1 и подсказка ветки."""
    env = _base_env(tmp_path, worker=True)
    before = _head(repo, env=env)

    result = _commit(repo, env)

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert _head(repo, env=env) == before
    assert "воркер не коммитит в основную ветку" in output
    assert "wt/1" in output
    assert "main" in output


def test_worker_profile_alone_blocks_commit_to_main(repo: Path, tmp_path: Path) -> None:
    """S-1: одного HERMES_PROFILE=worker-* достаточно, чтобы защита сработала."""
    env = _base_env(tmp_path)
    env["HERMES_PROFILE"] = "worker-feature"
    before = _head(repo, env=env)

    result = _commit(repo, env)

    assert result.returncode == 1, result.stdout + result.stderr
    assert _head(repo, env=env) == before


def test_human_commit_to_main_allowed(repo: Path, tmp_path: Path) -> None:
    """S-1: без переменных Hermes коммит в main проходит как раньше."""
    env = _base_env(tmp_path)
    before = _head(repo, env=env)

    result = _commit(repo, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo, env=env) != before


def test_worker_commit_in_task_branch_allowed(repo: Path, tmp_path: Path) -> None:
    """S-1: в ветке wt/<task-id> воркер коммитит — регрессия по хуку из docs/development/26."""
    env = _base_env(tmp_path, worker=True)
    _run(repo, "switch", "-q", "-c", "wt/1", env=env)
    before = _head(repo, env=env)

    result = _commit(repo, env)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert _head(repo, env=env) != before
    assert "pre-commit: OK" in output


def test_main_commit_without_hookspath_not_guarded(repo: Path, tmp_path: Path) -> None:
    """S-1: без `core.hooksPath .githooks` хук не работает — это и написано в README."""
    env = _base_env(tmp_path, worker=True)
    _run(repo, "config", "--unset", "core.hooksPath", env=env)
    before = _head(repo, env=env)

    result = _commit(repo, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo, env=env) != before


def test_reviewer_commit_to_main_allowed(repo: Path, tmp_path: Path) -> None:
    """S-1: профиль worker-review собирает main (§7 п.8) — коммит в main проходит.

    Без исключения ревьюер попадал в `worker-*` и не мог закоммитить мерж, то есть
    после установки хуков main некому было обновлять.
    """
    env = _base_env(tmp_path)
    env["HERMES_PROFILE"] = "worker-review"
    before = _head(repo, env=env)

    result = _commit(repo, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo, env=env) != before


def test_reviewer_profile_overrides_kanban_task(repo: Path, tmp_path: Path) -> None:
    """S-1: worker-review перебивает HERMES_KANBAN_TASK — ревьюеру main разрешён."""
    env = _base_env(tmp_path, worker=True)
    env["HERMES_PROFILE"] = "worker-review"
    before = _head(repo, env=env)

    result = _commit(repo, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo, env=env) != before


# --- pre-push ------------------------------------------------------------------


def test_worker_push_main_blocked(repo: Path, remote: Path, tmp_path: Path) -> None:
    """S-1: воркер не пушит main — код возврата не 0, origin/main не сдвинулся."""
    env = _base_env(tmp_path, worker=True)
    # Коммит в main делаем в человеческом контексте, иначе локальный pre-commit
    # остановит его раньше push — проверяем именно pre-push.
    assert _commit(repo, _base_env(tmp_path)).returncode == 0
    local = _head(repo, "main", env=env)
    before_remote = _head(repo, "refs/remotes/origin/main", env=env)
    assert local != before_remote

    result = _run(repo, "push", "origin", "main", env=env)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "push в main запрещён" in output
    assert "воркер не пушит в основную ветку" in output
    assert "wt/1" in output
    assert _head(repo, "refs/remotes/origin/main", env=env) == before_remote


def test_worker_push_task_branch_allowed(repo: Path, remote: Path, tmp_path: Path) -> None:
    """S-1: push ветки wt/<task-id> воркеру разрешён."""
    env = _base_env(tmp_path, worker=True)
    _run(repo, "switch", "-q", "-c", "wt/1", env=env)
    assert _commit(repo, env).returncode == 0

    result = _run(repo, "push", "origin", "wt/1", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo, "refs/remotes/origin/wt/1", env=env) == _head(repo, env=env)


def test_human_push_main_warns_but_succeeds(repo: Path, remote: Path, tmp_path: Path) -> None:
    """S-1: человеку push в main разрешён, но печатается предупреждение."""
    env = _base_env(tmp_path)
    assert _commit(repo, env).returncode == 0
    local = _head(repo, "main", env=env)

    result = _run(repo, "push", "origin", "main", env=env)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "это основная ветка" in output
    assert _head(repo, "refs/remotes/origin/main", env=env) == local


def test_reviewer_push_main_allowed(repo: Path, remote: Path, tmp_path: Path) -> None:
    """S-1: ревьюер пушит main — это его работа (§7 п.8), предупреждения нет."""
    env = _base_env(tmp_path)
    env["HERMES_PROFILE"] = "worker-review"
    assert _commit(repo, env).returncode == 0
    local = _head(repo, "main", env=env)

    result = _run(repo, "push", "origin", "main", env=env)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "это основная ветка" not in output
    assert _head(repo, "refs/remotes/origin/main", env=env) == local


def test_reviewer_push_main_allowed_even_with_kanban_task(repo: Path, remote: Path, tmp_path: Path) -> None:
    """S-1: HERMES_KANBAN_TASK не мешает ревьюеру — профиль перебивает задачу."""
    env = _base_env(tmp_path, worker=True)
    env["HERMES_PROFILE"] = "worker-review"
    assert _commit(repo, env).returncode == 0
    local = _head(repo, "main", env=env)

    result = _run(repo, "push", "origin", "main", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head(repo, "refs/remotes/origin/main", env=env) == local


def test_push_branch_outside_scheme_warns_but_passes(repo: Path, remote: Path, tmp_path: Path) -> None:
    """S-1: ветка вне wt/*/card-* — предупреждение, но push проходит."""
    env = _base_env(tmp_path, worker=True)
    _run(repo, "switch", "-q", "-c", "feature/проба", env=env)
    assert _commit(repo, env).returncode == 0

    result = _run(repo, "push", "origin", "feature/проба", env=env)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "вне схемы wt/<task-id> или card-*" in output
