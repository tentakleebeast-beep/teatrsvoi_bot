"""Секреты рядом с кодом не должны попадать в индекс.

Повод (01.11.2026): в корне репозитория появились `creds.json` (ключи сервисного
аккаунта) и `.env.dev` (локальное окружение). Оба начинались с точки/были json и
оба **не** были закрыты `.gitignore`: шаблон `.env` закрывает ровно файл `.env`,
а `creds.json` не закрывался ничем. Достаточно было `git add -A` в воркерском
worktree, чтобы ключи уехали в `origin`.

Правило проекта — `docs/development/26-process-and-git.md` §7a: перед коммитом
`git status --porcelain` не показывает `.env`, `secrets/`, `creds.json`; шаблон
`.env.example` обязан остаться в индексе, иначе новый человек не узнает, какие
переменные нужны.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Должны быть закрыты шаблонами (файлы могут отсутствовать — проверяем правила).
MUST_IGNORE = (".env", ".env.dev", ".env.local", "creds.json", "google-sa.creds.json",
               "secrets/token.txt")
# Должны остаться в индексе: без них непонятно, что настраивать.
MUST_TRACK = (".env.example",)

# Публичный чек-лист (план 129 §Ш-01): рядом с отчётами не бывает выгрузок живых данных.
# Шаблоны намеренно по имени, а не по каталогу: файл вида `*.dump` в любом месте — тоже
# выгрузка, но проверяем строго то, что заявлено в плане, — каталог отчётов.
REPORTS_DIR = "docs/reports/"
REPORTS_FORBIDDEN = ("*backup*.json", "*.dump", "*.sql.gz")
# Исключений нет: два дампа, которые лежали в индексе, удалены картой Ш-02 (t_84c72fab),
# и вернуться они не могут — запрет ловит и их, и любой новый `*backup*.json`/`*.dump`/`*.sql.gz`.


def _forbidden_in_reports(path: str) -> bool:
    """Путь под `docs/reports/` с именем из запрещённых шаблонов."""
    if not path.startswith(REPORTS_DIR):
        return False
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in REPORTS_FORBIDDEN)



def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def _git_ok() -> bool:
    return _git("rev-parse", "--git-dir").returncode == 0


def test_секреты_закрыты_gitignore():
    """Каждый файл с секретами закрыт шаблоном, а не только `.env` по имени."""
    if not _git_ok():
        pytest.skip("нет git")
    leaky = [name for name in MUST_IGNORE if _git("check-ignore", "-q", name).returncode != 0]
    assert not leaky, "не закрыты .gitignore: " + ", ".join(leaky)


def test_шаблон_окружения_остаётся_в_индексе():
    """`.env.example` — документация окружения, его нельзя закрывать шаблоном."""
    if not _git_ok():
        pytest.skip("нет git")
    for name in MUST_TRACK:
        assert (ROOT / name).exists(), f"{name} пропал из репозитория"
        assert _git("check-ignore", "-q", name).returncode != 0, f"{name} закрыт .gitignore"
        tracked = _git("ls-files", "--error-unmatch", name).returncode == 0
        assert tracked, f"{name} не в индексе"


def test_секретов_нет_в_индексе():
    """Ни один файл с ключами не отслеживается, даже если попал в каталог раньше.

    Шаблоны `*.example` — исключение: в них нет значений, только имена переменных
    (`docs/development/26-process-and-git.md` §7a). Поэтому `.env.dev.example`
    (шаблон dev-окружения, карта t_4aa28fe9) в индексе допустим, а `.env.dev` — нет.
    """
    if not _git_ok():
        pytest.skip("нет git")
    tracked = _git("ls-files").stdout.splitlines()
    bad = [path for path in tracked
           if not path.endswith(".example")
           and (path.endswith("creds.json") or path.startswith(("secrets/", ".env."))
                or path == ".env")]
    assert not bad, "в индексе секреты: " + ", ".join(bad)


def test_выгрузок_данных_нет_рядом_с_отчётами():
    """В `docs/reports/` не лежат выгрузки живых данных: бэкапы, дампы, архивы SQL.

    Повод — два дампа в индексе, найденные планом 129: строки бюджета и лист «Финансы».
    Оба удалены в Ш-02 (t_84c72fab); ловушка здесь, чтобы следующий такой файл не проехал
    незамеченным, и работает без списка исключений.
    """
    if not _git_ok():
        pytest.skip("нет git")
    tracked = _git("ls-files").stdout.splitlines()
    bad = [path for path in tracked if _forbidden_in_reports(path)]
    assert not bad, "в docs/reports/ выгрузки данных: " + ", ".join(bad)


@pytest.mark.parametrize("path", [
    "docs/reports/dev-db-test-rows-backup-31.10.2026.json",
    "docs/reports/finance-sheet-backup-28.10.2026.json",
    "docs/reports/svoi-2026-11-05.dump",
    "docs/reports/svoi-2026-11-05.sql.gz",
    "docs/reports/archive/old/svoi.dump",
])
def test_правило_ловит_выгрузку(path: str):
    """Запрет действует: шаблоны ловят бэкапы, дампы и архивы, где бы они ни лежали внутри."""
    assert _forbidden_in_reports(path), f"{path} не пойман правилом"


@pytest.mark.parametrize("path", [
    "docs/reports/136-gitleaks-inventory.md",
    "docs/reports/137-public-repo-file-decisions.md",
    "docs/reports/116-vk-miniapp-second-front.md",
    "scripts/backup.sh",
    ".github/workflows/backup-timer.yml",
])
def test_правило_не_трогает_отчёты_и_код_бэкапов(path: str):
    """Обратная сторона: отчёты и скрипты резервного копирования не блокируются."""
    assert not _forbidden_in_reports(path), f"{path} ошибочно запрещён"

