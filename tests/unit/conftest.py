"""Тесты gateway берут модули через `gateway_modules` из этого же каталога.

Здесь же сторож окружения unit-набора (t_1dfb94b6): unit-тесты не должны менять
переменные, которые читают integration-тесты и стенд. `tests/conftest.py:_base_dsn()`
берёт `DATABASE_URL` из окружения, сервис — токен и маркер среды. Раньше
`test_conflict_watch` вызывал `--env dev`, продовый сторож (намеренно) писал
`.env.dev`-значения в `os.environ` своего процесса, и совместный прогон
`pytest tests/unit tests/integration` уходил на dev-`DATABASE_URL` с паролем `pw` —
786 ERROR на setup фикстуры `test_db`.

Сторож снимает окружение до и после каждого unit-модуля и падает тестом
`tests/unit/test_env_hygiene.py` в конце unit-раздела, называя виновный модуль.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Iterator

import pytest

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Переменные, которые читают integration-набор и стенд. Остальные настройки тесты
# передают явно (`configure(Settings(...))`), подменять окружение им не нужно.
GUARDED = ("DATABASE_URL", "TELEGRAM_TOKEN", "ENV")

GUARD_TEST = "test_unit_набор_не_меняет_окружение"

# Снимок «до» (pytest_configure) и след по модулям, оставившим окружение.
STATE: dict = {"guarded": GUARDED, "baseline": {}, "drift": {}}


def snapshot() -> dict[str, str | None]:
    """Текущие значения сторожевых переменных (отсутствие — `None`)."""
    return {key: os.environ.get(key) for key in GUARDED}


def drift(before: dict[str, str | None],
          after: dict[str, str | None]) -> dict[str, tuple[str | None, str | None]]:
    """Что разошлось: `{ключ: (было, стало)}`."""
    return {key: (before[key], after[key]) for key in GUARDED if before[key] != after[key]}


def pytest_configure(config: pytest.Config) -> None:
    """Снимок окружения до первого unit-теста — с ним сверяется сторож в конце."""
    STATE["baseline"] = snapshot()


@pytest.fixture(scope="session")
def unit_env_guard() -> dict:
    """Состояние сторожа для теста `test_env_hygiene`: снимок «до» и кто что оставил."""
    return STATE


@pytest.fixture(scope="module", autouse=True)
def _unit_env_watch(request: pytest.FixtureRequest) -> Iterator[None]:
    """Снимок до/после каждого unit-модуля: видно, кто именно оставил окружение."""
    before = snapshot()
    yield
    changed = drift(before, snapshot())
    if changed:
        STATE["drift"][request.module.__name__] = changed


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Сторож идёт последним в unit-разделе: он смотрит итог всех unit-модулей."""
    guard = [item for item in items if item.name == GUARD_TEST]
    if not guard:
        return
    rest = [item for item in items if item not in guard]
    anchor = 0
    for index, item in enumerate(rest):
        if HERE in pathlib.Path(str(item.fspath)).resolve().parents:
            anchor = index + 1
    items[:] = rest[:anchor] + guard + rest[anchor:]
