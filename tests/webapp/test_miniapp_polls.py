"""Экраны опросов в браузере: компактные строки, свёрнутый список, сводка, экспорт (E8).

Проверяется замером, а не на глаз (docs/development/162 §6 и §14.5):

* конструктор — строки 56 px, тип значением справа, в первом кадре 390×844 видно
  не меньше 8 вопросов из 11 (мокап `01b`, 1 255 px против 1 446);
* адресаты — люди свёрнуты до «в списке 8, показать всех», экран не растёт линейно
  по труппе;
* сводка на 11 вопросов — блок свёрнут до строки итога, без разворотов ≤ 1 000 px;
* целей нажатия меньше 44 px — 0, переполнения вбок — 0.

Обоснование: FR-G18, AC-15, `docs/development/162-bot-polls.md` §6, §14.5.
Движки: WebKit (iPhone) и Chromium (Pixel) — прогон дважды, как у остальных экранов.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest tests/webapp/test_miniapp_polls.py -q
"""

from __future__ import annotations

import pytest

TAP_MIN = 44

OVERFLOW = ("() => document.documentElement.scrollWidth "
            "- document.documentElement.clientWidth")

VISIBLE_QUESTIONS = """
() => {
  const frame = window.innerHeight;
  return Array.from(document.querySelectorAll('#questions .ui-row'))
    .filter((row) => row.getBoundingClientRect().bottom <= frame + 1).length;
}
"""

SMALL_TARGETS = """
(min) => {
  const found = [];
  document.querySelectorAll('a, button, input, select, textarea').forEach((node) => {
    if (node.closest('[hidden]')) { return; }
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) { return; }
    if (rect.height < min - 0.5) {
      found.push((node.textContent || node.id || node.tagName).trim().slice(0, 40) +
                 ': ' + Math.round(rect.height) + 'px');
    }
  });
  return found;
}
"""


def _no_overflow(page) -> None:
    assert page.evaluate(OVERFLOW) <= 0, "экран переполнен по горизонтали"


def _tap_targets(page) -> None:
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, "целей нажатия меньше 44 px: " + "; ".join(small)


def _height(page) -> int:
    return int(page.evaluate("() => document.documentElement.scrollHeight"))


def test_список_опросов_и_отказ_по_праву(app_page) -> None:
    """Админ и организатор видят список; роль без `poll.manage` — слова отказа, а не сбой."""
    page = app_page("/webapp/polls")
    page.wait_for_selector("#polls .ui-acc", timeout=10_000)
    assert page.locator("#polls .ui-list .ui-row").count() == 3
    text = page.inner_text("#polls")
    assert "Анкета организаторам" in text and "Активные" in text
    assert page.locator("#denied").is_hidden()
    _no_overflow(page)
    assert not page.svoi_errors, page.svoi_errors

    # V-088 (б): организатор ведёт опросы сам — отказа на его роли быть не должно.
    organizer = app_page("/webapp/polls", headers={"X-SVOI-Role": "org"})
    organizer.wait_for_selector("#polls .ui-acc", timeout=10_000)
    assert organizer.locator("#denied").is_hidden()
    assert not organizer.svoi_errors, organizer.svoi_errors

    denied = app_page("/webapp/polls", headers={"X-SVOI-Role": "master"})
    denied.wait_for_selector("#denied:not([hidden])", timeout=10_000)
    body = denied.inner_text("body")
    assert "Опросы ведут администратор и организатор" in body, body
    assert "Не удалось загрузить опросы" not in body, "403 подан как сбой сети"
    assert denied.locator("#form").is_hidden()


def test_список_разбит_на_три_группы(app_page) -> None:
    """Активные, черновики и архив — три раскрывающихся блока, а не один список.

    Активные и черновики раскрыты (это текущая работа), архив свёрнут: история и
    растягивала список. Раскрытие нативное — нажатие на заголовок группы.
    """
    page = app_page("/webapp/polls")
    page.wait_for_selector("#polls .ui-acc", timeout=10_000)

    titles = page.eval_on_selector_all(
        "#polls .ui-acc > summary .ui-row__title",
        "nodes => nodes.map(node => node.textContent.trim())")
    assert titles == ["Активные", "Черновики", "Архив"], titles

    opened = page.eval_on_selector_all("#polls .ui-acc", "nodes => nodes.map(n => n.open)")
    assert opened == [True, True, False], opened

    assert "Анкета организаторам" in page.inner_text("#polls-running")
    assert "Что улучшить в расписании" in page.inner_text("#polls-draft")
    # Свёрнутый архив отдаёт только заголовок: строки появляются после раскрытия.
    assert "Анкета после фестиваля" not in page.inner_text("#polls-closed")

    page.click("#polls-closed > summary")
    assert page.eval_on_selector("#polls-closed", "node => node.open") is True
    assert page.locator("#polls-closed .ui-list .ui-row").count() == 1
    assert "Анкета после фестиваля" in page.inner_text("#polls-closed")
    assert not page.svoi_errors, page.svoi_errors


def test_конструктор_без_poll_id_отказ_без_no_poll_manage(app_page) -> None:
    """Новый опрос: конструктор только с правом `poll.manage`, иначе отказ словами.

    Дефект D2 отчёта 163: `/webapp/polls/edit` без `poll_id` не проверял право вообще,
    конструктор открывался любой роли, а отказ приходил только при сохранении (403).
    Ожидание для `org` читаем из стенда: карта D1 (`t_d9dd65d7`) добавляет ему
    `poll.manage`, и тогда тот же тест должен ждать конструктор, а не отказ.
    """
    from tests.webapp.server import NO_POLL_MANAGE_ROLES

    allowed = app_page("/webapp/polls/edit")
    allowed.wait_for_selector("#questions .ui-row", timeout=10_000)
    assert allowed.locator("#denied").is_hidden()
    assert "Добавить вопрос" in allowed.inner_text("#form")

    for role in ("troupe", "master"):
        denied = app_page("/webapp/polls/edit", headers={"X-SVOI-Role": role})
        denied.wait_for_selector("#denied:not([hidden])", timeout=10_000)
        assert denied.locator("#form").is_hidden(), f"{role}: конструктор виден без права"
        body = denied.inner_text("body")
        assert "Опросы ведут администратор и организатор" in body, body
        assert "Не удалось загрузить" not in body, "403 подан как сбой сети"

    org = app_page("/webapp/polls/edit", headers={"X-SVOI-Role": "org"})
    if "org" in NO_POLL_MANAGE_ROLES:
        org.wait_for_selector("#denied:not([hidden])", timeout=10_000)
        assert org.locator("#form").is_hidden()
    else:
        org.wait_for_selector("#questions .ui-row", timeout=10_000)
        assert org.locator("#denied").is_hidden()


def test_конструктор_компактные_строки(app_page) -> None:
    """56 px, тип справа, 8 вопросов в первом кадре — как на мокапе `01b`."""
    page = app_page("/webapp/polls/edit?poll_id=7")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    rows = page.locator("#questions .ui-row")
    assert rows.count() == 11

    heights = page.evaluate(
        "() => Array.from(document.querySelectorAll('#questions .ui-row'))"
        ".map((row) => Math.round(row.getBoundingClientRect().height))")
    assert max(heights) <= 60, f"строка вопроса выше 56 px: {heights}"
    assert page.evaluate(VISIBLE_QUESTIONS) >= 8, "в первом кадре меньше 8 вопросов"
    assert _height(page) < 1_400, f"конструктор выше мокапа: {_height(page)} px"
    assert "один из списка" in page.inner_text("#questions")
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


def test_адресаты_свёрнуты_и_экран_не_растёт(app_page) -> None:
    """20 человек — в списке 8 и «показать всех»; высота не линейна по труппе."""
    page = app_page("/webapp/polls/edit?poll_id=7")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    page.click("#to-audience")
    page.wait_for_selector("#step-audience:not([hidden])", timeout=10_000)
    page.wait_for_function(
        "() => document.getElementById('audience-count').textContent.indexOf('Уйдёт') !== -1",
        timeout=10_000)

    assert page.locator("#people .ui-row").count() == 8
    assert "в списке 8 из 20" in page.inner_text("#people-all")
    height = _height(page)
    assert height <= 1_000, f"экран адресатов вырос: {height} px при 20 людях"
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors

    page.click("#people-all")
    assert page.locator("#people .ui-row").count() == 20


def test_сводка_свёрнута_и_экспорт(app_page) -> None:
    """11 вопросов — 11 свёрнутых блоков, документ без разворотов ≤ 1 000 px."""
    page = app_page("/webapp/polls/results?poll_id=7")
    page.wait_for_selector("#questions details", timeout=10_000)
    blocks = page.locator("#questions details")
    assert blocks.count() == 11
    opened = page.evaluate(
        "() => Array.from(document.querySelectorAll('#questions details'))"
        ".filter((node) => node.open).length")
    assert opened == 0, "блок вопроса раскрыт по умолчанию"

    height = _height(page)
    assert height <= 1_000, f"сводка на 11 вопросов выше 1 000 px: {height}"
    text = page.inner_text("#questions")
    assert "ответили 6" in text
    assert "выборов больше ответивших" in text

    # Экспорт больше не `href`: адрес готовит сервер, открывает браузер телефона.
    assert page.get_attribute("#csv", "href") == "#"
    assert page.get_attribute("#md", "href") == "#"
    assert "6 из 14" in page.inner_text("#head")
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, page.svoi_errors


def test_экспорт_открывает_файл_по_короткой_ссылке_и_не_уводит_страницу(app_page) -> None:
    """Обращение №44: «CSV»/«Markdown» дают ссылку с подписью и не заменяют экран.

    Голый `href` на `/webapp/polls/export` уходил верхним переходом в WKWebView,
    заголовка Authorization там нет — человек видел JSON 401 вместо файла.
    """
    page = app_page("/webapp/polls/results?poll_id=7")
    page.wait_for_selector("#questions details", timeout=10_000)
    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")
    page.click("#csv")
    page.wait_for_function("() => window.__opened && window.__opened.length > 0",
                           timeout=10_000)
    url = page.evaluate("() => window.__opened")
    assert "/webapp/polls/export" in url and "format=csv" in url, url
    assert "token=" in url, url
    assert page.url.split("?")[0].endswith("/webapp/polls/results"), page.url

    # По открытому адресу действительно приходит файл, а не JSON отказа.
    response = page.request.get(url)
    assert response.status == 200, response.text()
    assert "text/csv" in (response.headers.get("content-type") or "")
    assert "вопрос;тип;человек" in response.text()

    page.evaluate(
        "() => { window.__opened = '';"
        " window.Telegram.WebApp.openLink = function (url) { window.__opened = url; }; }")
    page.click("#md")
    page.wait_for_function("() => window.__opened && window.__opened.length > 0",
                           timeout=10_000)
    md_url = page.evaluate("() => window.__opened")
    assert "format=md" in md_url and "token=" in md_url, md_url
    md_file = page.request.get(md_url)
    assert md_file.status == 200, md_file.text()
    assert md_file.text().startswith("# Анкета организаторам")
    assert not page.svoi_errors, page.svoi_errors


@pytest.mark.parametrize("device", ["iPhone 14", "Pixel 7"])
def test_экраны_опроса_на_двух_движках(device_page, device) -> None:
    """Один и тот же экран на iPhone (WebKit) и Pixel (Chromium): нет переполнения.

    `device_page` пропускает профиль чужого движка, поэтому полный прогон гоняет
    тест дважды — по разу на каждом (`--browser webkit` и `--browser chromium`).
    """
    for path in ("/webapp/polls", "/webapp/polls/edit?poll_id=7",
                 "/webapp/polls/results?poll_id=7"):
        page = device_page(path, device)
        page.wait_for_timeout(600)
        assert page.evaluate(OVERFLOW) <= 0, f"{device}: переполнение на {path}"
        assert not page.svoi_errors, (device, path, page.svoi_errors)


# --- E8-D5: черновик с явным сохранением и копия опроса (docs/development/166 §5.1) --
# Формы стенда (`tests/webapp/server.py`): копия `poll_id=9`, черновик `poll_id=8`,
# закрытая сводка `poll_id=10`, отказы копирования по заголовку `X-SVOI-POLLS-COPY`.

CONFIRM_STUB = """
(answer) => {
  window.__confirmText = "";
  window.__confirmCalls = 0;
  window.localStorage.setItem("svoi.confirm.calls", "0");
  window.Telegram.WebApp.showConfirm = function (text, cb) {
    window.__confirmText = text;
    window.__confirmCalls += 1;
    window.localStorage.setItem("svoi.confirm.calls", String(window.__confirmCalls));
    if (cb) { cb(answer); }
  };
}
"""

COPY_NOTE = ("Копия сохранена как черновик. Адресаты не перенесены: "
             "выберите их заново.")
EXIT_TEXT = "Выйти без сохранения? Черновик не сохранён."


def _watch_posts(page) -> list[dict]:
    """Адрес и тело каждого POST: «ушёл ровно один `save`» проверяется фактом."""
    calls: list[dict] = []
    page.on("request", lambda request: calls.append(
        {"url": request.url, "body": request.post_data or ""})
        if request.method == "POST" else None)
    return calls


def _sent(calls: list[dict], suffix: str) -> list[dict]:
    return [call for call in calls if call["url"].split("?")[0].endswith(suffix)]


def _fill_question(page, text: str, kind: str = "text", options: str = "") -> None:
    """Заполнить открытый редактор вопроса и нажать «Готово»."""
    page.fill("#q-text", text)
    page.select_option("#q-kind", kind)
    if options:
        page.fill("#q-options", options)
    page.click("#q-done")


def _three_questions(page) -> None:
    """Тема и три вопроса нового опроса: первый редактор уже открыт страницей."""
    _fill_question(page, "Какие файлы открываете утром?", "single", "Расписание\nЗвонки")
    for text in ("Сколько часов уходит на согласование?", "Что улучшить?"):
        page.click("#add-question")
        _fill_question(page, text, "text")
    page.fill("#title", "Анкета организатору")


def test_сохранить_черновик_остаётся_в_конструкторе(app_page) -> None:
    """AC-1: «Сохранить черновик» — один `save`, ни одного `start`, полоса и тот же экран."""
    page = app_page("/webapp/polls/edit")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    _three_questions(page)
    calls = _watch_posts(page)

    page.click("#save-draft")
    page.wait_for_selector("#note:not([hidden])", timeout=10_000)

    assert page.inner_text("#note") == "Черновик сохранён"
    assert page.locator("#note").evaluate("n => n.classList.contains('ui-alert--ok')")
    assert page.locator("#step-questions").is_visible()
    assert page.locator("#step-audience").is_hidden()
    saves = _sent(calls, "/webapp/polls/save")
    assert len(saves) == 1, [call["url"] for call in calls]
    assert not _sent(calls, "/webapp/polls/start"), "уход в отправку без адресатов"
    assert page.locator("#questions .ui-row").count() == 3
    assert page.input_value("#title") == "Анкета организатору"
    assert not page.svoi_errors, page.svoi_errors


def test_повторное_сохранение_правит_тот_же_черновик(app_page) -> None:
    """AC-2: второй `save` несёт `poll_id` первого ответа, а не создаёт второй опрос."""
    import json

    page = app_page("/webapp/polls/edit")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    _three_questions(page)
    calls = _watch_posts(page)

    page.click("#save-draft")
    page.wait_for_selector("#note:not([hidden])", timeout=10_000)
    page.click("#add-question")
    _fill_question(page, "Что оставить как есть?", "text")
    page.click("#save-draft")
    page.wait_for_function(
        "() => document.querySelectorAll('#questions .ui-row').length === 4 && "
        "document.getElementById('note').textContent === 'Черновик сохранён'",
        timeout=10_000)

    saves = _sent(calls, "/webapp/polls/save")
    assert len(saves) == 2, saves
    first = json.loads(saves[0]["body"])
    second = json.loads(saves[1]["body"])
    assert first.get("poll_id") is None, first
    assert second.get("poll_id") is not None, second
    assert second["poll_id"] == 7, "стенд вернул poll_id — второй save обязан его повторить"
    assert len(second["questions"]) == 4
    assert not page.svoi_errors, page.svoi_errors


def test_дальше_ведёт_на_шаг_адресатов(app_page) -> None:
    """AC-3: «Дальше» сохраняет и открывает шаг адресатов; `#error` скрыт."""
    page = app_page("/webapp/polls/edit")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    _three_questions(page)
    calls = _watch_posts(page)

    page.click("#to-audience")
    page.wait_for_selector("#step-audience:not([hidden])", timeout=10_000)

    assert len(_sent(calls, "/webapp/polls/save")) == 1
    assert page.locator("#step-questions").is_hidden()
    assert page.locator("#error").is_hidden()
    assert not page.svoi_errors, page.svoi_errors


def test_выход_с_правками_спрашивает_и_не_теряет_ввод(app_page) -> None:
    """AC-4: «‹» с правками — диалог; «Отмена» остаётся, «Выйти» уходит в список."""
    page = app_page("/webapp/polls/edit?poll_id=8")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    page.click("#save-draft")
    page.wait_for_selector("#note:not([hidden])", timeout=10_000)
    page.fill("#title", "Что улучшить в расписании, правка")
    calls = _watch_posts(page)

    page.evaluate(CONFIRM_STUB, False)
    page.click("a.ui-appbar__back")
    assert page.evaluate("() => window.__confirmCalls") == 1, "диалога не было"
    assert page.evaluate("() => window.__confirmText") == EXIT_TEXT
    assert "/webapp/polls/edit" in page.url, page.url
    assert page.input_value("#title") == "Что улучшить в расписании, правка"
    assert not _sent(calls, "/webapp/polls/save"), "выход сохранил черновик сам"

    page.evaluate(CONFIRM_STUB, True)
    page.click("a.ui-appbar__back")
    page.wait_for_url("**/webapp/polls", timeout=10_000)
    assert page.url.split("?")[0].endswith("/webapp/polls"), page.url


def test_без_правок_выход_не_спрашивает(app_page) -> None:
    """AC-5: сразу после сохранения «‹» уходит без диалога."""
    page = app_page("/webapp/polls/edit?poll_id=8")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    page.click("#save-draft")
    page.wait_for_selector("#note:not([hidden])", timeout=10_000)

    page.evaluate(CONFIRM_STUB, False)
    page.click("a.ui-appbar__back")
    page.wait_for_url("**/webapp/polls", timeout=10_000)
    # Счётчик читаем из localStorage: новый документ списка прежние `window`-поля теряет.
    assert page.evaluate("() => window.localStorage.getItem('svoi.confirm.calls')") == "0"


def test_сохранение_без_темы_и_без_вопросов_отказывает_на_клиенте(app_page) -> None:
    """AC-6, AC-7: пустая тема и отсутствие вопросов — текстом, без запроса на сервер."""
    page = app_page("/webapp/polls/edit?poll_id=8")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    calls = _watch_posts(page)

    page.fill("#title", "")
    page.click("#save-draft")
    page.wait_for_selector("#error:not([hidden])", timeout=10_000)
    assert page.inner_text("#error") == "У опроса нет темы."
    assert not _sent(calls, "/webapp/polls/save")

    page.fill("#title", "Что улучшить в расписании")
    for _ in range(3):
        page.click("#questions .ui-row")
        page.wait_for_selector("#editor:not([hidden])", timeout=10_000)
        page.click("#q-delete")
    assert page.locator("#questions .ui-row").count() == 0
    page.click("#save-draft")
    page.wait_for_selector("#error:not([hidden])", timeout=10_000)
    assert page.inner_text("#error") == "В опросе нет ни одного вопроса."
    assert not _sent(calls, "/webapp/polls/save")
    assert not page.svoi_errors, page.svoi_errors


def test_подпись_под_темой_больше_не_обещает_автосохранение(app_page) -> None:
    """AC-9: обещание «черновик сохраняется сам» снято, вместо него — правда."""
    page = app_page("/webapp/polls/edit")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    hint = page.inner_text("#save-hint")
    assert hint == "Черновик сохранится, когда нажмёте «Сохранить черновик».", hint
    assert "Черновик сохраняется сам" not in page.content()


def test_строка_черновика_в_списке_не_имеет_своих_действий(app_page) -> None:
    """AC-11: «3 вопроса», ссылка в конструктор, внутри строки — ноль кнопок.

    Статус строкой больше не пишется: он и есть заголовок группы («Черновики»).
    """
    page = app_page("/webapp/polls")
    page.wait_for_selector("#polls .ui-row", timeout=10_000)

    row = page.locator("#polls a.ui-row[href='/webapp/polls/edit?poll_id=8']")
    assert row.count() == 1, "строка черновика не ведёт в конструктор"
    text = row.inner_text()
    assert "3 вопроса" in text, text
    assert "Черновики" in page.inner_text("#polls-draft > summary"), "строка вне группы"
    assert page.locator("#polls .ui-row button").count() == 0
    assert page.locator("#polls .ui-row a a").count() == 0


def test_строка_черновика_открывает_конструктор(app_page) -> None:
    """AC-12: нажатие на строку — конструктор того же опроса с тремя вопросами."""
    page = app_page("/webapp/polls")
    page.wait_for_selector("#polls .ui-row", timeout=10_000)
    page.click("#polls a.ui-row[href='/webapp/polls/edit?poll_id=8']")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)

    assert page.url.split("?")[0].endswith("/webapp/polls/edit"), page.url
    assert "poll_id=8" in page.url
    assert page.input_value("#title") == "Что улучшить в расписании"
    assert page.locator("#questions .ui-row").count() == 3
    assert page.is_enabled("#add-question")
    assert not page.svoi_errors, page.svoi_errors


def test_сводка_держит_копировать_второй_кнопкой(app_page) -> None:
    """AC-33: четыре элемента в порядке; у закрытого «Закрыть опрос» скрыта."""
    for path, closed in (("/webapp/polls/results?poll_id=7", False),
                         ("/webapp/polls/results?poll_id=10", True)):
        page = app_page(path)
        page.wait_for_selector("#questions details", timeout=10_000)
        buttons = page.eval_on_selector_all(
            "#form > .ui-actions--bottom > *",
            "nodes => nodes.map(node => node.textContent.trim())")
        assert buttons == ["Закрыть опрос", "Копировать", "Экспорт CSV",
                           "Экспорт Markdown"], buttons
        assert page.locator("#copy").is_visible()
        assert page.is_enabled("#copy")
        assert page.locator("#close").is_hidden() is closed, path
        _no_overflow(page)
        assert not page.svoi_errors, (path, page.svoi_errors)


def test_копирование_ведёт_в_конструктор_копии(app_page) -> None:
    """AC-34: один `copy` с `poll_id`, переход в копию, полоса и тема с суффиксом."""
    import json

    page = app_page("/webapp/polls/results?poll_id=7")
    page.wait_for_selector("#questions details", timeout=10_000)
    calls = _watch_posts(page)

    page.click("#copy")
    page.wait_for_url("**/webapp/polls/edit?poll_id=9&note=copy", timeout=10_000)
    page.wait_for_selector("#questions .ui-row", timeout=10_000)

    copies = _sent(calls, "/webapp/polls/copy")
    assert len(copies) == 1, [call["url"] for call in calls]
    assert json.loads(copies[0]["body"]) == {"poll_id": 7}
    assert page.inner_text("#note") == COPY_NOTE
    assert page.locator("#note").is_visible()
    assert page.input_value("#title").endswith(" — копия")
    assert page.locator("#questions .ui-row").count() == 11
    assert not page.svoi_errors, page.svoi_errors


def test_без_метки_копии_полосы_нет(app_page) -> None:
    """AC-35: прямая ссылка в черновик без `note=copy` полосу не показывает."""
    page = app_page("/webapp/polls/edit?poll_id=8")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    assert page.locator("#note").is_hidden()


def test_отказ_копирования_показывает_текст_и_не_уводит_страницу(app_page) -> None:
    """AC-36: пока ответа нет — кнопка `disabled`; отказ — текстом, кнопка снова жива."""
    page = app_page("/webapp/polls/results?poll_id=7",
                    headers={"X-SVOI-POLLS-COPY": "draft"})
    page.wait_for_selector("#questions details", timeout=10_000)

    page.click("#copy", no_wait_after=True)
    assert page.is_disabled("#copy"), "кнопка не заблокирована на время запроса"
    page.wait_for_selector("#error:not([hidden])", timeout=10_000)
    assert page.inner_text("#error") == "Черновик не копируют: откройте его в конструкторе."
    assert page.url.split("?")[0].endswith("/webapp/polls/results"), page.url
    assert page.is_enabled("#copy")
    assert not page.svoi_errors, page.svoi_errors
