"""Тест хука перед коммитом `.githooks/pre-commit`.

Хук — локальный дубль гейта: `ruff check` по staged-файлам и
`scripts/check_invariants.py` целиком. Ставится один раз на клон командой
`git config core.hooksPath .githooks`; правило и чеклист — docs/development/26-process-and-git.md §4.

Тест герметичен: временный git-репозиторий в `tmp_path`, копия хука, глобальная
git-конфигурация отрезана, `SVOI_RUFF` — абсолютный путь к ruff из venv. Postgres
не нужен: `SVOI_INVARIANTS_SCRIPT` подменяется заглушкой, а при выставленной
переменной проба базы не делается — за доступность отвечает сам скрипт.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".githooks" / "pre-commit"
RUFF = ROOT / ".venv" / "bin" / "ruff"

pytestmark = pytest.mark.skipif(
    not RUFF.is_file(), reason="нет .venv/bin/ruff: нечем проверять staged-файлы"
)


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


def _head(repo: Path, env: dict[str, str]) -> str:
    return _run(repo, "log", "--oneline", "-1", env=env).stdout.strip()


def _write_script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Временный репозиторий с установленным хуком и заглушкой инвариантов."""
    path = tmp_path / "repo"
    path.mkdir()
    env = _env(tmp_path)
    _run(path, "init", "-q", env=env)
    _run(path, "config", "user.email", "hook@test.local", env=env)
    _run(path, "config", "user.name", "Hook Test", env=env)
    _run(path, "config", "core.hooksPath", ".githooks", env=env)

    hooks = path / ".githooks"
    hooks.mkdir()
    shutil.copy2(HOOK, hooks / "pre-commit")
    (hooks / "pre-commit").chmod(0o755)
    assert _run(path, "config", "--get", "core.hooksPath", env=env).stdout.strip() == ".githooks"

    # Проверка обоснованности тестов берёт скрипт из репозитория: в герметичном
    # репозитории его нет, поэтому копируем — иначе шаг молча пропускался бы.
    scripts = path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "test_audit.py", scripts / "test_audit.py")
    (path / "tests").mkdir()

    (path / "README.md").write_text("# демо\n", encoding="utf-8")
    (path / "_ok.py").write_text('print("ok")\n', encoding="utf-8")
    _run(path, "add", ".", env=env)
    _run(path, "commit", "-q", "--no-verify", "-m", "init", env=env)
    return path


def _env(tmp_path: Path) -> dict[str, str]:
    """Окружение коммита: без чужой git-конфигурации, с заглушкой инвариантов."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    invariants = _write_script(
        tmp_path / "ok_invariants.py", 'print("Все проверки пройдены.")\n'
    )
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "LC_ALL": "C",
        "SVOI_RUFF": str(RUFF),
        "SVOI_INVARIANTS_SCRIPT": str(invariants),
    }


def test_clean_markdown_commit_passes(repo: Path, tmp_path: Path) -> None:
    """Правка `.md` коммитится, вывод хука — ровно одна строка об успехе."""
    env = _env(tmp_path)
    before = _head(repo, env)
    (repo / "README.md").write_text("# демо\n\nправка\n", encoding="utf-8")
    _run(repo, "add", "README.md", env=env)

    result = _run(repo, "commit", "-q", "-m", "docs: правка", env=env)

    assert result.returncode == 0, result.stderr
    assert _head(repo, env) != before
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    assert len(lines) == 1, lines
    assert lines[0].startswith("pre-commit: OK")


def test_clean_python_commit_reports_ruff_and_invariants(repo: Path, tmp_path: Path) -> None:
    """Чистый `.py` проходит: в строке успеха есть и число файлов, и инварианты."""
    env = _env(tmp_path)
    (repo / "_clean_probe.py").write_text('print("ok")\n', encoding="utf-8")
    _run(repo, "add", "_clean_probe.py", env=env)

    result = _run(repo, "commit", "-q", "-m", "chore: чистый модуль", env=env)

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "ruff: 1 файл(ов)" in output
    assert "инварианты пройдены" in output


def test_ruff_violation_blocks_commit(repo: Path, tmp_path: Path) -> None:
    """Неиспользуемый импорт вне `tests/` не даёт создать коммит."""
    env = _env(tmp_path)
    before = _head(repo, env)
    (repo / "_probe_unused_import.py").write_text("import os\n", encoding="utf-8")
    _run(repo, "add", "_probe_unused_import.py", env=env)

    result = _run(repo, "commit", "-q", "-m", "chore: грязный импорт", env=env)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert _head(repo, env) == before
    assert "F401" in output
    assert "_probe_unused_import.py" in output
    assert "ruff (staged-файлы)" in output
    assert "--no-verify" in output


def test_failed_invariants_blocks_commit(repo: Path, tmp_path: Path) -> None:
    """Упавшая проверка инвариантов прерывает коммит и называет себя."""
    env = _env(tmp_path)
    env["SVOI_INVARIANTS_SCRIPT"] = str(
        _write_script(
            tmp_path / "fail_invariants.py",
            'print("FAIL I99 демо-инвариант")\nraise SystemExit(1)\n',
        )
    )
    before = _head(repo, env)
    (repo / "README.md").write_text("# демо\n\nещё правка\n", encoding="utf-8")
    _run(repo, "add", "README.md", env=env)

    result = _run(repo, "commit", "-q", "-m", "docs: правка", env=env)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert _head(repo, env) == before
    assert "инварианты (docs/architecture/11)" in output
    assert "FAIL I99 демо-инвариант" in output
    assert "--no-verify" in output


def test_clean_test_file_reports_audit(repo: Path, tmp_path: Path) -> None:
    """Тест с ID требования проходит, и строка успеха называет проверку обоснованности."""
    env = _env(tmp_path)
    (repo / "tests" / "test_ok.py").write_text(
        'def test_с_id():\n    """S-1: тест с обоснованием принимается."""\n    assert True\n',
        encoding="utf-8",
    )
    _run(repo, "add", "tests/test_ok.py", env=env)

    result = _run(repo, "commit", "-q", "-m", "test: с ID", env=env)

    assert result.returncode == 0, result.stderr
    assert "тесты обоснованы" in (result.stdout + result.stderr)


def test_test_without_id_blocks_commit(repo: Path, tmp_path: Path) -> None:
    """Тест без ID требования/дефекта не даёт создать коммит (docs/development/26 §7a)."""
    env = _env(tmp_path)
    before = _head(repo, env)
    (repo / "tests" / "test_bad.py").write_text(
        "def test_без_обоснования():\n    assert True\n", encoding="utf-8"
    )
    _run(repo, "add", "tests/test_bad.py", env=env)

    result = _run(repo, "commit", "-q", "-m", "test: без ID", env=env)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert _head(repo, env) == before
    assert "тесты без ID (docs/development/26 §7a)" in output
    assert "test_bad.py" in output          # строгий вывод называет файл
    assert "--no-verify" in output


def test_deleted_python_file_commits(repo: Path, tmp_path: Path) -> None:
    """Удаление `.py` не роняет хук на отсутствующем пути (`--diff-filter=ACMR`)."""
    env = _env(tmp_path)
    _run(repo, "rm", "-q", "_ok.py", env=env)

    result = _run(repo, "commit", "-q", "-m", "chore: удалить модуль", env=env)

    assert result.returncode == 0, result.stderr


def test_no_verify_skips_hook(repo: Path, tmp_path: Path) -> None:
    """`git commit --no-verify` при том же нарушении ruff создаёт коммит."""
    env = _env(tmp_path)
    before = _head(repo, env)
    (repo / "_probe_unused_import.py").write_text("import os\n", encoding="utf-8")
    _run(repo, "add", "_probe_unused_import.py", env=env)

    result = _run(repo, "commit", "-q", "--no-verify", "-m", "chore: в обход", env=env)

    assert result.returncode == 0, result.stderr
    assert _head(repo, env) != before
