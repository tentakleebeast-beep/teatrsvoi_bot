"""Страницы Mini App: помощники JS в одном месте, экраны — по модулям (N-1, R-9).

Обоснование: до 31.10.2026 `api()` жил пятью копиями (`pages/admin.py`,
`pages/legacy.py`, `pages/misc.py`, `pages/week.py` дважды), `auth()` — тремя.
Правка в одной копии не доезжала до остальных, а поведение на ошибках расходилось.
Теперь реализация одна — в `static/webapp-shell.js`, страница берёт `SVOI.api`.

Тест статический: он не поднимает приложение, а читает исходники. Так дешевле
поймать возврат копии, чем в браузере.

Правила и требования раздела: docs/reports/101-webapp-week-money-split.md.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

PAGES = ROOT / "services" / "tool-api" / "app" / "pages"
SHELL = ROOT / "services" / "tool-api" / "app" / "static" / "webapp-shell.js"

# Модули, которые карта разложила по разделам: каждый — не больше ~400 строк.
SPLIT_PREFIXES = ("admin", "misc", "money", "week")


def _sources() -> list[pathlib.Path]:
    return sorted(path for path in PAGES.glob("*.py") if path.name != "__init__.py")


def test_помощники_запроса_и_входа_живут_в_оболочке() -> None:
    """Одна реализация `api()`/`auth()` — в static, на страницах только ссылки."""
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for name in ("api", "auth", "enter"):
            if re.search(rf"\bfunction {name}\s*\(", text):
                offenders.append(f"{path.name}: function {name}()")
    assert not offenders, "на страницах снова свои копии: " + ", ".join(offenders)

    shell = SHELL.read_text(encoding="utf-8")
    assert "function api(" in shell and "function auth(" in shell and "function enter(" in shell
    assert "api: api" in shell, "оболочка не отдаёт api страницам"


def test_страницы_не_держат_свой_токен() -> None:
    """Источник токена один — оболочка (`SVOI.token`), на страницах имени нет.

    Дефект, который не ловит проверка `function api`: `pages/files.py` звал
    `token` из старого ядра `admin.py`. После разложения ядра имя исчезло, и
    кнопка «Скачать архивом» падала с ReferenceError до запроса — заголовок
    Authorization подставляет оболочка, странице он не нужен.
    """
    offenders = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\btoken\b", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, "страница снова держит своё имя токена: " + ", ".join(offenders)


def test_отказ_во_входе_печатается_словами_сервера() -> None:
    """`auth()` отдаёт причину отказа; экран печатает её, а не общий текст.

    До правки страницы звали `enter()` (boolean) и на отказе писали своё
    «Нет доступа. Откройте раздел из бота.» — слова сервера терялись.
    """
    for path in _sources():
        assert "SVOI.enter" not in path.read_text(encoding="utf-8"), (
            f"{path.name}: снова boolean-вход вместо `auth()` с причиной")

    for name in ("admin_archive.py", "admin_audit.py", "admin_feedback.py",
                 "admin_invites.py", "admin_pending.py", "admin_people.py",
                 "admin_rights.py"):
        text = (PAGES / name).read_text(encoding="utf-8")
        assert "auth().then(function (denied)" in text, f"{name}: причина отказа не берётся"
        assert "status(denied, true)" in text, f"{name}: причина отказа не печатается"

    for name in ("files.py", "week_template.py", "week_summary.py"):
        # Скрипт недели разрезан на смысловые части (`week_marks.py` +
        # `week_template.py`), поэтому вход по правам живёт во второй части.
        text = (PAGES / name).read_text(encoding="utf-8")
        assert "await auth()" in text or "auth().then(function (denied)" in text, name


def test_разделы_admin_и_misc_разложены_по_модулям() -> None:
    """Каждый раздел — свой модуль; монолит на 1251/1326 строк не возвращается."""
    for name, limit in (("admin.py", 60), ("misc.py", 60)):
        lines = len((PAGES / name).read_text(encoding="utf-8").splitlines())
        assert lines <= limit, f"{name}: снова монолит ({lines} строк)"

    for path in _sources():
        if not path.name.startswith(SPLIT_PREFIXES):
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 400, f"{path.name}: {lines} строк — раздел не разделён"


def test_каждый_модуль_страниц_не_больше_400_строк() -> None:
    """Порог ~400 строк — для всех экранов, а не только разрезанных разделов.

    До 31.10.2026 `week.py` (884) и `money.py` (886) оставались монолитами
    после разреза admin/misc — порог приёмки не выполнялся.
    """
    big = []
    for path in _sources():
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > 400:
            big.append(f"{path.name}: {lines}")
    assert not big, "модули страниц снова больше ~400 строк: " + ", ".join(big)


def test_экраны_по_прежним_именам_доступны() -> None:
    """Маршруты, стенд и тесты берут экраны из `pages.admin` / `pages.misc`."""
    from app.pages import admin, misc, money, week

    for name in ("PEOPLE_PAGE", "PENDING_PAGE", "RIGHTS_PAGE", "ARCHIVE_PAGE",
                 "AUDIT_PAGE", "INVITES_PAGE", "FEEDBACK_QUEUE_PAGE", "DUES_ADMIN_PAGE"):
        assert getattr(admin, name).startswith("<!doctype html>"), name
    for name in ("MONEY_PAGE", "TX_PAGE"):
        assert getattr(money, name).startswith("<!doctype html>"), name
    for name in ("WEEK_PAGE", "SUMMARY_PAGE"):
        assert getattr(week, name).startswith("<!doctype html>"), name
    for name in ("DUES_PAGE", "NOTIFICATIONS_PAGE", "FEEDBACK_PAGE", "BROADCAST_PAGE",
                 "RECONCILE_PAGE", "SLOTS_PAGE", "PROFILE_PAGE"):
        assert getattr(misc, name).startswith("<!doctype html>"), name
