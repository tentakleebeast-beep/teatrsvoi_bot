"""Сторож матрицы 121 «действие участника × интерфейс» (docs/reports/121).

Обоснование: docs/reports/121-action-interface-matrix.md, docs/development/26 §4.

Матрица — сгенерированный отчёт: JSON рядом с ним собран из `docs/development/19` и `20`,
таблицы в md — из JSON. Такой отчёт без сторожа расходится с источником молча: до карты
t_b0048881 проверка `scripts/check_action_matrix.py` не входила ни в гейт, ни в pytest,
и FR-G18/T1-поля разъехались за две недели. Поэтому проверяем не «зелёный прогон», а ловлю:
подставленное расхождение обязано дать ошибки и код 1, а пересборка отчёта не имеет права
трогать текст вне размеченных регионов `<!-- …:begin/end -->`.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def matrix():
    """Модуль проверки: импортируется из `scripts/`, как его зовёт гейт."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_action_matrix as mod

    return mod


def inventory(mod) -> dict:
    """Копия JSON-инвентаря отчёта 121 — правится в тестах, файл на диске не трогается."""
    return json.loads(mod.DATA.read_text(encoding="utf-8"))


def requirement_with_status(data: dict, status: str = "готово") -> dict:
    """Требование с заданным статусом: подменять поле имеет смысл только на отличии."""
    for row in data["requirements"]:
        if row["status"] == status:
            return row
    raise AssertionError(f"в JSON 121 нет требования со статусом {status!r}")


def outside_regions(mod, text: str) -> str:
    """Текст отчёта с вырезанными регионами — «всё, что генератор не имеет права менять»."""
    for begin, end in mod.MARKERS.values():
        if begin in text:
            head, rest = text.split(begin, 1)
            _, tail = rest.split(end, 1)
            text = f"{head}⟪region⟫{tail}"
    return text


def test_текущий_json_сходится_с_документами(matrix):
    """На текущих данных проверка зелёная: 19/20 и JSON 121 не разошлись."""
    assert matrix.validate(inventory(matrix)) == []


def test_расходящийся_статус_требования_ловится(matrix):
    """Подменённый `status` требования — ошибка с ID и именем поля, а не тишина."""
    data = inventory(matrix)
    row = requirement_with_status(data, "готово")
    row["status"] = "отменено"
    errors = matrix.validate(data)
    assert any(row["id"] in error and "status" in error for error in errors), errors


def test_расходящийся_интерфейс_действия_ловится(matrix):
    """Интерфейс вне закрытого списка (`чат`/`Mini App`/`оба`/`нигде`) — ошибка."""
    data = inventory(matrix)
    data["actions"][0]["interface"] = "телефон"
    errors = matrix.validate(data)
    assert any(data["actions"][0]["id"] in error and "интерфейс" in error
               for error in errors), errors


def test_подменённый_json_роняет_проверку_кодом_1(matrix, tmp_path, monkeypatch, capsys):
    """Проверка отказывает на подставных данных: копия JSON с расхождением — код 1.

    Без этого «гейт ловит расхождение» остаётся обещанием: у 117 (`audit_actions.py`)
    такой тест есть, и здесь он нужен по той же причине. Целая копия того же файла
    возвращает 0 — значит код 1 приходит от расхождения, а не от временного пути.
    """
    data = inventory(matrix)
    intact = tmp_path / "121-intact.json"
    intact.write_text(matrix.DATA.read_text(encoding="utf-8"), encoding="utf-8")
    requirement_with_status(data, "готово")["status"] = "отменено"
    broken = tmp_path / "121-broken.json"
    broken.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["check_action_matrix.py"])
    monkeypatch.setattr(matrix, "DATA", broken)
    assert matrix.main() == 1
    err = capsys.readouterr().err
    assert "ОШИБКА" in err and "status" in err

    monkeypatch.setattr(matrix, "DATA", intact)
    assert matrix.main() == 0, "целая копия JSON не прошла — код 1 приходит не от расхождения"


def test_пересборка_не_правит_текст_вне_регионов(matrix):
    """`replace_region` меняет только тело региона: шапка, середина и хвост остаются.

    Сторож сверяет только регионы, поэтому текст вне них мог быть переписан `--write`
    незаметно для проверки. Тест фиксирует свойство самой пересборки.
    """
    text = (
        "# шапка отчёта\n\nабзац до таблиц\n"
        "<!-- summary:begin -->\nстарая сводка\n<!-- summary:end -->\n"
        "строка между регионами\n"
        "<!-- matrix:begin -->\nстарая матрица\n<!-- matrix:end -->\n"
        "подпись после таблиц\n"
    )
    rebuilt = matrix.replace_region(text, "summary", "новая сводка")
    assert outside_regions(matrix, rebuilt) == outside_regions(matrix, text)
    assert "старая матрица" in rebuilt, "чужой регион переписан"
    assert "новая сводка" in rebuilt

    both = matrix.replace_region(rebuilt, "matrix", "новая матрица")
    assert outside_regions(matrix, both) == outside_regions(matrix, text)
    assert both.startswith("# шапка отчёта")
    assert both.rstrip().endswith("подпись после таблиц")


def test_отчёт_воспроизводится_генератором_целиком(matrix):
    """md 121 равен пересборке из JSON, а текст вне регионов не изменён ни на байт."""
    report = matrix.REPORT.read_text(encoding="utf-8")
    rebuilt = report
    for name, body in matrix.rendered(inventory(matrix)).items():
        rebuilt = matrix.replace_region(rebuilt, name, body)
    assert rebuilt == report, "отчёт 121 расходится с JSON; запустите --write"
    assert outside_regions(matrix, rebuilt) == outside_regions(matrix, report)
