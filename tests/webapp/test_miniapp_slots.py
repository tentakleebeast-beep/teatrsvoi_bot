"""Поиск окон в Mini App: две области, вход из сводки и из чата (FR-D5, С6, З-? Ш-05).

Обоснование: FR-D5 (поиск окон в двух областях), план 126 Ш-05, docs/reports/124 §1
(П-10) и §3 (С6), docs/architecture/40 §4–§6.

Дефекты, которые сторожат тесты:

  * экран умел только репетицию: арендатору окно не найти без фразы в чате;
  * список людей висел и на аренде, где он не значит ничего;
  * вход со сводки не передавал даты показанной недели, и период выбирали заново.

Стенд: `tests/webapp/server.py` отвечает по `policy` из запроса — аренда даёт окна
зала («зал свободен», без людей), репетиция — окна по людям.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp -q --browser chromium
"""

from __future__ import annotations


def test_экран_знает_обе_области(app_page) -> None:
    page = app_page("/webapp/slots")
    page.wait_for_selector("#kinds .ui-chip")

    kinds = page.locator("#kinds .ui-chip")
    assert kinds.count() == 3, "тип события не выбрать: аренда недостижима с экрана"
    labels = [chip.inner_text().strip() for chip in kinds.all()]
    assert labels == ["Репетиция", "Тренинг", "Аренда зала"]
    assert page.locator("#kinds .ui-chip--on").inner_text().strip() == "Репетиция"
    assert not page.svoi_errors, page.svoi_errors


def test_аренда_прячет_людей_и_показывает_зал(app_page) -> None:
    page = app_page("/webapp/slots")
    page.wait_for_selector("#kinds .ui-chip")

    page.get_by_role("button", name="Аренда зала").click()
    assert page.locator("#people-section").is_hidden(), "на аренде список людей не нужен"
    assert "зал" in page.locator("#people-hint").inner_text().lower()

    page.get_by_role("button", name="Найти окна").click()
    page.wait_for_selector("#windows .ui-card")

    cards = page.locator("#windows .ui-card")
    assert cards.count() == 2, "стенд отдаёт два окна зала"
    assert "зал свободен" in cards.first.inner_text()
    assert "Назначить" not in page.locator("#windows").inner_text(), \
        "арендатору назначать нечего: зал сдаётся договором, а не репетицией"


def test_репетиция_показывает_людей_и_назначение(app_page) -> None:
    page = app_page("/webapp/slots")
    page.wait_for_selector("#people .ui-row")

    page.locator("#people .ui-row").first.click()          # обязателен
    page.get_by_role("button", name="Найти окна").click()
    page.wait_for_selector("#windows .ui-card")

    card = page.locator("#windows .ui-card").first
    assert "свободны" in card.inner_text()
    assert "не отметились" in card.inner_text(), "человек без отметок должен быть назван"
    assert "Назначить" in card.inner_text()


def test_вход_со_сводки_передаёт_даты_недели(app_page) -> None:
    """Дом один, вход — ссылка: период не выбирают заново (docs/architecture/40 §5)."""
    summary = app_page("/webapp/week/summary?start=2026-10-26")
    summary.wait_for_selector("#to-slots")
    href = summary.get_attribute("#to-slots", "href")
    assert href.startswith("/webapp/slots?from=2026-10-26")

    summary.locator("#to-slots").click()
    summary.wait_for_url("**/webapp/slots**")
    summary.wait_for_selector("#kinds .ui-chip")
    assert summary.input_value("#from") == "2026-10-26"
    assert summary.input_value("#to") == "2026-11-01", "даты недели не подставились"
    assert not summary.svoi_errors, summary.svoi_errors


def test_вход_из_чата_открывает_экран_на_нужных_критериях(app_page) -> None:
    """Кнопка чата ведёт в `/webapp/slots`; карточка события передаёт тип и людей."""
    page = app_page("/webapp/slots?kind=rental&participants=30")
    page.wait_for_selector("#kinds .ui-chip")

    assert page.locator("#kinds .ui-chip--on").inner_text().strip() == "Аренда зала"
    assert page.locator("#people-section").is_hidden()
    assert not page.svoi_errors, page.svoi_errors


def test_назначение_с_экрана_передаёт_выбранный_тип(app_page) -> None:
    """Тип из чипа едет в карточку назначения — один путь `events/assign` (С1).

    Дефект, который сторожит тест: экран всегда отправлял `kind: "rehearsal"`,
    и тренинг с экрана назначался репетицией — другой цвет, другие педагоги.
    """
    page = app_page("/webapp/slots")
    page.wait_for_selector("#people .ui-row")
    previews: list[dict] = []
    page.on("request", lambda request: previews.append(request.post_data_json)
            if request.url.endswith("/webapp/preview") else None)

    page.get_by_role("button", name="Тренинг").click()
    page.locator("#people .ui-row").first.click()
    page.get_by_role("button", name="Найти окна").click()
    page.wait_for_selector("#windows .ui-card")
    page.locator("#windows .ui-card").first.get_by_role("button", name="Назначить").click()
    page.locator(".ui-sheet .ui-btn--primary").click()        # подтверждение до записи
    page.wait_for_selector(".ui-toast")

    assert previews, "экран не собрал карточку назначения"
    assert previews[0]["kind"] == "training", "выбранный тип подменён репетицией"
    assert previews[0]["participants"], "состав окна не передан"
    assert not page.svoi_errors, page.svoi_errors


# Роль без права `schedule.slots` (труппа) — заголовок стенда, как в
# `test_miniapp_week_rights.py`: один стенд проверяет оба пути прав.
PARTICIPANT = {"X-SVOI-Role": "participant"}


def test_без_права_экран_говорит_о_праве_а_не_о_сети(app_page) -> None:
    """403 на список людей — «раздел не для вашей роли», а не «люди не загрузились».

    Карта t_f802d135: экран писал «Люди не загрузились. Потяните экран вниз» в ответ
    на 403, и человек тянул экран бесконечно. Принципы 91: 8 (ошибка говорит, что
    делать), 9 (пусто — не тупик), Б3 (роль видна словами); право `schedule.slots`
    у труппы отсутствует (docs/architecture/25 §9.2, С6).
    """
    page = app_page("/webapp/slots", headers=PARTICIPANT)
    page.wait_for_selector("#denied:not([hidden])", timeout=10_000)

    text = page.locator("#denied").inner_text()
    assert "организатор" in text.lower(), text
    assert "администратор" in text.lower(), text
    assert "Потяните экран вниз" not in page.inner_text("body"), \
        "403 подан как сбой сети: человек будет тянуть экран вниз бесконечно"
    assert "Люди не загрузились" not in page.inner_text("body")
    assert page.locator("#search-form").is_hidden(), "форма поиска без права — тупик"
    assert not page.svoi_errors, page.svoi_errors
