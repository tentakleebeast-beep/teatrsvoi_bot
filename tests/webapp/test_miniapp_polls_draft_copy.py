"""Черновик и копия опроса в браузере: сохранение, возврат, копирование (E8-D5).

Проверяется замером, а не на глаз (`docs/development/166-poll-draft-copy.md` §3, §5):

* конструктор: опрос из двух вопросов с вариантами сохраняется одной кнопкой,
  в списке появляется одна строка «черновик», и она возвращает ровно набранное —
  вопросы, варианты, тему;
* черновик без вопросов: кнопка говорит словами, что дальше, и `save` не уходит;
* копия закрытого опроса: та же тема с суффиксом « — копия», то же число вопросов
  и те же подписи вариантов;
* копия идущего опроса: тоже черновик, ни одного `start`, число ответивших у
  источника не изменилось;
* 390 и 320 px, WebKit и Chromium: цели нажатия ≥ 44 px, вбок не едет, JS-ошибок нет.

Где стоит граница измерения. Стенд `tests/webapp/server.py` держит список опросов
статикой и не записывает черновик: `POST /webapp/polls/save` на нём отвечает
`poll_id`, но `GET /webapp/polls/edit/data` всё равно отдаёт заранее заготовленный
опрос. Поэтому запись и возврат проверяются так: настоящая страница отправляет
настоящий `save`, тест запоминает тело запроса и отвечает по форме `api/polls.py`
(`polls_save`/`polls_edit_data`/`polls_list`). Тем самым проверено то, что живёт в
браузере — состав запроса и отрисовка ответа; запись в базу и `poll_run` на копии
проверяются `tests/integration/test_polls_copy.py`, где есть соединение с БД.

Флейки в этих тестах уже были: `_choose` в `test_miniapp_polls_mine.py` считал
`ui-btn--primary`, а у `multi` этот класс носит и «Дальше» (b5d344b, 7f1e5f0).
Здесь состояние ожидается по конкретному признаку (текст строки, число строк,
атрибут `href`), а не по счётчику классов.

    SVOI_WEBAPP_TESTS=1 .venv/bin/python -m pytest \\
        tests/webapp/test_miniapp_polls_draft_copy.py -q --browser webkit
"""

from __future__ import annotations

import json

import pytest

TAP_MIN = 44
DRAFT_ID = 55                 # id, который «сервер» теста вернёт на первый `save`
DRAFT_TITLE = "Анкета организатору"

OVERFLOW = ("() => document.documentElement.scrollWidth "
            "- document.documentElement.clientWidth")

SMALL_TARGETS = """
(min) => {
  const found = [];
  document.querySelectorAll('a, button, input, select, textarea').forEach((node) => {
    if (node.closest('[hidden]')) { return; }
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') { return; }
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

COPY_NOTE = ("Копия сохранена как черновик. Адресаты не перенесены: "
             "выберите их заново.")

COPY_ENDPOINT = "/webapp/polls/copy"
SAVE_ENDPOINT = "/webapp/polls/save"
START_ENDPOINT = "/webapp/polls/start"
CLOSE_ENDPOINT = "/webapp/polls/close"


# --- помощники ---------------------------------------------------------------

def _watch_posts(page) -> list[dict]:
    """Адрес, тело и код ответа каждого POST: «ушёл ровно один `save`» — факт."""
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


def _open_question(page, index: int):
    """Открыть редактор вопроса по номеру в списке и дождаться полей."""
    page.locator("#questions .ui-row").nth(index).click()
    page.wait_for_selector("#editor:not([hidden])", timeout=10_000)
    return page


def _capture_copy(page, store: dict) -> None:
    """Пропустить `copy` через стенд и запомнить тело ответа.

    `expect_response(...).json()` не годится: к моменту возврата страница уже
    ушла в конструктор копии, и браузер тела ответа не отдаёт.
    """
    def handler(route):
        response = route.fetch()
        body = response.text()
        store["copy"] = json.loads(body)
        route.fulfill(status=response.status, content_type="application/json", body=body)
    page.route(f"**{COPY_ENDPOINT}", handler)


def _no_overflow(page) -> None:
    assert page.evaluate(OVERFLOW) <= 0, "экран переполнен по горизонтали"


def _tap_targets(page) -> None:
    small = page.evaluate(SMALL_TARGETS, TAP_MIN)
    assert not small, "целей нажатия меньше 44 px: " + "; ".join(small)


def _json_route(payload: dict, status: int = 200, on_request=None):
    """Ответ вместо стенда: та же форма, что у `api/polls.py`."""
    def handler(route):
        if on_request is not None:
            on_request(route.request)
        route.fulfill(status=status, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))
    return handler


def _edit_data_from(payload: dict, poll_id: int) -> dict:
    """Ответ `GET /webapp/polls/edit/data` по телу, которое прислал `save`.

    Коды вариантов и id вопросов на стенде не нужны клиенту, но форма повторяет
    `api/polls.py`: `poll` + `questions` + `can_edit`.
    """
    questions = []
    for index, item in enumerate(payload.get("questions") or []):
        questions.append({
            "id": 900 + index, "position": item.get("position") or index + 1,
            "text": item.get("text") or "", "kind": item.get("kind") or "single",
            "required": item.get("required", True),
            "free_text": item.get("free_text", False),
            "max_choices": item.get("max_choices"),
            "options": [{"position": number, "code": f"a{number}", "label": option["label"]}
                        for number, option in enumerate(item.get("options") or [], start=1)],
        })
    return {"ok": True,
            "poll": {"id": poll_id, "title": payload.get("title") or "",
                     "intro": payload.get("intro"), "status": "draft",
                     "anonymous": False, "closes_at": None},
            "questions": questions, "can_edit": True}


def _serve_draft(page, store: dict) -> None:
    """Маршруты черновика: `save` запоминается, `list` и `edit/data` отвечают по нему.

    Стенд не пишет черновик (см. docstring модуля), поэтому запись и возврат
    проверяются здесь. `list` отдаёт и идущий опрос 7: «второго опроса нет» — это
    ровно две строки, а не три.
    """
    def save(route):
        store["payload"] = json.loads(route.request.post_data or "{}")
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "poll_id": DRAFT_ID,
                                       "status": "draft"}))

    def poll_list(route):
        payload = store.get("payload") or {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "ok": True,
            "polls": [
                {"id": 7, "title": "Анкета организаторам", "status": "running",
                 "questions": 11, "invited": 14, "answered": 6,
                 "created_at": "2026-11-15T09:00:00"},
                {"id": DRAFT_ID, "title": payload.get("title") or DRAFT_TITLE,
                 "status": "draft", "questions": len(payload.get("questions") or []),
                 "invited": 0, "answered": 0, "created_at": "2026-11-16T09:00:00"},
            ]}, ensure_ascii=False))

    def edit_data(route):
        poll_id = int(route.request.url.split("poll_id=")[1].split("&")[0])
        if poll_id != DRAFT_ID:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(_edit_data_from(store.get("payload") or {},
                                                      DRAFT_ID), ensure_ascii=False))

    page.route(f"**{SAVE_ENDPOINT}", save)
    page.route("**/webapp/polls/list", poll_list)
    page.route("**/webapp/polls/edit/data?*", edit_data)


# --- 1. черновик сохраняется и возвращается ---------------------------------

def test_черновик_из_двух_вопросов_возвращается_из_списка(app_page) -> None:
    """§5 AC-1/AC-2/AC-10/AC-12: `save` несёт набранное, список показывает черновик.

    Проверяется замером: тело запроса (два вопроса, варианты первого), одна строка
    черновика в списке, и после возврата — те же тексты и те же подписи вариантов.
    """
    page = app_page("/webapp/polls/edit")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    _fill_question(page, "Какие файлы открываете утром?", "single",
                   "Расписание студии\nЗвонки")
    page.click("#add-question")
    _fill_question(page, "Что улучшить в расписании?", "text")
    page.fill("#title", DRAFT_TITLE)
    assert page.locator("#questions .ui-row").count() == 2

    store: dict = {}
    _serve_draft(page, store)
    calls = _watch_posts(page)

    page.click("#save-draft")
    page.wait_for_selector("#note:not([hidden])", timeout=10_000)
    assert page.inner_text("#note") == "Черновик сохранён"
    assert page.locator("#note").evaluate("n => n.classList.contains('ui-alert--ok')")

    saves = _sent(calls, SAVE_ENDPOINT)
    assert len(saves) == 1, [call["url"] for call in calls]
    body = json.loads(saves[0]["body"])
    assert body.get("poll_id") is None, "новый черновик пришёл с чужим poll_id"
    assert [item["text"] for item in body["questions"]] == [
        "Какие файлы открываете утром?", "Что улучшить в расписании?"]
    assert body["questions"][0]["kind"] == "single"
    assert [option["label"] for option in body["questions"][0]["options"]] == [
        "Расписание студии", "Звонки"]
    assert body["questions"][1]["kind"] == "text"
    assert not _sent(calls, START_ENDPOINT), "сохранение ушло в отправку"

    # Уходим в список и возвращаемся: строка черновика одна, она ведёт в конструктор.
    page.click("a.ui-appbar__back")
    page.wait_for_url("**/webapp/polls", timeout=10_000)
    page.wait_for_selector("#polls .ui-row", timeout=10_000)
    assert page.locator("#polls .ui-row").count() == 2, "в списке появился второй опрос"
    draft = page.locator(f"#polls a.ui-row[href='/webapp/polls/edit?poll_id={DRAFT_ID}']")
    assert draft.count() == 1, "строки сохранённого черновика нет в списке"
    text = draft.inner_text()
    assert DRAFT_TITLE in text and "2 вопроса" in text and "черновик" in text, text
    assert page.locator("#polls a.ui-row").count() == 2

    draft.click()
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    assert page.input_value("#title") == DRAFT_TITLE
    rows = page.locator("#questions .ui-row")
    assert rows.count() == 2
    assert "Какие файлы открываете утром?" in rows.nth(0).inner_text()
    assert "Что улучшить в расписании?" in rows.nth(1).inner_text()
    _open_question(page, 0)
    assert page.input_value("#q-options") == "Расписание студии\nЗвонки"
    page.click("#q-done")
    page.wait_for_selector("#editor", state="hidden", timeout=10_000)
    _no_overflow(page)
    assert not page.svoi_errors, page.svoi_errors


# --- 2. черновик без вопросов -----------------------------------------------

def test_черновик_без_вопросов_говорит_словами_и_не_создаётся(app_page) -> None:
    """§3.1: пустой опрос — текстом «В опросе нет ни одного вопроса.», без `save`."""
    page = app_page("/webapp/polls/edit")
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    # Новый опрос начинается с одного пустого вопроса: убираем его — получаем пустоту.
    _open_question(page, 0)
    page.click("#q-delete")
    page.wait_for_function(
        "() => document.querySelectorAll('#questions .ui-row').length === 0", timeout=10_000)
    page.fill("#title", "Пустой черновик")

    calls = _watch_posts(page)
    page.click("#save-draft")
    page.wait_for_selector("#error:not([hidden])", timeout=10_000)
    assert page.inner_text("#error") == "В опросе нет ни одного вопроса."
    assert not _sent(calls, SAVE_ENDPOINT), "пустой опрос всё-таки ушёл на сервер"
    assert not _sent(calls, START_ENDPOINT)
    assert page.input_value("#title") == "Пустой черновик", "ввод потерян"
    assert page.locator("#step-questions").is_visible()
    assert not page.svoi_errors, page.svoi_errors


# --- 3. копия закрытого опроса ----------------------------------------------

def test_копия_закрытого_опроса_держит_вопросы_и_подписи_вариантов(app_page) -> None:
    """§3.3, §5 AC-34: «Копировать» на закрытом — конструктор копии с суффиксом §3.

    Число вопросов и подписи вариантов сверяются с источником, а не с числом из
    стенда: суффикс — тема источника плюс « — копия» (`api/polls.py` `polls_copy`).
    """
    # Тема источника — та, что видит конструктор; суффикс §3 проверяется от неё,
    # а не от константы стенда.
    source = app_page("/webapp/polls/edit?poll_id=10")
    source.wait_for_selector("#questions .ui-row", timeout=10_000)
    source_title = source.input_value("#title")

    page = app_page("/webapp/polls/results?poll_id=10")
    page.wait_for_selector("#questions details", timeout=10_000)
    assert page.inner_text("#head-note").startswith("опрос закрыт"), \
        page.inner_text("#head-note")
    source_questions = page.locator("#questions details").count()
    assert source_questions == 11
    page.locator("#questions details").nth(0).locator("summary").click()
    source_options = page.eval_on_selector_all(
        "#questions details:nth-of-type(1) .ui-list .ui-row__title",
        "nodes => nodes.map(node => node.textContent.trim())")
    assert source_options[:2] == ["Расписание студийской таблицы", "Календарь"]

    calls = _watch_posts(page)
    captured: dict = {}
    _capture_copy(page, captured)
    page.click("#copy")
    page.wait_for_url("**/webapp/polls/edit?poll_id=*note=copy", timeout=10_000)
    page.wait_for_selector("#questions .ui-row", timeout=10_000)

    payload = captured["copy"]
    assert payload["status"] == "draft", payload
    assert payload["url"].endswith("note=copy"), payload

    copies = _sent(calls, COPY_ENDPOINT)
    assert len(copies) == 1, [call["url"] for call in calls]
    assert json.loads(copies[0]["body"]) == {"poll_id": 10}
    assert page.inner_text("#note") == COPY_NOTE
    assert page.input_value("#title") == f"{source_title} — копия"
    assert page.locator("#questions .ui-row").count() == source_questions

    _open_question(page, 0)
    assert page.input_value("#q-options").split("\n") == source_options
    page.click("#q-done")
    page.wait_for_selector("#editor", state="hidden", timeout=10_000)
    assert "Опрос уже отправлен" not in page.inner_text("body")
    assert page.is_enabled("#save-draft")
    _no_overflow(page)
    assert not page.svoi_errors, page.svoi_errors


# --- 4. копия идущего опроса: черновик и ни одного запуска -------------------

def test_копия_идущего_опроса_не_запускает_источник_и_числится_черновиком(app_page) -> None:
    """§5 AC-17/AC-33/AC-34: копия идущего — `draft`, ни `start`, ни `close`.

    Число ответивших у источника читается до и после; ноль `start` — факт из
    журнала запросов. Отсутствие `poll_run` в базе проверяет
    `tests/integration/test_polls_copy.py` — браузер до базы не достаёт.
    """
    before = app_page("/webapp/polls/results?poll_id=7")
    before.wait_for_selector("#questions details", timeout=10_000)
    answered_before = before.inner_text("#answered")
    assert answered_before == "6 из 14", answered_before
    question_count = before.locator("#questions details").count()

    calls = _watch_posts(before)
    captured: dict = {}
    _capture_copy(before, captured)
    before.click("#copy")
    before.wait_for_url("**/webapp/polls/edit?poll_id=*note=copy", timeout=10_000)
    before.wait_for_selector("#questions .ui-row", timeout=10_000)
    payload = captured["copy"]
    assert payload["status"] == "draft", payload
    assert payload["title"].endswith(" — копия"), payload
    assert before.locator("#questions .ui-row").count() == question_count
    assert before.inner_text("#note") == COPY_NOTE
    assert before.is_enabled("#add-question"), "копия пришла замороженной, как отправленная"

    assert len(_sent(calls, COPY_ENDPOINT)) == 1
    assert not _sent(calls, START_ENDPOINT), "копирование отправило опрос"
    assert not _sent(calls, CLOSE_ENDPOINT), "копирование закрыло источник"

    # Копия в списке — черновик: строка с её id ведёт в конструктор и говорит «черновик».
    copy_id = payload["poll_id"]

    def copy_list(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "ok": True,
            "polls": [
                {"id": 7, "title": "Анкета организаторам", "status": "running",
                 "questions": 11, "invited": 14, "answered": 6,
                 "created_at": "2026-11-15T09:00:00"},
                {"id": copy_id, "title": payload["title"], "status": "draft",
                 "questions": question_count, "invited": 0, "answered": 0,
                 "created_at": "2026-11-16T09:00:00"},
            ]}, ensure_ascii=False))

    listing = app_page("/webapp/polls")
    listing.route("**/webapp/polls/list", copy_list)
    listing.reload(wait_until="load")
    listing.wait_for_selector("#polls .ui-row", timeout=10_000)
    row = listing.locator(f"#polls a.ui-row[href='/webapp/polls/edit?poll_id={copy_id}']")
    assert row.count() == 1, "копии нет в списке как черновика"
    assert "черновик" in row.inner_text() and "идёт" not in row.inner_text()

    # Число ответивших у источника не изменилось.
    after = app_page("/webapp/polls/results?poll_id=7")
    after.wait_for_selector("#questions details", timeout=10_000)
    assert after.inner_text("#answered") == answered_before
    assert after.locator("#questions details").count() == question_count
    assert not after.svoi_errors, after.svoi_errors


# --- 5. размеры и движки -----------------------------------------------------

@pytest.mark.parametrize("width, height", ((390, 844), (320, 658)))
def test_копия_и_конструктор_на_двух_ширинах(app_page, width, height) -> None:
    """§3.4: на 390 и 320 px кнопки ≥ 44 px, вбок не едет, JS-ошибок нет."""
    page = app_page("/webapp/polls/results?poll_id=10")
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_selector("#questions details", timeout=10_000)
    _no_overflow(page)
    _tap_targets(page)
    assert not page.svoi_errors, (width, page.svoi_errors)

    page.click("#copy")
    page.wait_for_url("**/webapp/polls/edit?poll_id=*note=copy", timeout=10_000)
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    _no_overflow(page)
    _tap_targets(page)
    for button in ("#save-draft", "#to-audience"):
        height_px = page.locator(button).evaluate(
            "node => Math.round(node.getBoundingClientRect().height)")
        assert height_px >= TAP_MIN, f"{button} на {width} px: {height_px} px"
    assert not page.svoi_errors, (width, page.svoi_errors)


@pytest.mark.parametrize("device", ["iPhone 14", "Pixel 7"])
def test_черновик_и_копия_на_двух_движках(device_page, device) -> None:
    """WebKit (iPhone) и Chromium (Pixel): те же экраны, ни переполнения, ни ошибок.

    `device_page` пропускает профиль чужого движка, поэтому полный прогон гоняет
    тест дважды — по разу на каждом (`--browser webkit` и `--browser chromium`).
    """
    for path in ("/webapp/polls", "/webapp/polls/edit?poll_id=8",
                 "/webapp/polls/results?poll_id=10"):
        page = device_page(path, device)
        page.wait_for_timeout(600)
        assert page.evaluate(OVERFLOW) <= 0, f"{device}: переполнение на {path}"
        assert not page.svoi_errors, (device, path, page.svoi_errors)

    page = device_page("/webapp/polls/results?poll_id=10", device)
    page.wait_for_selector("#questions details", timeout=10_000)
    page.click("#copy")
    page.wait_for_url("**/webapp/polls/edit?poll_id=*note=copy", timeout=10_000)
    page.wait_for_selector("#questions .ui-row", timeout=10_000)
    assert page.inner_text("#note") == COPY_NOTE
    assert page.evaluate(OVERFLOW) <= 0, f"{device}: конструктор копии едет вбок"
    _tap_targets(page)
    assert not page.svoi_errors, (device, page.svoi_errors)
