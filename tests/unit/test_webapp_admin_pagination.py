"""Админские экраны Mini App: длинные списки листаются, код копируется нажатием.

Раньше это была одна страница с таблицей участников и пагинацией по 10 строк,
а подписи брались из `config/ui-texts.yaml` через `window.UI_TEXTS`. После
переработки (docs/development/92 §5) каждый раздел — свой экран, подписи живут
в разметке, а требования прежние: список листается по 20 строк, код приглашения
копируется нажатием, горизонтального скролла и таблиц нет.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

from app.pages import admin  # noqa: E402


def test_люди_листаются_по_страницам() -> None:
    page = admin.PEOPLE_PAGE
    assert "var shown = 20;" in page, "по 20 строк на страницу"
    assert "shown += 20" in page
    assert "renderPeople" in page
    assert "Показать ещё" in page


def test_аудит_листается_по_страницам() -> None:
    page = admin.AUDIT_PAGE
    assert "var shown = 20;" in page
    assert "shown += 20" in page
    assert "Показать ещё" in page


def test_код_копируется_нажатием() -> None:
    """Код приглашения — кнопка: нажал и он в буфере, руками не переписывают."""
    page = admin.INVITES_PAGE
    assert "navigator.clipboard.writeText" in page
    assert "Копировать" in page
    assert "/webapp/admin/invite" in page


def test_таблиц_и_горизонтального_скролла_нет() -> None:
    """Главный дефект прежней панели: таблица и лента вкладок уезжали вбок."""
    for page in (admin.PEOPLE_PAGE, admin.PENDING_PAGE, admin.RIGHTS_PAGE,
                 admin.ARCHIVE_PAGE, admin.AUDIT_PAGE, admin.INVITES_PAGE,
                 admin.FEEDBACK_QUEUE_PAGE):
        assert "<table" not in page
        assert "overflow-x" not in page


def test_опасное_действие_называет_объект() -> None:
    """Отклонение заявки — с именем человека, а не «Вы уверены?»."""
    assert "Отклонить заявку: " in admin.PENDING_PAGE
