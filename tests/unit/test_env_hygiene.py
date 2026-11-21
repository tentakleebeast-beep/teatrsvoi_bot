"""Сторож окружения unit-набора (урок L-048, карты t_1dfb94b6, t_986edeac).

Совместный прогон `pytest tests/unit tests/integration` в одном процессе валится,
если unit-тест оставил в `os.environ` свой `DATABASE_URL`, `TELEGRAM_TOKEN` или `ENV`:
фикстура `test_db` (`tests/conftest.py`) читает DSN из окружения уже после unit-набора
и уходит на чужую базу (было 786 ERROR `password authentication failed`).

Снимок «до» берётся лениво на первом unit-модуле, «после» — этот тест; conftest же
ставит его последним в unit-разделе и копит, какой модуль что оставил. Утечка при
импорте модуля (например, скрипт зовёт `apply_env()` на уровне модуля) попадает
в отдельный след `collection_drift` — иначе виновника не назвать.
"""

from __future__ import annotations

import os


def _format(leaks: dict) -> str:
    return "; ".join(
        f"{module}: " + ", ".join(f"{key} {old!r} -> {new!r}" for key, (old, new) in keys.items())
        for module, keys in leaks.items()
    )


def test_unit_набор_не_меняет_окружение(unit_env_guard):
    guarded = unit_env_guard["guarded"]
    before = unit_env_guard["baseline"]
    after = {key: os.environ.get(key) for key in guarded}
    changed = {key: (before.get(key), after[key]) for key in guarded
               if before.get(key) != after[key]}
    leaks = {**unit_env_guard.get("collection_drift", {}), **unit_env_guard["drift"]}
    if not changed and not leaks:
        return
    where = _format(leaks) or "модуль не определён"
    details = []
    if changed:
        details.append(", ".join(f"{key} {old!r} -> {new!r}" for key, (old, new) in changed.items()))
    if leaks:
        details.append(f"оставил: {where}")
    raise AssertionError(
        "unit-набор изменил окружение, которое читают integration-тесты и стенд: "
        + "; ".join(details)
    )
