#!/usr/bin/env python3
"""Уборка ветвей: что влито и удаляется, что живо и трогать нельзя.

Дефект, ради которого скрипт: правило «ветка на задачу, срок до трёх дней» в
`docs/development/26` §3 было, а шага «ветка умирает вместе с картой» не было ни в §7a,
ни в §7c. К 15.11.2026 на `origin` накопилось 68 ветвей, из них 65 влитых: у каждой
`git rev-list --count main..branch = 0`. Мусор неотличим от работы на глаз и по дате —
отличим только по числу неприменённых патчей.

Классификация (никогда по дате):

  merged    — `0` коммитов сверх базы: содержимое уже в базе, ветвь можно удалять;
  alive     — есть неприменённые патчи: это работа, её не трогаем;
  protected — текущая ветвь, база и ветви, занятые worktree (идёт карта): не удаляются
              никогда, даже если формально `merged`.

Удаление — только по явному `--delete` и только для `merged`; список печатается до
удаления. `--quiet-when-clean` даёт молчание, когда удалять нечего: так задание
планировщика не шумит каждую неделю.

  env -u PYTHONPATH .venv/bin/python scripts/branch_reap.py                  # отчёт
  env -u PYTHONPATH .venv/bin/python scripts/branch_reap.py --json           # машинный
  env -u PYTHONPATH .venv/bin/python scripts/branch_reap.py --delete         # убрать merged
  env -u PYTHONPATH .venv/bin/python scripts/branch_reap.py --remote --delete  # и на origin
  env -u PYTHONPATH .venv/bin/python scripts/branch_reap.py --no-fetch       # без сети

Тест: tests/unit/test_branch_reap.py. Правило: `docs/development/26` §7a п.8, §7c.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_NAMES = ("main", "master")


class Branch:
    """Ветвь и её судьба. `unique` — сколько коммитов есть в ней и нет в базе."""

    __slots__ = ("name", "unique", "scope", "worktree", "current")

    def __init__(self, name: str, unique: int, scope: str,
                 worktree: str | None = None, current: bool = False) -> None:
        self.name = name
        self.unique = unique
        self.scope = scope            # "local" | "remote"
        self.worktree = worktree
        self.current = current

    @property
    def protected(self) -> bool:
        return self.current or bool(self.worktree) or self.name in PROTECTED_NAMES

    @property
    def fate(self) -> str:
        if self.protected:
            return "protected"
        return "merged" if self.unique == 0 else "alive"

    def as_dict(self) -> dict:
        return {"name": self.name, "scope": self.scope, "unique": self.unique,
                "fate": self.fate, "worktree": self.worktree}


def classify(branches: list[Branch]) -> dict[str, list[Branch]]:
    """Три корзины по `unique` и защите. Порядок внутри — по имени, чтобы диффы были стабильны."""
    out: dict[str, list[Branch]] = {"merged": [], "alive": [], "protected": []}
    for branch in branches:
        out[branch.fate].append(branch)
    for bucket in out.values():
        bucket.sort(key=lambda b: b.name)
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def _worktrees() -> dict[str, str]:
    """Ветвь → путь worktree. Пути содержат пробелы, поэтому разбираем `--porcelain`."""
    mapping: dict[str, str] = {}
    path, branch = None, None
    for line in _git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
        elif not line.strip() and branch and path:
            mapping[branch] = path
            path = branch = None
    if branch and path:
        mapping[branch] = path
    return mapping


def collect(base: str, remote: str | None = None) -> list[Branch]:
    """Ветви локальные и (если задано) на `remote`, с числом неприменённых патчей."""
    current = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    worktrees = _worktrees()
    branches = [Branch(name, int(_git("rev-list", "--count", f"{base}..{name}").strip() or 0),
                       "local", worktrees.get(name), name == current)
                for name in _git("for-each-ref", "--format=%(refname:short)", "refs/heads").split()]
    if remote:
        for line in _git("for-each-ref", "--format=%(refname:short) %(symref)",
                         f"refs/remotes/{remote}").splitlines():
            parts = line.split()
            if not parts or len(parts) > 1:
                continue          # `origin` — это symref на origin/main, а не ветвь
            name = parts[0]
            if "/" not in name:
                continue
            short = name.split("/", 1)[1]
            if short == "HEAD":
                continue
            # Удалённый двойник ветви под worktree — это ветвь идущей карты: удалять нельзя.
            branches.append(Branch(short, int(_git("rev-list", "--count",
                                                   f"{base}..{name}").strip() or 0), "remote",
                                   worktrees.get(short)))
    return branches


def delete(buckets: dict[str, list[Branch]], remote: str | None, log) -> list[str]:
    """Удаляет `merged`. Возвращает список удалённых имён."""
    done: list[str] = []
    for branch in buckets["merged"]:
        if branch.scope == "local":
            proc = subprocess.run(["git", "branch", "-D", branch.name], cwd=ROOT,
                                  capture_output=True, text=True)
        else:
            if not remote:
                continue
            proc = subprocess.run(["git", "push", remote, "--delete", branch.name], cwd=ROOT,
                                  capture_output=True, text=True)
        if proc.returncode == 0:
            done.append(f"{branch.scope}:{branch.name}")
            log(f"удалена {branch.scope}:{branch.name}")
        else:
            log(f"НЕ удалена {branch.scope}:{branch.name}: {proc.stderr.strip()[:200]}")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Что влито, что живо, что можно удалить")
    parser.add_argument("--base", default="origin/main", help="база сравнения (по умолчанию origin/main)")
    parser.add_argument("--remote", default="origin", help="удалённый репозиторий; пусто — не смотреть")
    parser.add_argument("--delete", action="store_true", help="удалить влитые ветви (сначала печатает список)")
    parser.add_argument("--json", action="store_true", help="машинный отчёт")
    parser.add_argument("--no-fetch", action="store_true",
                        help="не обновлять remote-tracking ссылки (иначе fetch --prune)")
    parser.add_argument("--quiet-when-clean", action="store_true",
                        help="молчать, когда удалять нечего (для задания планировщика)")
    args = parser.parse_args(argv)

    if args.remote and not args.no_fetch:
        # Без prune удалённая ветвь живёт в отчёте как «merged»: её уже нет на GitHub,
        # а локальная ссылка осталась. Отчёт должен показывать то, что есть в GitHub.
        subprocess.run(["git", "fetch", "--prune", args.remote], cwd=ROOT,
                       capture_output=True, text=True)
    branches = collect(args.base, args.remote or None)
    buckets = classify(branches)
    deleted: list[str] = []
    if args.delete:
        deleted = delete(buckets, args.remote or None, lambda line: print(line, file=sys.stderr))

    if args.json:
        print(json.dumps({"base": args.base,
                          "alive": [b.as_dict() for b in buckets["alive"]],
                          "merged": [b.as_dict() for b in buckets["merged"]],
                          "protected": [b.as_dict() for b in buckets["protected"]],
                          "deleted": deleted}, ensure_ascii=False, indent=2))
        return 0

    if args.quiet_when_clean and not buckets["merged"]:
        return 0

    print(f"база сравнения: {args.base}")
    print(f"живых (есть неприменённые патчи): {len(buckets['alive'])}")
    for branch in buckets["alive"]:
        print(f"  {branch.name:<40} {branch.unique} шт.  {branch.scope}")
    print(f"влитых (удаляются): {len(buckets['merged'])}")
    for branch in buckets["merged"]:
        print(f"  {branch.name:<40} {branch.scope}")
    if buckets["protected"]:
        print(f"защищено (текущая, база, worktree): {len(buckets['protected'])}")
        for branch in buckets["protected"]:
            why = "worktree" if branch.worktree else "база/текущая"
            print(f"  {branch.name:<40} {why}")
    if args.delete:
        print(f"удалено: {len(deleted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
