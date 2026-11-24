"""Релизный цикл: prepare ничего не публикует, --apply выкатывает по команде.

Обоснование: docs/development/26 §6 — прод обновляется только по команде владельца;
тег ставится на main при чистом дереве, раздел CHANGELOG обязателен, существующий тег
не переставляется, откат — предыдущий тег. Тесты гоняют скрипт на временном git-репозитории
с bare-origin; живой прод не трогается, `gh` и `curl` подменены заглушками в PATH.

Д-5 (docs/reports/78): `--apply` ждёт свой run по headSha и времени запуска, а не
«первый в списке»; состояние пишется только когда прод подтвердил коммит тега
(адрес прод-проверки задан в deploy/release.sh).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "release.sh"
REPORT = "docs/reports/78-release-state.md"

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


def _head(repo: pathlib.Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _report(repo: pathlib.Path) -> pathlib.Path:
    path = repo / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 78. Состояние релиза\n", encoding="utf-8")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-m", "docs: отчёт 78").returncode == 0
    # prepare сверяет HEAD с origin/main: без push коммит отчёта её сорвёт
    assert _git(repo, "push", "origin", "main").returncode == 0
    return path


def _fake_bin(tmp_path: pathlib.Path, *, watch_rc: int = 0, run_id: str = "5001",
              ready_after: int = 1, stale_id: str = "4242",
              health: str | None = None) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Заглушки gh и curl в PATH.

    gh отдаёт то, что у него спросили: если в вызове нет отбора по `headSha`
    (прежний `--limit 1`), печатается прошлый успешный run — ровно дефект Д-5.
    `ready_after` — на какой проверке GitHub «регистрирует» наш run.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh.log"
    repo_log = tmp_path / "gh_repo.log"
    polls = tmp_path / "gh_polls"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'printf "%s\\n" "${{GH_REPO:-<не задан>}}" >> "{repo_log}"\n'
        'if [ "$1 $2" = "run list" ]; then\n'
        '    case "$*" in\n'
        '        *"select(.headSha"*)\n'
        f'            n=0; [ -f "{polls}" ] && n="$(cat "{polls}")"\n'
        f'            n=$((n + 1)); printf "%s" "$n" > "{polls}"\n'
        f'            if [ "$n" -ge {ready_after} ]; then printf "%s\\n" "{run_id}"; fi\n'
        '            ;;\n'
        f'        *) printf "%s\\n" "{stale_id}" ;;\n'
        '    esac\n'
        '    exit 0\n'
        'fi\n'
        f'if [ "$1 $2" = "run watch" ]; then exit {watch_rc}; fi\n'
        'exit 0\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    if health is not None:
        curl = bin_dir / "curl"
        curl.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' '{health}'\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)
    return bin_dir, log, repo_log


def _apply_env(bin_dir: pathlib.Path, attempts: int = 3) -> dict[str, str]:
    """PATH с заглушками и короткое ожидание: опрос не должен длиться минуты."""
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RELEASE_RUN_POLL_ATTEMPTS": str(attempts),
        "RELEASE_RUN_POLL_SECONDS": "0",
    }


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
    sha = _head(repo)
    assert "тег:               v0.4.0" in result.stdout
    assert sha in result.stdout
    assert "от тега:           v0.3.0" in result.stdout
    assert "feat: release.sh" in result.stdout
    assert "Прод не тронут" in result.stdout
    # push не выполнялся: в bare-origin тега нет
    assert _git(tmp_path / "origin.git", "rev-parse", "-q", "--verify",
                "refs/tags/v0.4.0").returncode != 0


def test_apply_запускает_workflow_и_сообщает_об_откате(tmp_path):
    """--apply: push тега, gh workflow run с dry_run=false, при провале — notify_admin.

    Провал run → код 1, id прогона в сообщении админу, в отчёт 78 ничего не пишется.
    """
    repo = _repo(tmp_path)
    report = _report(repo)
    before = report.read_text(encoding="utf-8")
    bin_dir, gh_log, repo_log = _fake_bin(tmp_path, watch_rc=1)

    prepared = _run(repo, "v0.4.0", "--skip-tests")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    notify = _notify_stub(repo)

    result = _run(repo, "v0.4.0", "--apply", "--skip-tests", **_apply_env(bin_dir))

    assert result.returncode == 1, result.stdout + result.stderr
    calls = gh_log.read_text(encoding="utf-8").splitlines()
    assert "workflow run deploy.yml -f tag=v0.4.0 -f dry_run=false" in calls
    assert any(call.startswith("run watch 5001") for call in calls), calls
    text = notify.read_text(encoding="utf-8")
    assert "v0.4.0" in text and "откат" in text.lower()
    assert "5001" in text
    assert report.read_text(encoding="utf-8") == before
    # тег ушёл в origin — выкатывается именно он
    assert _git(tmp_path / "origin.git", "rev-parse", "-q", "--verify",
                "refs/tags/v0.4.0").returncode == 0
    # выкат идёт из единственного публичного репозитория (docs/26 §10)
    assert set(repo_log.read_text(encoding="utf-8").split()) == {
        "tentakleebeast-beep/teatrsvoi_bot"}


def test_apply_ждёт_свой_run_а_не_первый_в_списке(tmp_path):
    """Д-5: пока GitHub регистрирует run, первый в списке — прошлый успешный.

    Ждём run с headSha = HEAD и createdAt не раньше запуска; чужой run 4242
    не должен попасть в `gh run watch` (иначе «Run успешен» без выката).
    """
    repo = _repo(tmp_path)
    report = _report(repo)
    sha = _head(repo)
    health = f'{{"ok":true,"env":"prod","git_sha":"{sha[:12]}"}}'
    bin_dir, gh_log, _ = _fake_bin(tmp_path, run_id="5001", ready_after=2,
                                   stale_id="4242", health=health)

    prepared = _run(repo, "v0.4.0", "--skip-tests")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr

    result = _run(repo, "v0.4.0", "--apply", "--skip-tests", **_apply_env(bin_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    calls = gh_log.read_text(encoding="utf-8").splitlines()
    lists = [call for call in calls if call.startswith("run list")]
    assert len(lists) >= 2, calls
    for call in lists:
        assert "select(.headSha" in call, call
        assert sha in call, call
        assert "createdAt >=" in call, call
    watched = [call for call in calls if call.startswith("run watch")]
    assert watched == ["run watch 5001 --exit-status"], calls
    assert not any("4242" in call for call in calls), calls
    assert "Run 5001 успешен" in result.stdout
    assert "git_sha" in report.read_text(encoding="utf-8")


def test_apply_без_своего_run_не_рапортует_успех(tmp_path):
    """Своего run так и не появилось → код 1, чужой успешный run не засчитывается."""
    repo = _repo(tmp_path)
    report = _report(repo)
    before = report.read_text(encoding="utf-8")
    bin_dir, gh_log, _ = _fake_bin(tmp_path, watch_rc=0, run_id="5001",
                                   ready_after=999, stale_id="4242")

    prepared = _run(repo, "v0.4.0", "--skip-tests")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr

    result = _run(repo, "v0.4.0", "--apply", "--skip-tests", **_apply_env(bin_dir))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "не появился" in result.stdout + result.stderr
    calls = gh_log.read_text(encoding="utf-8").splitlines()
    assert not any(call.startswith("run watch") for call in calls), calls
    assert report.read_text(encoding="utf-8") == before


def test_apply_пишет_git_sha_из_health_и_сверяет_с_тегом(tmp_path):
    """Успешный run: git_sha в отчёте — из прод-проверки, с id прогона и без догадок."""
    repo = _repo(tmp_path)
    report = _report(repo)
    sha = _head(repo)
    health = f'{{"ok":true,"env":"prod","git_sha":"{sha[:12]}"}}'
    bin_dir, _, _ = _fake_bin(tmp_path, run_id="7777", health=health)

    prepared = _run(repo, "v0.4.0", "--skip-tests")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr

    result = _run(repo, "v0.4.0", "--apply", "--skip-tests", **_apply_env(bin_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    text = report.read_text(encoding="utf-8")
    assert "## Состояние на" in text
    assert f"git_sha: `{sha[:12]}`" in text
    assert f"`{sha[:12]}`" in text and "совпадает с коммитом тега" in text
    assert "после run 7777" in text
    assert "источник:" in text
    # запись закоммичена, а не брошена в рабочем дереве
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert "docs(release): v0.4.0" in _git(repo, "log", "-1", "--format=%s").stdout


def test_apply_не_пишет_отчёт_если_прод_на_другом_коммите(tmp_path):
    """Прод отвечает чужим git_sha → код 1, в отчёт 78 состояние не записывается."""
    repo = _repo(tmp_path)
    report = _report(repo)
    before = report.read_text(encoding="utf-8")
    health = '{"ok":true,"env":"prod","git_sha":"deadbeef1234"}'
    bin_dir, _, _ = _fake_bin(tmp_path, run_id="8888", health=health)

    prepared = _run(repo, "v0.4.0", "--skip-tests")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr

    result = _run(repo, "v0.4.0", "--apply", "--skip-tests", **_apply_env(bin_dir))

    assert result.returncode == 1, result.stdout + result.stderr
    text = result.stdout + result.stderr
    assert "deadbeef1234" in text and "v0.4.0" in text
    assert report.read_text(encoding="utf-8") == before
