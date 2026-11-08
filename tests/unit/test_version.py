"""Версия живой сборки: /health отдаёт git_sha и uptime (FR-H12, L-005)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.version import (
    UNKNOWN,
    format_uptime,
    project_root,
    read_git_sha,
    uptime_seconds,
)

SHA = "a43a396be7c1f0d2e3b4c5d6e7f8091a2b3c4d5e"


def git_repo(tmp_path: Path, head: str, ref: str | None = None) -> Path:
    git = tmp_path / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text(head, encoding="utf-8")
    if ref:
        target = git / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(SHA + "\n", encoding="utf-8")
    return tmp_path


def test_sha_из_ссылки_ветки(tmp_path):
    root = git_repo(tmp_path, "ref: refs/heads/main\n", "refs/heads/main")
    assert read_git_sha(root, env={}) == SHA[:12]


def test_sha_из_packed_refs(tmp_path):
    root = git_repo(tmp_path, "ref: refs/heads/main\n")
    (root / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA} refs/heads/main\n", encoding="utf-8")
    assert read_git_sha(root, env={}) == SHA[:12]


def test_detached_head_и_переменная_окружения(tmp_path):
    root = git_repo(tmp_path, SHA + "\n")
    assert read_git_sha(root, env={}) == SHA[:12]
    other = git_repo(tmp_path / "other", SHA + "\n")
    assert read_git_sha(other, env={"GIT_SHA": "191199a"}) == "191199a"


def test_нет_git_или_мусор_в_head(tmp_path):
    plain = tmp_path / "no-git"
    plain.mkdir()
    assert read_git_sha(plain, env={}) == UNKNOWN
    broken = git_repo(tmp_path / "broken", "ref: refs/heads/main\n", "refs/heads/main")
    (broken / ".git" / "refs" / "heads" / "main").write_text("не sha\n", encoding="utf-8")
    assert read_git_sha(broken, env={}) == UNKNOWN


def test_корень_проекта_содержит_git():
    assert (project_root() / "services" / "tool-api" / "app" / "version.py").is_file()
    assert (project_root() / ".git").exists()


def test_uptime_и_человеческий_вид():
    start = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    assert uptime_seconds(start, start + timedelta(seconds=42)) == 42
    assert uptime_seconds(start, start - timedelta(minutes=5)) == 0
    assert format_uptime(42) == "42с"
    assert format_uptime(125) == "2м 5с"
    assert format_uptime(3 * 3600 + 4 * 60 + 5) == "3ч 4м"
    assert format_uptime(86400 + 2 * 3600) == "1д 2ч 0м"
