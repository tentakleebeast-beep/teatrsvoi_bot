"""Инвентарь действий: отчёт воспроизводится кодом (docs/reports/117).

Обоснование: INT-018, docs/reports/117-actions-inventory.md. Проверяем главное
свойство инвентаря — он не мнение, а результат разбора файлов: число маршрутов
совпадает с `contracts/openapi.json`, отчёт в `docs/` равен тому, что даёт скрипт,
а списки «без права» и «никто не зовёт» не расходятся с маршрутами.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inventory():
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_actions as mod

    return mod, mod.build()


def test_route_count_matches_openapi_snapshot(inventory):
    """Критерий приёмки 2: число маршрутов в таблице равно контракту, без расхождений."""
    mod, data = inventory
    assert data["counts"]["openapi_ops"] == data["counts"]["code_ops"]
    assert data["missing_in_code"] == []
    assert data["missing_in_snapshot"] == []
    assert len({(route.method, route.path) for route in data["routes"] if route.in_schema}) \
        == data["counts"]["openapi_ops"]
    assert mod.check(mod.report_text(data), data) == []


def test_report_file_is_generated_from_code(inventory):
    """Критерий приёмки 1: таблица в отчёте — результат команды `--write`."""
    mod, data = inventory
    report = mod.REPORT.read_text(encoding="utf-8")
    assert mod.BEGIN in report and mod.END in report
    assert report == mod.merge_report(mod.report_text(data))


def test_every_action_states_right_and_test(inventory):
    """Критерий приёмки 3: у действия есть право (или честное «—») и отметка о тесте."""
    _, data = inventory
    for route in data["routes"]:
        assert route.right_text, f"{route.method} {route.path}: право не указано"
        assert isinstance(route.tests, set), f"{route.method} {route.path}: нет отметки о тесте"


def test_no_right_list_is_exactly_routes_without_any_check(inventory):
    """Критерий приёмки 4: в «без права» ровно те маршруты, где нет ни одного пути проверки.

    Сервисный Bearer-токен и `core._webapp_require` в теле — такая же проверка на входе,
    поэтому маршруты с ними в список «без права» не попадают.
    """
    _, data = inventory
    expected = {(route.method, route.path) for route in data["routes"]
                if not route.rights and not route.service_token and not route.body_caps}
    actual = {(route.method, route.path) for route in data["no_right"]}
    assert actual == expected


def test_wildcard_list_only_hand_written_checks(inventory):
    """Критерий приёмки 4: право через «*» в теле — отдельный список с именем права."""
    _, data = inventory
    for route in data["wildcard"]:
        assert route.wildcard, f"{route.method} {route.path}: список без права"
        assert all("." in capability for capability in route.wildcard)


def test_orphans_are_not_called_by_any_interface(inventory):
    """Критерий приёмки 5: у эндпоинта из списка сирот нет ни одного потребителя."""
    mod, data = inventory
    routes = data["routes"]
    refs = (mod.mini_app_refs() + mod.gateway_refs() + mod.script_refs())
    orphans = {(route.method, route.path) for route in data["orphans"]}
    assert orphans == {(route.method, route.path) for route in routes if not route.interfaces}
    for method, path in orphans:
        callers = [ref for ref in refs if mod.path_matches(path, ref.path)]
        assert callers == [], f"{method} {path}: на самом деле зовут {callers[:3]}"


def test_path_matching_is_segment_wise():
    """Адреса сравниваются посегментно: префикс не совпадение, параметр — совпадение."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_actions as mod

    assert mod.path_match("/tools/events/cancel", "/tools/events/cancel") == 1
    assert mod.path_match("/tools/events/cancel", "/tools/events/move") == 0
    assert mod.path_match("/webapp/admin", "/webapp/admin/t/people") == 0
    assert mod.path_match("/folders/{id}/zip", "/folders/{folder_id}/zip") == 1
    assert mod.path_match("/folders/{id}/zip", "/folders/{}/zip") == 1
    assert mod.path_match("/agent/actions/{id}/apply", "/agent/actions/{id}/{verb}") == 2


def test_js_paths_rebuild_concatenation_and_tell_call_from_link():
    """Вёрстка экранов склеивает адрес из строк — склейка и ссылка различаются."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_actions as mod

    call = mod.js_line_paths('  api("/webapp/folder/" + id + "/order", body)')
    assert call == [("/webapp/folder/{}/order", True)]
    link = mod.js_line_paths('<a class="ui-tile" href="/webapp/money">Деньги</a>')
    assert link == [("/webapp/money", False)]
    template = mod.js_line_paths("fetch(`/webapp/folder/${id}/zip`)")
    assert template == [("/webapp/folder/{}/zip", True)]


def test_chat_inventory_has_commands_and_callbacks(inventory):
    """Гейтвей в инвентаре: у команд и кнопок есть триггер, права и отметка о тесте."""
    _, data = inventory
    triggers = [action["trigger"] for action in data["chat"]]
    assert any("CommandStart" in trigger or "/start" in trigger for trigger in triggers)
    assert any("panel:root" in trigger for trigger in triggers)
    for action in data["chat"]:
        assert action["kind"] in {"кнопка", "сообщение", "ошибка"}
        assert action["where"].startswith("services/tg-gateway/app/routers/")
        assert isinstance(action["tests"], list)


def test_screens_are_resolved_to_page_code(inventory):
    """У каждого экрана раскрыт код страницы, и почти каждый зовёт данные.

    Без вызовов остаются справочные экраны (`/webapp/ui` — витрина компонентов,
    `/webapp/admin/t/audit` — лента событий приходит в разметке) и редиректы.
    """
    _, data = inventory
    assert data["screens"], "экранов не найдено"
    for screen in data["screens"]:
        assert screen["constant"] != "—", screen["path"]
    calling = [screen for screen in data["screens"] if screen["endpoints"]]
    assert len(calling) >= len(data["screens"]) - 4, "слишком много экранов без данных"


def test_agent_tools_are_read_only(inventory):
    """Реестр инструментов агента — только чтение (docs/development/108 §4)."""
    _, data = inventory
    assert data["agent_tools"]
    assert all(tool["read_only"] for tool in data["agent_tools"])


def _route(data, path):
    """Маршрут по пути: в отчёте одна строка на метод, для проверок хватает первой."""
    matches = [route for route in data["routes"] if route.path == path]
    assert matches, f"маршрута {path} нет в инвентаре"
    return matches[0]


def test_webapp_body_require_names_capability(inventory):
    """Право Mini App, проверенное в теле, названо в колонке «Право», а не «сессия».

    `core._webapp_require(who, "…")` — та же проверка, что `require()`, вызванная
    напрямую (app/core.py:720-733); без её разбора /webapp/files/list выглядел бы
    доступным любому участнику, а /webapp/tx/save — без budget.write.
    """
    _, data = inventory
    assert _route(data, "/webapp/tx/save").body_caps == ("budget.write",)
    assert "budget.write" in _route(data, "/webapp/tx/save").right_text
    files = _route(data, "/webapp/files/list")
    assert files.body_caps == ("admin.panel",)
    assert "admin.panel" in files.right_text
    assert len(data["body_caps"]) >= 28


def test_service_token_routes_are_not_reported_as_unchecked(inventory):
    """Сверка Bearer-токена сервиса — проверка на входе, а не «—».

    POST /admin/bootstrap создаёт администратора, GET /tools/participants/search ищет
    людей до входа: оба зовёт гейтвей по служебному токену, и в списке «без права»
    им не место.
    """
    _, data = inventory
    no_right = {(route.method, route.path) for route in data["no_right"]}
    for path in ("/admin/bootstrap", "/tools/participants/search"):
        route = _route(data, path)
        assert route.service_token, path
        assert route.right_text == "сервисный токен", route.right_text
        assert (route.method, path) not in no_right


def test_body_check_detectors_read_source():
    """Разбор исходника ловит и `_webapp_require`, и сверку токена — без запуска обработчика."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_actions as mod

    assert mod._body_capabilities('core._webapp_require(who, "budget.write")') == ("budget.write",)
    assert mod._body_capabilities('_webapp_require(who, "admin.panel")') == ("admin.panel",)
    assert mod._body_capabilities("return {'ok': True}") == ()

    token = 'hmac.compare_digest(authorization or "", f"Bearer {core.TOOL_API_TOKEN}")'
    assert mod._has_service_token(token)
    assert not mod._has_service_token("hmac.compare_digest(a, b)")


def test_chat_coverage_matches_token_not_substring():
    """Покрытие чата — токен действия, а не слово в любом месте тестового файла.

    Ревью 2: подстрочный поиск ключа `files` засчитывал `RISK.decide(..., files=[changed])`
    из tests/unit/test_kanban_risk_triggers.py как тест `cmd_files` (и так же `cmd_mkdir`,
    `cmd_zip`), из-за чего отчёт расходился с кодом на сведённом с main дереве.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_actions as mod

    texts = {
        "test_alien.py": "RISK.decide(card, files=[changed])\nARCHIVE.zips = 1\nfolders = []\n",
        "test_command.py": 'msg = Message(text="/files")',
        "test_filter.py": 'handler = Command("files")',
        "test_callback.py": 'assert callback.data.startswith("panel:root")',
    }
    assert mod._test_files_for("Command('files', texts.TITLE_FILES)", texts) == [
        "test_command.py", "test_filter.py",
    ]
    assert "test_alien.py" not in mod._test_files_for("Command('zip', 'архив')", texts)
    assert "test_alien.py" not in mod._test_files_for("Command('mkdir', 'папка')", texts)
    assert mod._test_files_for("F.data == 'panel:root'", texts) == ["test_callback.py"]


def test_orphan_review_covers_every_orphan(inventory):
    """Ревизия сирот (docs/reports/124, карты AUD-8 и Ш-14) не расходится с кодом.

    Таблица классификации обязана покрыть каждую сироту — иначе решение «по каждому
    маршруту» снова становится мнением. Проверка машинная и в обе стороны: строка с судьбой
    `—` обязана быть в текущем списке сирот, строка `потребитель` — уже нет (её зовёт
    интерфейс), строки `удалён`/`удалён ранее` не должны регистрироваться в приложении.
    Иначе таблица описывает вчерашний день: сирота без строки или строка без сироты.
    """
    _, data = inventory
    text = (ROOT / "docs" / "reports" / "130-orphan-review.md").read_text(encoding="utf-8")
    begin, end = "<!-- orphan-review:begin -->", "<!-- orphan-review:end -->"
    assert begin in text and end in text
    block = text.split(begin, 1)[1].split(end, 1)[0]

    fates = {"—", "токен", "удалён", "удалён ранее", "потребитель"}
    rows = {}
    for line in block.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue
        method, path, basket, fate, decision = cells
        key = (method, path.strip("`"))
        assert key not in rows, f"{key}: строка повторяется"
        assert basket in {"(а)", "(б)", "(в)"}, f"{key}: корзина {basket!r}"
        assert fate in fates, f"{key}: судьба {fate!r} вне словаря {sorted(fates)}"
        assert decision, f"{key}: решение не записано"
        rows[key] = (basket, fate)

    orphans = {(route.method, route.path) for route in data["orphans"]}
    routes = {(route.method, route.path) for route in data["routes"]}
    gone = {key for key, (_, fate) in rows.items() if fate in {"удалён", "удалён ранее"}}
    gained = {key for key, (_, fate) in rows.items() if fate == "потребитель"}
    waiting = {key for key, (_, fate) in rows.items() if fate not in
               {"удалён", "удалён ранее", "потребитель"}}

    assert ("POST", "/tools/calendar/event") in gone, "дубль С1 не помечен удалённым"
    assert not (gone & routes), f"удалённые маршруты всё ещё в приложении: {sorted(gone & routes)}"
    assert not (gained & orphans), f"строка-потребитель без потребителя: {sorted(gained & orphans)}"
    assert gained <= routes, f"строка-потребитель без маршрута: {sorted(gained - routes)}"
    assert waiting == orphans, (f"таблица и код разошлись: лишние {sorted(waiting - orphans)}, "
                                f"не покрыты {sorted(orphans - waiting)}")


def test_report_lists_all_right_check_paths(inventory):
    """Отчёт называет все пути проверки права, а не только `core.require`.

    Ревью 1: утверждение «права проверяются двумя путями» было неверным — путей три,
    и §7 должен показывать оба вида проверок в теле плюс сервисный токен.
    """
    mod, data = inventory
    text = mod.report_text(data)
    for marker in ("Права проверяются тремя путями", "### 7a.", "### 7b.", "### 7c."):
        assert marker in text, marker
