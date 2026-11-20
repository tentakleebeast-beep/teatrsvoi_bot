"""Сторож окружения unit-набора (урок L-048, карта t_1dfb94b6).

Совместный прогон `pytest tests/unit tests/integration` в одном процессе валится,
если unit-тест оставил в `os.environ` свой `DATABASE_URL`, `TELEGRAM_TOKEN` или `ENV`:
фикстура `test_db` (`tests/conftest.py`) читает DSN из окружения уже после unit-набора
и уходит на чужую базу (было 786 ERROR `password authentication failed`).

Снимок «до» делает `tests/unit/conftest.py:pytest_configure`, «после» — этот тест;
conftest же ставит его последним в unit-разделе и копит, какой модуль что оставил.
"""

from __future__ import annotations

import os


def test_unit_набор_не_меняет_окружение(unit_env_guard):
    guarded = unit_env_guard["guarded"]
    before = unit_env_guard["baseline"]
    after = {key: os.environ.get(key) for key in guarded}
    changed = {key: (before.get(key), after[key]) for key in guarded
               if before.get(key) != after[key]}
    if not changed:
        return
    leaks = unit_env_guard["drift"]
    where = "; ".join(
        f"{module}: " + ", ".join(f"{key} {old!r} -> {new!r}" for key, (old, new) in keys.items())
        for module, keys in leaks.items()
    ) or "модуль не определён"
    raise AssertionError(
        "unit-набор изменил окружение, которое читают integration-тесты и стенд: "
        + ", ".join(f"{key} {old!r} -> {new!r}" for key, (old, new) in changed.items())
        + f" — оставил: {where}"
    )
