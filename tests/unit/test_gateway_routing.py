"""
Порядок обработчиков в gateway — регрессионный тест.

История: «Админ-панель» и /participants уходили в модель, потому что
свободный текст (`F.text`) был зарегистрирован раньше кнопок меню и команд,
и перехватывал всё подряд. Тест держит порядок: меню и команды — до catch-all,
а catch-all работает только без активного состояния FSM и не начинается с «/».
Обоснование (Ш-51): файл создан в 64c8435 fix(gateway): кнопки меню и команды больше не уходят в LLM; UC-каталог (docs/28).
"""

import pathlib
import sys

import gateway_modules
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "services" / "tg-gateway"


@pytest.fixture(scope="module")
def gw():
    return gateway_modules.namespace()


def handlers(gw):
    """Обработчики message всех роутеров в том порядке, в каком их видит Dispatcher."""
    from main import build_dispatcher  # noqa: PLC0415 — сборка без сети и polling

    dp = build_dispatcher()
    return [handler for router in dp.sub_routers for handler in router.message.handlers]


def names(gw):
    return [h.callback.__name__ for h in handlers(gw)]


def test_кнопки_меню_обрабатываются_до_свободного_текста(gw):
    order = names(gw)
    assert "on_menu_button" in order, "обработчик кнопок меню не зарегистрирован"
    assert "on_text" in order
    assert order.index("on_menu_button") < order.index("on_text")


def test_свободный_текст_не_перехватывает_команды(gw):
    """У catch-all должен стоять фильтр состояния и исключение «/»."""
    catch_all = next(h for h in handlers(gw) if h.callback.__name__ == "on_text")
    text = repr(catch_all.filters)
    assert "StateFilter" in text, "catch-all сработает внутри формы и съест ввод"
    assert "startswith" in text or "MagicFilter" in text, \
        "catch-all не исключает команды с «/»"


def test_команды_зарегистрированы(gw):
    order = names(gw)
    for handler in ("cmd_dues", "cmd_help", "menu_cmd"):
        assert handler in order, f"обработчик {handler} не зарегистрирован"


def test_админских_команд_больше_нет(gw):
    """Админский доступ — в панели кнопками: печатать /grant, /role, /invite не нужно."""
    source = gateway_modules.source()
    for command in ("grant", "role", "invite", "tariff", "sheet-retry", "pending",
                    "participants", "caps", "caps-list", "admin"):
        assert f'Command("{command}")' not in source, f"команда /{command} вернулась"


def test_все_админские_действия_есть_в_панели(gw):
    """Каждое действие, которое раньше делали командой, доступно кнопкой."""
    source = gateway_modules.source()
    for callback in ("panel:people", "panel:pending", "panel:rights", "panel:invite",
                     "panel:proles:", "panel:caps:", "panel:tariff:",
                     "panel:sheet_retry"):
        assert callback in source, f"нет кнопки {callback}"


def test_нет_дублей_обработчиков(gw):
    order = names(gw)
    duplicates = {n for n in order if order.count(n) > 1}
    assert not duplicates, f"дубли обработчиков: {duplicates}"


def test_кнопки_меню_совпадают_с_обработчиком(gw):
    """Клавиатура — три контроля диалога, а доменные входы живут отдельно.

    До решения 05.11.2026 правило было «клавиатура ⊆ MENU_SURFACE ∪ {Открыть меню}»:
    кнопки чата дублировали дома Mini App. Теперь клавиатура управляет разговором
    («Обнулить», «Озадачить», «Сколько мне осталось»), а MENU_SURFACE — доменные
    входы меню. Списки независимы, и пересекаться они не должны: иначе домен снова
    пролезет кнопкой в чат.
    """
    keyboard = {b.text for row in gw.MAIN_MENU.keyboard for b in row}
    assert keyboard == set(gw.DIALOG_CONTROLS)
    assert keyboard == {"Обнулить", "Озадачить", "Сколько мне осталось"}
    assert not keyboard & set(gw.MENU_SURFACE)
    items = {text for _, texts in gw.menu_groups(["*"]) for text in texts}
    assert items == set(gw.MENU_ACTIONS) - {"Помощь"}


def test_у_каждого_контрола_клавиатуры_есть_обработчик(gw):
    """Каждая подпись клавиатуры — известный контроль, и обработчик его знает."""
    order = names(gw)
    assert "on_dialog_control" in order, "обработчик трёх кнопок не зарегистрирован"
    assert order.index("on_dialog_control") < order.index("on_text"), \
        "кнопки диалога должны обрабатываться до свободного текста"
    source = gateway_modules.source("routers.menu")
    for label in gw.DIALOG_CONTROLS:
        assert label in {gw.BTN_RESET, gw.BTN_TASK, gw.BTN_QUOTA}
    block = source[source.find("async def dialog_control"):]
    assert "BTN_RESET" in block and "BTN_TASK" in block and "TaskFrame" in block
    assert "answer_quota" in block


def test_клавиатура_одинакова_для_всех_прав(gw):
    """Три контроля разговора не зависят от прав: отказа на них быть не может."""
    admin = {b.text for row in gw.menu_kb(["*"]).keyboard for b in row}
    student = {b.text for row in gw.menu_kb(["schedule.availability.self"]).keyboard for b in row}
    nobody = {b.text for row in gw.menu_kb([]).keyboard for b in row}
    assert admin == student == nobody == set(gw.DIALOG_CONTROLS)


def test_howto_вопросы_не_уходят_в_модель(gw):
    """«как записать расход» — инструкция, а не операция: отвечаем справкой."""
    import re

    howto = re.compile(gw.HOWTO_Q)
    assert howto.search("как записать расход?")
    assert howto.search("где посмотреть расписание")
    assert howto.search("подскажи, что умеешь")
    assert not howto.search("расход 3000 на реквизит")
    assert not howto.search("купили ткань 12000")


def test_howto_проверяется_раньше_денег():
    source = gateway_modules.source()
    howto_pos = source.find("re.search(HOWTO_Q")
    money_pos = source.find("re.search(MONEY_Q, low)")
    assert 0 < howto_pos < money_pos, "порядок веток: HOWTO должен идти до MONEY"
