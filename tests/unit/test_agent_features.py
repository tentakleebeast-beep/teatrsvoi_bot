"""Реестр функций агента: покрытие экранов и кнопок, права и честный список действий.

Карта t_09672cea, docs/development/108 §9. Тест держит три обещания:

  * у каждой поверхности Mini App и каждой кнопки чата есть запись в
    `config/agent-features.yaml` — новый экран или кнопка без записи роняют тест;
    кнопки ищутся не только в объявленных списках, но и в самих исходниках бота
    (`tests/unit/button_scan.py`), а подписи из данных объявляются в `scenarios`;
  * у каждой записи ровно один смысл: инструмент из закрытого реестра, пункт
    «Что умею» или причина «не для агента»;
  * список действий для человека строится по его правам, а запись предлагается
    только с подтверждением кнопкой (A-3).

База и сеть не нужны: реестр файловый, права подставляются словарём `who`.
"""

from __future__ import annotations

import pathlib

import button_scan
import gateway_modules
import pytest
import yaml
from app.agently import features, registry

ROOT = pathlib.Path(__file__).resolve().parents[2]
REGISTRY_FILE = ROOT / "config" / "agent-features.yaml"
MENU_PY = ROOT / "services" / "tg-gateway" / "app" / "routers" / "menu.py"
KEYBOARDS_PY = ROOT / "services" / "tg-gateway" / "app" / "keyboards.py"
BUTTON_SURFACES = ("menu", "quick", "panel", "chat")

ADMIN = {"id": 1, "caps": ["*"]}
#: Актёр без бюджета студии: свои взносы, свои отметки, файлы — есть; бюджета — нет.
LIMITED = {"id": 2, "caps": ["budget.read.self", "schedule.availability.self"]}


# --------------------------------------------------------------- источники приложения
def _screen_paths() -> set[str]:
    """Все GET-маршруты Mini App: экраны и старые адреса-редиректы.

    Редиректы тоже в счёте: новый адрес без записи в реестре — такая же дыра,
    как новый экран, даже если он пока только перенаправляет.
    """
    from app.pages.routes import build_router  # noqa: PLC0415 — импорт тянет fastapi

    router = build_router(actor=lambda: {}, caps_for=lambda who: [])
    return {route.path for route in router.routes
            if "GET" in (route.methods or set()) and route.path.startswith("/webapp")}


def _button_sources() -> dict[str, set[str]]:
    """Подписи кнопок по поверхностям: меню, быстрые, админ-панель и чат.

    Кнопки чата берутся из двух объявленных списков: `keyboards.CHAT_BUTTONS`
    (постоянная клавиатура и служебные кнопки) и `routers.menu.CHAT_ACTION_BUTTONS`
    (/start, подсказка о вводе, фолбэк). Оба списка используются кодом бота,
    поэтому новая кнопка, добавленная туда без записи в реестре, роняет тест.
    """
    keyboards = gateway_modules.load("keyboards")
    menu_router = gateway_modules.load("routers.menu")
    return {
        "menu": set(keyboards.MENU_ACTIONS),
        "quick": set(keyboards.DIALOG_CONTROLS),
        "panel": {text for row in keyboards.panel_rows(["*"]) for text, _ in row},
        "chat": set(keyboards.CHAT_BUTTONS)
                | {text for text, _, _ in menu_router.CHAT_ACTION_BUTTONS},
    }


# ------------------------------------------------------------------------- покрытие
def test_каждый_экран_mini_app_есть_в_реестре() -> None:
    errors = features.check_coverage(_screen_paths(), {})
    assert not errors, "экраны без записи в реестре:\n  " + "\n  ".join(errors)


def test_каждая_кнопка_чата_есть_в_реестре() -> None:
    errors = features.check_coverage(set(), _button_sources())
    assert not errors, "кнопки без записи в реестре:\n  " + "\n  ".join(errors)


def test_покрытие_полное_и_обратное() -> None:
    """Записи в реестре не висят в воздухе: каждый источник существует в приложении."""
    screens = _screen_paths()
    buttons = _button_sources()
    scanned = button_scan.labels(_scanned())
    stale = sorted(features.screen_sources() - screens)
    for surface in BUTTON_SURFACES:
        known = buttons[surface] | (scanned if surface == "chat" else set())
        stale += sorted(features.button_sources(surface) - known)
    assert not stale, f"в реестре есть источники, которых нет в приложении: {stale}"


def test_неизвестный_экран_роняет_проверку_покрытия() -> None:
    """Если в Mini App появился экран без записи — это ошибка, а не тишина."""
    errors = features.check_coverage({"/webapp/new-screen"}, {})
    assert errors == ["экран /webapp/new-screen есть в приложении, но не в реестре"]


def test_неизвестная_кнопка_роняет_проверку_покрытия() -> None:
    errors = features.check_coverage(set(), {"menu": {"Новая кнопка"}})
    assert errors == ["кнопка «Новая кнопка» на поверхности menu есть, но не в реестре"]


SCANNED_FILES = (MENU_PY, KEYBOARDS_PY)


def _scanned() -> list[button_scan.ScannedButton]:
    """Кнопки, найденные в самих исходниках бота, а не в объявленных списках."""
    return button_scan.scan(*SCANNED_FILES)


def test_каждая_кнопка_в_исходниках_есть_в_реестре() -> None:
    """Любая подпись кнопки в menu.py и keyboards.py обязана быть в реестре.

    Проверяется не список, а исходник: кнопка, собранная мимо `CHAT_BUTTONS`,
    литералом в обработчике или с вычисляемым колбэком `"form:" + kind`,
    тоже находится и требует записи.
    """
    registered: set[str] = set()
    for surface in BUTTON_SURFACES:
        registered |= features.button_sources(surface)
    missing = sorted(
        f"{button.file}:{button.line} «{button.label}»"
        for button in _scanned()
        if button.label is not None and button.label not in registered)
    assert not missing, ("кнопки в исходниках бота без записи в config/agent-features.yaml:\n  "
                         + "\n  ".join(missing))


def test_динамические_подписи_описаны_сценарием() -> None:
    """Непроверяемое построчно место обязано быть объявлено в разделе `scenarios`.

    Подпись из данных (имя человека, тип операции) в реестр построчно не записать —
    тогда место объявляется с причиной. Новое место без записи и снятая запись,
    которой больше нет в исходнике, одинаково роняют тест.
    """
    found = button_scan.unresolved(_scanned())
    declared = features.scenario_keys()
    assert found - declared == set(), (
        "подписи из данных без записи в разделе scenarios:\n  "
        + "\n  ".join(map(str, sorted(found - declared))))
    assert declared - found == set(), (
        "в scenarios есть места, которых нет в исходниках: "
        f"{sorted(declared - found)}")


def test_сторож_видит_кнопку_в_любом_виде(tmp_path: pathlib.Path) -> None:
    """Сторож не пустой: литерал, вычисляемый колбэк, мимо списка и `text=` находятся."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from aiogram.types import KeyboardButton\n"
        "from webapp_links import keyboard as webapp_keyboard\n"
        'BTN = "Новая кнопка клавиатуры"\n'
        "\n"
        "def handler(rows, kind, caps):\n"
        '    rows.append([("Новая кнопка раздела", "sar:new")])\n'
        '    rows.append([("Новая кнопка формы", "form:" + kind)])\n'
        '    rows.append([(name, "mi:" + name) for name in caps])\n'
        '    webapp_keyboard("Новый экран", "/webapp/new", "")\n'
        "    return [KeyboardButton(text=BTN)]\n",
        encoding="utf-8")
    found = button_scan.scan_file(probe)
    assert button_scan.labels(found) == {
        "Новая кнопка раздела", "Новая кнопка формы", "Новый экран",
        "Новая кнопка клавиатуры"}
    assert button_scan.unresolved(found) == {("probe.py", "handler", "row", "name")}


def test_сторож_видит_вычисляемый_колбэк_в_боевом_файле() -> None:
    """Кнопка «Изменить» собирается склейкой `"form:" + тип` — она в счёте."""
    assert "Изменить" in button_scan.labels(_scanned())


def test_сценарий_без_причины_отклоняется(tmp_path: pathlib.Path) -> None:
    path = _write_scenarios(tmp_path, [{
        "id": "s", "file": "menu.py", "function": "f", "kind": "row",
        "expr": "text", "reason": "  "}])
    with pytest.raises(features.RegistryError, match="причина"):
        features.load_scenarios(path)


def test_сценарий_с_чужим_файлом_отклоняется(tmp_path: pathlib.Path) -> None:
    path = _write_scenarios(tmp_path, [{
        "id": "s", "file": "other.py", "function": "f", "kind": "row",
        "expr": "text", "reason": "подпись приходит из данных"}])
    with pytest.raises(features.RegistryError, match="не из"):
        features.load_scenarios(path)


def test_сценарии_репозитория_читаются() -> None:
    assert len(features.scenarios()) >= 5
    assert all(len(scenario.reason) > 10 for scenario in features.scenarios())


# ------------------------------------------------------------------------- структура
def test_реестр_читается_и_без_дублей() -> None:
    entries = features.all_features()
    assert len(entries) >= 40, f"записей подозрительно мало: {len(entries)}"
    assert len({feature.id for feature in entries}) == len(entries)
    sources = [(feature.surface, feature.source) for feature in entries if feature.kind == "button"]
    assert len(set(sources)) == len(sources), "одна кнопка записана дважды"


def test_у_каждой_записи_ровно_один_смысл() -> None:
    tools = {tool.name for tool in registry.all_tools()}
    for feature in features.all_features():
        meanings = [bool(feature.tool), feature.capabilities, bool(feature.not_for_agent)]
        assert sum(meanings) == 1, f"{feature.id}: смыслов {meanings}"
        if feature.tool:
            assert feature.tool in tools, f"{feature.id}: инструмента «{feature.tool}» нет"
        if feature.not_for_agent:
            assert len(feature.not_for_agent) > 10, f"{feature.id}: причина — отписка"


def test_пункт_что_умею_один_и_без_инструмента() -> None:
    capability_entries = [feature for feature in features.all_features() if feature.capabilities]
    assert [feature.id for feature in capability_entries] == ["menu.capabilities"]
    assert capability_entries[0].tool is None


def test_пишущих_инструментов_в_реестре_нет() -> None:
    """Реестр только предлагает: запись не появляется ни у одной поверхности."""
    read_only = {tool.name: tool.read_only for tool in registry.all_tools()}
    for feature in features.action_features():
        assert read_only[feature.tool] is True, f"{feature.id}: пишущий инструмент {feature.tool}"
    assert [feature.id for feature in features.action_features()
            if feature.tool == "propose_budget_tx"]


# --------------------------------------------------------------- права и список действий
def test_администратор_видит_и_чтение_и_предложение_записи() -> None:
    answer = features.human_capabilities(ADMIN)
    assert "Записать" in answer and "Посмотреть" in answer
    assert "«Расход и доход»" in answer and "«Бюджет»" in answer
    assert "подтверждения кнопкой" in answer


def test_актёр_без_бюджета_не_видит_чужих_действий() -> None:
    answer = features.human_capabilities(LIMITED)
    assert "«Мои взносы»" in answer and "«Расписание»" in answer
    assert "«Бюджет»" not in answer, "бюджет студии — только с правом budget.read"
    assert "Записать" not in answer, "запись — только с правом budget.write"


def test_действие_названо_один_раз_и_с_кнопки() -> None:
    """Форма, меню и быстрая кнопка — одно действие; подпись берётся с кнопки."""
    answer = features.human_capabilities(ADMIN)
    assert answer.count("«Расход и доход»") == 1
    assert "«Форма операции»" not in answer
    assert "«Деньги (быстрая кнопка)»" not in answer
    # чтение тоже не дублируется: «Деньги» (экран) и «Бюджет» (пункт меню) — одно действие
    assert answer.count("«Бюджет»") == 1
    assert "«Деньги»" not in answer


def test_человек_без_прав_получает_честный_минимум() -> None:
    answer = features.human_capabilities({"id": 3, "caps": []})
    # read_schedule/read_availability/list_files доступны любому активному,
    # поэтому «пусто» бывает только у неактивного: проверяем форму ответа.
    assert answer.startswith("Посмотреть:") or answer.startswith("По вашим правам")


def test_список_действий_не_содержит_не_для_агента() -> None:
    for feature in features.all_features():
        if feature.not_for_agent:
            assert feature not in features.features_for(ADMIN)


# --------------------------------------------------------------------- промпт модели
def test_промпт_перечисляет_только_разрешённые_действия() -> None:
    block = features.prompt_block(LIMITED)
    assert "«Расписание»" in block and "«Мои взносы»" in block
    assert "«Бюджет»" not in block
    assert "другие не предлагай" in block


def test_промпт_без_прав_честно_говорит_что_нечего() -> None:
    block = features.prompt_block({"id": 4, "caps": ["нет-такого-права"]})
    # read_schedule и read_availability не требуют прав — они остаются,
    # а вот бюджет и запись не появляются.
    assert "«Бюджет»" not in block and "«Расход и доход»" not in block


# ------------------------------------------------------------- распознавание вопроса
@pytest.mark.parametrize("text", [
    "что ты умеешь?",
    "что можно записать?",
    "что можно посмотреть",
    "какие действия тебе доступны",
    "что умеешь и что можно записать",
])
def test_вопрос_о_возможностях_распознаётся(text: str) -> None:
    assert features.answers_capability_question(text) is True


@pytest.mark.parametrize("text", [
    "какой сегодня день",
    "что можно посмотреть в расписании на этой неделе",
    "сколько денег в бюджете за ноябрь",
    "кто свободен завтра",
    "какие у меня взносы за октябрь",
])
def test_вопрос_про_данные_не_считается_вопросом_о_возможностях(text: str) -> None:
    assert features.answers_capability_question(text) is False


def test_ответ_о_возможностях_без_инструментов_и_записи() -> None:
    result = features.capability_answer(ADMIN)
    assert result["ok"] is True and result["steps"] == []
    assert result["propose"] is None and result["tokens"] == 0
    assert result["answer"] == features.human_capabilities(ADMIN)


# ------------------------------------------------- действия вне закрытого реестра
@pytest.mark.parametrize("text", [
    "Перенеси репетицию на час позже",
    "Отмени мою запись на завтра",
    "Удали событие из расписания",
    "Измени время репетиции на 19:00",
])
def test_просьба_о_недоступном_действии_распознаётся(text: str) -> None:
    assert features.asks_unsupported_action(text) is True


@pytest.mark.parametrize("text", [
    "Запиши расход 500 рублей на реквизит",
    "Какие у меня взносы?",
    "Что можно посмотреть?",
    "Кто свободен завтра?",
])
def test_обычный_вопрос_не_считается_недоступным_действием(text: str) -> None:
    assert features.asks_unsupported_action(text) is False


def test_ответ_о_недоступном_действии_честный_и_без_записи() -> None:
    result = features.unsupported_action_answer(ADMIN)
    assert result["ok"] is True and result["propose"] is None and result["steps"] == []
    assert "не умею" in result["answer"].lower()
    assert "перенес" not in result["answer"].lower().replace("перенос", "")
    assert "«Расход и доход»" in result["answer"], "честный отказ говорит, что доступно"


# ------------------------------------------------------------------ проверка самого файла
def _write(tmp_path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    path = tmp_path / "features.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "entries": entries}, allow_unicode=True),
                    encoding="utf-8")
    return path


def _write_scenarios(tmp_path: pathlib.Path, scenarios: list[dict]) -> pathlib.Path:
    path = tmp_path / "scenarios.yaml"
    payload = {"version": 1, "scenarios": scenarios, "entries": [
        {"id": "x", "kind": "button", "surface": "chat", "source": "Кнопка",
         "title": "Кнопка", "not_for_agent": "не действие агента"}]}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_инструмент_вне_закрытого_реестра_отклоняется(tmp_path) -> None:
    path = _write(tmp_path, [
        {"id": "x", "kind": "button", "surface": "menu", "source": "Кнопка",
         "title": "Кнопка", "tool": "delete_everything"}])
    with pytest.raises(features.RegistryError, match="нет в registry.TOOLS"):
        features.load(path)


def test_два_смысла_у_записи_отклоняются(tmp_path) -> None:
    path = _write(tmp_path, [
        {"id": "x", "kind": "button", "surface": "menu", "source": "Кнопка",
         "title": "Кнопка", "tool": "read_budget", "not_for_agent": "и это тоже"}])
    with pytest.raises(features.RegistryError, match="ровно один смысл"):
        features.load(path)


def test_не_для_агента_без_причины_отклоняется(tmp_path) -> None:
    path = _write(tmp_path, [
        {"id": "x", "kind": "button", "surface": "menu", "source": "Кнопка",
         "title": "Кнопка", "not_for_agent": "  "}])
    with pytest.raises(features.RegistryError, match="ровно один смысл|без причины"):
        features.load(path)


def test_пустой_реестр_отклоняется(tmp_path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("version: 1\nentries: []\n", encoding="utf-8")
    with pytest.raises(features.RegistryError, match="нет списка entries"):
        features.load(path)


def test_реестр_из_репозитория_проходит_проверку() -> None:
    assert REGISTRY_FILE.is_file()
    assert len(features.load(REGISTRY_FILE)) == len(features.all_features())
