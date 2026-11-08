"""Панель «Бюджет»: переключение месяцев (Ш-32, обращение №15).

Гейтвей в unit-тестах не поднимается, поэтому проверяем чистый модуль
`periods.py`: сдвиг периода через границу года и состав кнопок навигации.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))

import gateway_modules
import periods  # noqa: E402


def test_соседние_месяцы_внутри_года():
    assert periods.shift_period("2026-10", -1) == "2026-09"
    assert periods.shift_period("2026-10", 1) == "2026-11"


def test_переход_через_границу_года():
    assert periods.shift_period("2027-01", -1) == "2026-12"
    assert periods.shift_period("2026-12", 1) == "2027-01"


def test_декабрь_минус_двенадцать_это_прошлый_год():
    assert periods.shift_period("2026-12", -12) == "2025-12"
    assert periods.shift_period("2026-01", 12) == "2027-01"


def test_неверный_период_не_превращается_в_мусор():
    assert periods.is_period("2026-10") is True
    for bad in ("2026-13", "2026-1", "", None, "октябрь", "2026-10-01"):
        assert periods.is_period(bad) is False
    assert periods.shift_period("2026-13", 1) == "2026-13"


def test_кнопки_ведут_на_соседние_месяцы():
    rows = periods.money_nav_rows("2027-01")
    data = [d for row in rows for _, d in row]
    assert "panel:money:2026-12" in data
    assert "panel:money:2027-02" in data
    # кнопка возврата в корень панели сохранилась
    assert data[-1] == "panel:root"


def test_подписи_кнопок_без_эмодзи():
    rows = periods.money_nav_rows("2026-10")
    for row in rows:
        for text, _ in row:
            assert text.strip(), "пустая подпись кнопки"
            assert text.isprintable()


def test_пустой_период_даёт_текущий_месяц():
    """Некорректный/пустой период не должен ломать callback: он остаётся пустым,
    и обработчик запрашивает текущий месяц."""
    rows = periods.money_nav_rows("")
    data = [d for row in rows for _, d in row]
    assert data[0] == "panel:money:"
    assert data[1] == "panel:money:"


def test_обработчик_принимает_период_из_callback():
    """Статическая проверка связки: панель ловит `panel:money:YYYY-MM`
    и передаёт период в API, иначе кнопки будут вести в никуда."""
    source = gateway_modules.source("routers.admin")
    start = source.index('F.data.startswith("panel:money")')
    block = source[start:source.index("panel:feedback", start)]
    assert "&period={period}" in block
    assert "money_nav_rows" in block


def test_на_первом_периоде_нет_кнопки_назад():
    """Ш-43: нижняя граница — первый период в budget_tx."""
    rows = periods.money_nav_rows("2026-07", first_period="2026-07",
                                 current_period="2026-10")
    data = [d for row in rows for _, d in row]
    assert "panel:money:2026-06" not in data
    assert "panel:money:2026-08" in data


def test_в_текущем_месяце_нет_кнопки_вперёд():
    """Ш-43: верхняя граница — текущий месяц, в будущее листать нельзя."""
    rows = periods.money_nav_rows("2026-10", first_period="2026-07",
                                 current_period="2026-10")
    data = [d for row in rows for _, d in row]
    assert "panel:money:2026-11" not in data
    assert "panel:money:2026-09" in data


def test_в_середине_журнала_обе_кнопки():
    rows = periods.money_nav_rows("2026-08", first_period="2026-07",
                                 current_period="2026-10")
    data = [d for row in rows for _, d in row]
    assert "panel:money:2026-07" in data
    assert "panel:money:2026-09" in data


def test_без_границ_поведение_прежнее():
    data = [d for row in periods.money_nav_rows("2026-10") for _, d in row]
    assert "panel:money:2026-09" in data
    assert "panel:money:2026-11" in data


def test_обработчик_передаёт_границы_из_api():
    """Границы приходят из `budget/recent`, иначе кнопки снова безграничны."""
    source = gateway_modules.source("routers.admin")
    start = source.index("F.data.startswith(\"panel:money\")")
    block = source[start:source.index("panel:feedback", start)]
    assert "res.get(\"first_period\")" in block
    assert "res.get(\"current_period\")" in block
