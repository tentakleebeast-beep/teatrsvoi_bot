"""Релизный цикл: prepare ничего не публикует, --apply выкатывает по команде.

Обоснование: docs/development/26 §6 — прод обновляется только по команде владельца;
тег ставится на main при чистом дереве, раздел CHANGELOG обязателен, существующий тег
не переставляется, откат — предыдущий тег. Тесты гоняют скрипт на временном git-репозитории
с bare-origin; живой прод не трогается, `gh` подменён заглушкой в PATH.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "release.sh"

CHANGELOG = """# Журнал версий

## v0.4.0 — 01.11.2026

### Добавлено

- выпуск версии одной командой.
"""


def _env(**extra: str) -> dict[str, str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Тест",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Тест",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    env.update(extra)
    return env


def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, env=_env(),
    )


def _commit(repo: pathlib.Path, name: str, text: str, message: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert _git(repo, "add", "-A").returncode == 0
    result = _git(repo, "commit", "-m", message)
    assert result.returncode == 0, result.stderr


def _repo(tmp_path: pathlib.Path, changelog: str = CHANGELOG) -> pathlib.Path:
    """Клон с bare-origin: prepare сверяет HEAD с origin/main, значит origin нужен."""
    origin = tmp_path / "origin.git"
    assert subprocess.run(["git", "init", "--bare", str(origin)],
                          capture_output=True, env=_env()).returncode == 0
    repo = tmp_path / "repo"
    assert subprocess.run(["git", "clone", str(origin), str(repo)],
                          capture_output=True, text=True, env=_env()).returncode == 0
    assert _git(repo, "checkout", "-b", "main").returncode == 0
    _commit(repo, "README.md", "репозиторий\n", "feat: база")
    assert _git(repo, "tag", "-a", "v0.3.0", "-m", "Релиз v0.3.0").returncode == 0
    (repo / "deploy").mkdir()
    (repo / "deploy" / "release.sh").write_text(SCRIPT.read_text(encoding="utf-8"),
                                                encoding="utf-8")
    # один коммит после тега — он и должен попасть в состав релиза
    _commit(repo, "CHANGELOG.md", changelog, "feat: release.sh")
    assert _git(repo, "push", "-u", "origin", "main").returncode == 0
    assert _git(repo, "push", "origin", "v0.3.0").returncode == 0
    return repo


def _run(repo: pathlib.Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "deploy/release.sh", *args],
        cwd=repo, capture_output=True, text=True, env=_env(**extra),
    )


def _has_tag(repo: pathlib.Path, tag: str) -> bool:
    return _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0


def test_грязное_дерево_не_ставит_тег(tmp_path):
    """Незакоммиченные изменения → код 1, тега нет."""
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("правка без коммита\n", encoding="utf-8")

    result = _run(repo, "v0.4.0", "--skip-tests")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "грязное" in result.stdout + result.stderr
    assert not _has_tag(repo, "v0.4.0")


def test_нет_раздела_changelog_не_ставит_тег(tmp_path):
    """Раздел версии обязателен: иначе состав релиза не восстановить."""
    repo = _repo(tmp_path)

    result = _run(repo, "v0.9.9", "--skip-tests")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "нет раздела CHANGELOG для v0.9.9" in result.stdout + result.stderr
    assert not _has_tag(repo, "v0.9.9")


def test_prepare_ставит_локальный_тег_и_печатает_план(tmp_path):
    """Тег создан, push не было, план содержит тег, sha и коммиты от прошлого тега."""
    repo = _repo(tmp_path)

    result = _run(repo, "0.4.0", "--skip-tests")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _has_tag(repo, "v0.4.0")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert "тег:               v0.4.0" in result.stdout
    assert sha in result.stdout
    assert "от тега:           v0.3.0" in result.stdout
    assert "feat: release.sh" in result.stdout
    assert "Прод не тронут" in result.stdout
    # push не выполнялся: в bare-origin тега нет
    assert _git(tmp_path / "origin.git", "rev-parse", "-q", "--verify",
                "refs/tags/v0.4.0").returncode != 0


def _fake_gh(tmp_path: pathlib.Path, watch_rc: int) -> tuple[pathlib.Path, pathlib.Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'if [ "$1 $2" = "run list" ]; then echo 4242; exit 0; fi\n'
        f'if [ "$1 $2" = "run watch" ]; then exit {watch_rc}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return bin_dir, log


def _notify_stub(repo: pathlib.Path) -> pathlib.Path:
    """Подставной scripts/notify_admin.py: пишет аргументы в файл, в Telegram не ходит."""
    out = repo / "notify.log"
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "notify_admin.py").write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(out)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return out


def test_apply_запускает_workflow_и_сообщает_об_откате(tmp_path):
    """--apply: push тега, gh workflow run с dry_run=false, при провале — notify_admin."""
    repo = _repo(tmp_path)
    bin_dir, gh_log = _fake_gh(tmp_path, watch_rc=1)

    prepared = _run(repo, "v0.4.0", "--skip-tests")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    notify = _notify_stub(repo)

    result = _run(repo, "v0.4.0", "--apply", "--skip-tests",
                  PATH=f"{bin_dir}:{os.environ['PATH']}")

    assert result.returncode == 1, result.stdout + result.stderr
    calls = gh_log.read_text(encoding="utf-8").splitlines()
    assert "workflow run deploy.yml -f tag=v0.4.0 -f dry_run=false" in calls
    assert any(call.startswith("run watch") for call in calls), calls
    text = notify.read_text(encoding="utf-8")
    assert "v0.4.0" in text and "откат" in text.lower()
    # тег ушёл в origin — выкатывается именно он
    assert _git(tmp_path / "origin.git", "rev-parse", "-q", "--verify",
                "refs/tags/v0.4.0").returncode == 0
