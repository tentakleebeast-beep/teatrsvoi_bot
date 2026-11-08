"""Роль «педагог» в «Поиске окон»: дисциплина тренинга — из данных, а не из памяти (Э1, Г14).

Критерий карты Э1: «Given мастер с одной дисциплиной, When открывает «Поиск окон» и
выбирает «Тренинг», Then дисциплина подставлена без выбора и уходит в `/webapp/preview`
полем `discipline`; с двумя дисциплинами — показывается выбор; Given администратор,
When тот же экран, Then селектора дисциплин нет».

Почему браузером, а не чтением разметки: `misc_slots.py` собирает раздел скриптом
(`drawDiscipline`) по ответу `/webapp/slots/people`, а подставляет выбранный код в запрос
в `assign()`. Тест на `my_disciplines` в ответе API (tests/integration/test_master_role.py)
держит серверную половину, но ничего не говорит о том, что экран этот ответ увидел,
нарисовал чипы и отправил код на сервер. Здесь проверяется именно сквозной путь.

Роль на стенде: заголовок `X-SVOI-My-Disciplines` (см. `tests/webapp/server.py`).
Пустой заголовок — администратор/организатор: селектора нет, поведение прежнее.

Спека роли: docs/architecture/140-master-role.md (Э1, критерий Г14).
"""

from __future__ import annotations

MASTER_ONE = {"X-SVOI-My-Disciplines": "music"}
MASTER_TWO = {"X-SVOI-My-Disciplines": "music,acting"}


def _previews(page) -> list[dict]:
    captured: list[dict] = []
    page.on("request", lambda request: captured.append(request.post_data_json)
            if request.url.endswith("/webapp/preview") else None)
    return captured


def _assign_from_first_window(page) -> None:
    page.locator("#people .ui-row").first.click()
    page.get_by_role("button", name="Найти окна").click()
    page.wait_for_selector("#windows .ui-card")
    page.locator("#windows .ui-card").first.get_by_role("button", name="Назначить").click()
    page.locator(".ui-sheet .ui-btn--primary").click()
    page.wait_for_selector(".ui-toast")


def test_одна_дисциплина_подставлена_и_уходит_в_карточку(app_page) -> None:
    """Г14, docs/architecture/140-master-role.md: единственная дисциплина подставлена."""
    page = app_page("/webapp/slots", headers=MASTER_ONE)
    page.wait_for_selector("#kinds .ui-chip")

    # На репетиции дисциплины нет: раздел показывается только для тренинга.
    assert page.locator("#discipline-section").is_hidden()
    page.get_by_role("button", name="Тренинг").click()
    assert page.locator("#discipline-section").is_visible(), \
        "педагогу не показали дисциплину тренинга: выбор молча ушёл на сервер пустым"

    chips = page.locator("#disciplines .ui-chip")
    assert chips.count() == 1, "одна дисциплина — выбор из одного, а не список"
    assert chips.first.inner_text().strip() == "Музыка"
    assert page.locator("#disciplines .ui-chip--on").count() == 1, \
        "единственная дисциплина не выбрана заранее"

    previews = _previews(page)
    _assign_from_first_window(page)
    assert previews, "экран не собрал карточку назначения"
    assert previews[0]["kind"] == "training"
    assert previews[0].get("discipline") == "music", \
        f"код дисциплины не ушёл в /webapp/preview: {previews[0].get('discipline')!r}"
    assert not page.svoi_errors, page.svoi_errors


def test_две_дисциплины_дают_выбор_и_выбранная_уходит(app_page) -> None:
    """Г14, docs/architecture/140-master-role.md: две дисциплины — человек выбирает сам."""
    page = app_page("/webapp/slots", headers=MASTER_TWO)
    page.wait_for_selector("#kinds .ui-chip")
    page.get_by_role("button", name="Тренинг").click()

    chips = page.locator("#disciplines .ui-chip")
    assert chips.count() == 2, "у педагога две дисциплины — экран обязан дать выбор"
    assert page.locator("#disciplines .ui-chip--on").count() == 0, \
        "при двух дисциплинах выбор не делается за человека"

    page.get_by_role("button", name="Актёрское мастерство").click()
    selected = page.locator("#disciplines .ui-chip--on")
    assert selected.count() == 1
    assert selected.inner_text().strip() == "Актёрское мастерство"

    previews = _previews(page)
    _assign_from_first_window(page)
    assert previews, "экран не собрал карточку назначения"
    assert previews[0].get("discipline") == "acting"
    assert not page.svoi_errors, page.svoi_errors


def test_у_администратора_селектора_дисциплин_нет(app_page) -> None:
    """Г14, docs/architecture/140-master-role.md: у администратора поведение прежнее."""
    page = app_page("/webapp/slots")            # my_disciplines пуст — админ/организатор
    page.wait_for_selector("#kinds .ui-chip")
    page.get_by_role("button", name="Тренинг").click()

    assert page.locator("#discipline-section").is_hidden(), \
        "администратору показали выбор дисциплин педагога"

    previews = _previews(page)
    _assign_from_first_window(page)
    assert previews, "экран не собрал карточку назначения"
    assert previews[0].get("discipline") is None, \
        "у администратора в карточку уехала чужая дисциплина"
    assert not page.svoi_errors, page.svoi_errors
