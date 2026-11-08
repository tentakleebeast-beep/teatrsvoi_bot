"""
Ш-52: команды — для ввода данных, диалог — для анализа (запрос Виктора 28.10.2026).

Держит инварианты шага:
1. вопрос о существующем не считается вводом данных и не включает состояние;
2. данные (занятость, расход, доход, назначение) опознаются как ввод;
3. у каждого состояния есть выход: кнопки меню, /start и запасной ответ
   на свободный текст — бот не молчит.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_APP = ROOT / "services" / "tg-gateway" / "app"
sys.path.insert(0, str(GATEWAY_APP))

import gateway_modules
import routing  # noqa: E402

MAIN = gateway_modules.source()

QUESTIONS = [
    "что у меня завтра",
    "когда все свободны",
    "сколько потратили в октябре",
    "как записать расход",
    "кто я",
    # вопрос о чужой занятости идёт к агенту (read_availability), иначе кэш
    # занятости в бою не срабатывает: возврат ревью A-4, 02.11.2026.
    "кто сегодня занят",
    "кто занят",
    "покажи занятость на сегодня",
]

DATA = {
    "потратил 3000 на реквизит": "budget",
    "купили ткань 12000": "budget",
    "занят 28.10 с 10 до 14": "availability",
    "назначь репетицию в четверг в 20": "assign",
}

FORM_STATES = ("BudgetForm.amount", "BudgetForm.category",
               "AvailForm.day", "AvailForm.time")


def test_ш52_вопрос_не_включает_ввод_данных():
    """Ш-52 п.2: вопрос о существующем отвечает диалог, состояние не включается."""
    for phrase in QUESTIONS:
        assert routing.data_input_kind(phrase) is None, phrase


def test_ш52_данные_опознаются_как_ввод():
    """Ш-52 п.1: данные распознаются — их принимает команда или форма."""
    for phrase, kind in DATA.items():
        assert routing.data_input_kind(phrase) == kind, phrase


def test_ш52_вопрос_идёт_раньше_ввода_при_цифре():
    """Ш-52 п.2: «сколько потратили в октябре» — вопрос, а не черновик траты."""
    names = routing.candidates("сколько потратили в октябре")
    assert names[0] == "money"
    assert "budget" in names  # слово «потратили» есть, но первым идёт вопрос


def test_ш52_назначение_не_становится_тратой():
    """Ш-52 п.1: «назначь репетицию в 20» — событие, а не трата 20 рублей."""
    names = routing.candidates("назначь репетицию в четверг в 20")
    assert names[0] == "assign"
    assert "budget" not in names


def test_ш52_команды_ввода_зарегистрированы():
    """Ш-52 п.1: данные вносятся командой — /expense, /income, /busy."""
    assert chr(67) + "ommand(\"expense\", \"income\")" in MAIN
    assert chr(67) + "ommand(\"busy\")" in MAIN


def test_ш52_форма_не_держит_человека_кнопками_меню():
    """Ш-52 п.3: кнопки меню работают внутри формы, а не становятся ответом."""
    for state in FORM_STATES:
        start = MAIN.index("@router.message(" + state + ", F.text)")
        block = MAIN[start:start + 1200]
        assert "MENU_SURFACE" in block, state


def test_ш52_подтверждение_не_молчит():
    """Ш-52 п.3: у состояния подтверждения нет разбора текста — отвечает запасной."""
    for state in ("BudgetForm.confirm", "AvailForm.confirm"):
        assert "@router.message(" + state not in MAIN
    marker = "StateFilter(\"*\"), F.text & ~F.text.startswith(\"/\")"
    assert marker in MAIN
    tail = MAIN[MAIN.index(marker):]
    assert "await state.clear()" in tail
    assert "обратитесь к разработчику" in tail
