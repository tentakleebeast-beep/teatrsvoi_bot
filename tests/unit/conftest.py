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

Отдельно (t_986edeac) unit-набор не читает настоящий `.env` из корня: скрипты
вроде `agent_cost` зовут `_cli.apply_env()` при импорте и иначе кладут
`TELEGRAM_TOKEN`/`ENV` в `os.environ` процесса. Здесь `_cli.ENV_FILE` указывает
на несуществующий файл — unit-тесты передают конфиг явно, а не из файла.
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

import _cli  # noqa: E402  (путь к scripts/ добавляет tests/conftest.py)

# Настоящий `.env` в корне — источник окружения integration-набора и стенда.
# Unit-тесты не должны его читать: импорт скрипта с `apply_env()` иначе пишет
# `TELEGRAM_TOKEN`/`ENV` в `os.environ` процесса (t_986edeac).
REAL_ENV_FILE = _cli.ENV_FILE
UNIT_ENV_FILE = HERE / "_нет_настоящего_env"
_cli.ENV_FILE = UNIT_ENV_FILE

# Переменные, которые читают integration-набор и стенд. Остальные настройки тесты
# передают явно (`configure(Settings(...))`), подменять окружение им не нужно.
GUARDED = ("DATABASE_URL", "TELEGRAM_TOKEN", "ENV")

GUARD_TEST = "test_unit_набор_не_меняет_окружение"

# Снимок «до» (ленивый, на первом unit-модуле), след по модулям и по импорту
# модулей при сборе тестов.
STATE: dict = {
    "guarded": GUARDED,
    "baseline": {},
    "drift": {},
    "collection_before": {},
    "collection_drift": {},
}


def snapshot() -> dict[str, str | None]:
    """Текущие значения сторожевых переменных (отсутствие — `None`)."""
    return {key: os.environ.get(key) for key in GUARDED}


def drift(before: dict[str, str | None],
          after: dict[str, str | None]) -> dict[str, tuple[str | None, str | None]]:
    """Что разошлось: `{ключ: (было, стало)}`."""
    return {key: (before[key], after[key]) for key in GUARDED if before[key] != after[key]}


def _ensure_baseline() -> None:
    """Снимок «до» берётся лениво — на первом unit-модуле, а не в `pytest_configure`.

    `pytest_configure` срабатывает до сбора тестов, а integration-модули на импорте
    читают `.env` (`load_dotenv`), и снимок, взятый раньше, делал сторож ложным:
    он видел «утечку» там, где integration-набор честно загрузил своё окружение.
    Утечки при импорте unit-модулей ловит отдельный трекинг сбора.
    """
    if not STATE["baseline"]:
        STATE["baseline"] = snapshot()


@pytest.fixture(scope="session")
def unit_env_guard() -> dict:
    """Состояние сторожа для теста `test_env_hygiene`: снимок «до» и кто что оставил."""
    return STATE


@pytest.fixture(scope="module", autouse=True)
def _unit_env_watch(request: pytest.FixtureRequest) -> Iterator[None]:
    """Снимок до/после каждого unit-модуля: видно, кто именно оставил окружение."""
    _ensure_baseline()
    before = snapshot()
    yield
    changed = drift(before, snapshot())
    if changed:
        STATE["drift"][request.module.__name__] = changed


def pytest_collectstart(collector: pytest.Collector) -> None:
    """Снимок до импорта unit-модуля: утечка при импорте тоже имеет виновника."""
    if isinstance(collector, pytest.Module):
        STATE["collection_before"][collector.nodeid] = snapshot()


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Сравнить окружение после импорта модуля и записать, кто оставил утечку."""
    before = STATE["collection_before"].pop(report.nodeid, None)
    if before is None:
        return
    changed = drift(before, snapshot())
    if changed:
        STATE["collection_drift"][report.nodeid] = changed


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """После сторожа возвращаем `_cli.ENV_FILE`: integration-набор читает настоящий `.env`."""
    if item.name == GUARD_TEST:
        _cli.ENV_FILE = REAL_ENV_FILE


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
