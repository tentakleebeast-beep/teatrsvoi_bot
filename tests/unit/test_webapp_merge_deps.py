"""Зависимости, поднятые при слиянии main: файлы, конверт ошибок, очередь обращений.

main принёс файлы/хранилище, каталог ошибок API и рефакторинг мёртвого кода. Ниже —
то, что проверяется именно на стыке: новая фича не должна остаться без входа,
а экраны не должны разучиться читать ответы API.

Правила и требования раздела: docs/reports/102-webapp-merge-with-shell.md.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.api import webapp_api  # noqa: E402
from app.pages import admin, files, home, routes  # noqa: E402

# очередь обращений — домен админки (этап B, карта t_135f65cd)
MAIN = (ROOT / "services" / "tool-api" / "app" / "api" / "admin.py").read_text(encoding="utf-8")


def test_файлы_есть_вход_в_мини_апп():
    """Строку «Файлы студии» отдаёт `/webapp/home/data` — и только администратору.

    Страница — публичная константа (05b7d2b): права на момент отдачи HTML неизвестны,
    поэтому строка едет данными в `more.personal`, а рисует её скрипт страницы.
    """
    assert webapp_api.personal_rows(["admin.panel"]) == [
        {"title": "Файлы студии", "meta": "записи, фонограммы, тексты",
         "url": "/webapp/files"}]
    # Заметки — личный раздел у всех (FR-F7): право `notes.write` есть у каждой роли,
    # а файлы по-прежнему открыты только администратору.
    assert webapp_api.personal_rows(["notes.write", "files.access"]) == [
        {"title": "Заметки", "meta": "личные и общие, с тегами", "url": "/webapp/notes"}]
    # В разметке строки файлов нет вовсе: её приносит ответ данных, а не страница.
    assert 'href="/webapp/files"' not in home.MORE_PAGE
    assert 'id="personal-list"' in home.MORE_PAGE
    assert "data.more.personal" in home.MORE_PAGE


def test_экраны_файлов_на_месте():
    page = files.FILES_PAGE
    assert "Файлы" in page and "/webapp/files/presign" in page
    router = routes.build_router(actor=lambda: {}, caps_for=lambda who: [])
    paths = {route.path for route in router.routes}
    assert {"/webapp/files", "/webapp/folder"} <= paths


def test_очередь_обращений_обслуживается_api():
    """Экран «Обращения» ходит в эти два адреса — они вернулись после слияния."""
    assert '"/webapp/admin/feedback"' in MAIN
    assert "/webapp/admin/feedback/decide" in MAIN
    assert "feedback_list(status=None, open_only=True, limit=50, who=who)" in MAIN


def test_решение_по_обращению_идёт_в_поле_resolution():
    """Контракт main: `resolution` и `source`, а не «note»."""
    page = admin.FEEDBACK_QUEUE_PAGE
    assert "resolution: resolution" in page
    assert 'source: "human"' in page


def test_экраны_читают_конверт_ошибок():
    """{code, title, detail} из docs/architecture/85 — иначе показывали бы «Не получилось»."""
    page = admin.FEEDBACK_QUEUE_PAGE
    assert "body.title" in page
    assert "body.code" in page
    assert "429" in page and "409" in page


def test_старые_адреса_ведут_на_новые():
    """Редиректы из первой жизни Mini App остаются (обратная совместимость)."""
    router = routes.build_router(actor=lambda: {}, caps_for=lambda who: [])
    paths = {route.path for route in router.routes}
    assert "/webapp/days" in paths and "/webapp/my/sheet" in paths
