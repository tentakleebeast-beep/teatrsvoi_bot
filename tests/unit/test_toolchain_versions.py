"""Одна версия Python на весь контур (аудит 96, находки N-2/N-3).

Раньше версии расходились: `ruff` проверял синтаксис 3.11, CI и образы собирались на 3.12,
локальный venv жил на 3.14 — то есть проверки не описывали ни прод, ни машину разработчика.
Теперь эталон один — `.python-version`, и тест не даёт ему разъехаться с `pyproject.toml`,
`Dockerfile`, workflow и README.

Тест читает файлы, а не запускает интерпретаторы: он дешёвый и падает на том же шаге,
где и остальные unit-тесты.

Правила и требования раздела: docs/development/26-process-and-git.md.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
VERSION_FILE = ROOT / ".python-version"
README = ROOT / "README.md"
DOCKERFILES = (
    ROOT / "services" / "tool-api" / "Dockerfile",
    ROOT / "services" / "tg-gateway" / "Dockerfile",
)

FROM_PYTHON = re.compile(r"^FROM\s+python:(\d+\.\d+)[-\w.]*", re.MULTILINE)


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _pyproject() -> dict:
    return tomllib.loads(_text(PYPROJECT))


def _pinned() -> str:
    """Версия из `.python-version` — та, по которой выровнены остальные файлы."""
    return _text(VERSION_FILE).strip()


def _workflow() -> dict:
    return yaml.safe_load(_text(WORKFLOW))


def _run_commands(job: dict) -> str:
    """Все `run:` одного job как один текст — искать по ним проще, чем по шагам."""
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def _job_env(job: dict) -> dict:
    env: dict = {}
    for step in job.get("steps", []):
        env.update(step.get("env") or {})
    env.update(job.get("env") or {})
    return env


def test_эталон_версии_задан_в_python_version() -> None:
    pinned = _pinned()
    assert re.fullmatch(r"\d+\.\d+", pinned), f".python-version должен содержать 3.12, сейчас {pinned!r}"


def test_requires_python_задан_и_равен_эталону() -> None:
    requires = _pyproject()["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(\d+\.\d+)", requires.strip())
    assert match, (
        "requires-python должен задавать нижнюю границу одной минорной версии "
        f"(>=3.12), сейчас {requires!r}"
    )
    assert match.group(1) == _pinned()


def test_ruff_проверяет_ту_же_версию() -> None:
    target = _pyproject()["tool"]["ruff"]["target-version"]
    assert target == "py" + _pinned().replace(".", ""), (
        f"ruff target-version {target!r} не совпадает с эталоном {_pinned()}"
    )


def test_образы_собираются_на_той_же_версии() -> None:
    for dockerfile in DOCKERFILES:
        found = FROM_PYTHON.findall(_text(dockerfile))
        assert found, f"{dockerfile}: не нашёл базовый образ FROM python:<версия>"
        assert set(found) == {_pinned()}, (
            f"{dockerfile}: базовый образ {sorted(set(found))} вместо {_pinned()}"
        )


def test_ci_гоняет_тесты_на_той_же_версии() -> None:
    versions: dict[str, str] = {}
    for name, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/setup-python"):
                versions[name] = str(step["with"]["python-version"])
    assert versions, "в workflow нет ни одного actions/setup-python"
    for job_name, version in versions.items():
        assert version == _pinned(), f"job {job_name}: python-version {version} вместо {_pinned()}"


def test_readme_указывает_ту_же_версию() -> None:
    assert _pinned() in _text(README), f"README не называет версию Python {_pinned()}"


def test_браузерные_тесты_идут_отдельным_джобом_и_не_скипаются_молча() -> None:
    jobs = _workflow()["jobs"]
    assert "webapp" in jobs, "в workflow нет job `webapp`: браузерные тесты вернутся в skip"
    webapp_env = _job_env(jobs["webapp"])
    assert str(webapp_env.get("SVOI_WEBAPP_TESTS")) == "1", (
        "job `webapp` обязан задавать SVOI_WEBAPP_TESTS=1, иначе тесты пропускаются"
    )
    assert "--browser chromium" in _run_commands(jobs["webapp"]), (
        "job `webapp` должен гонять движок chromium явно"
    )
    assert "--ignore=tests/webapp" in _run_commands(jobs["tests"]), (
        "job `tests` не должен собирать tests/webapp: иначе skip-ы попадают в общий отчёт"
    )
