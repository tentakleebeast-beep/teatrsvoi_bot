"""Лист по имени и контрольная сумма шапки (docs/development/186 §4.3 шаги 4–6).

Обоснование: docs/development/186 §4.3, §9 (`sheet_ambiguous`, `header_mismatch`).
Правила, которые держит этот файл: «первый лист файла» запрещён, пустое и повторяющееся
имя — отказ, шапка сравнивается по `sha256` от нормированных подписей, а служебная
колонка `svoi`, дописанная ботом, не превращает следующую запись в `header_mismatch`.
"""

from __future__ import annotations

from app import sheet_binding


def test_сумма_шапки_не_зависит_от_лишних_пробелов():
    """`"  Дата  "` и `"Дата"` — одна подпись: сумму не двигают пробелы человека."""
    left = sheet_binding.header_checksum(["Дата", " Сумма ", "Категория"])
    right = sheet_binding.header_checksum([" Дата", "Сумма", "Категория  "])
    assert left == right


def test_сумма_шапки_ловит_перестановку_колонок():
    """Переименование или перестановка подписей — уже другая сумма."""
    assert (sheet_binding.header_checksum(["Дата", "Сумма"])
            != sheet_binding.header_checksum(["Сумма", "Дата"]))


def test_сумма_шапки_не_склеивает_соседние_ячейки():
    """`["a b"]` и `["a", "b"]` — разные шапки: разделитель `\\x1f` не даёт склейки."""
    assert (sheet_binding.header_checksum(["a b"])
            != sheet_binding.header_checksum(["a", "b"]))


def test_лист_ищется_по_имени():
    """Имя найдено один раз — берём его `sheetId` и подпись."""
    props = [{"properties": {"sheetId": 1, "title": "Транзакции"}},
             {"properties": {"sheetId": 2, "title": "Итоги"}}]
    assert sheet_binding.pick_sheet(props, "Итоги") == (2, "Итоги")


def test_пустое_имя_листа_это_отказ():
    """Пустое имя — `sheet_ambiguous`: «первый лист файла» больше не подставляется."""
    props = [{"properties": {"sheetId": 1, "title": "Транзакции"}}]
    try:
        sheet_binding.pick_sheet(props, "  ")
    except Exception as caught:  # noqa: BLE001 — проверяем именно код отказа
        assert getattr(caught, "code", "") == "sheet_ambiguous"
    else:
        raise AssertionError("пустое имя листа прошло молча")


def test_два_листа_с_одним_именем_это_отказ():
    """Две страницы с одним именем — писать некуда: `sheet_ambiguous`."""
    props = [{"properties": {"sheetId": 1, "title": "Занятость"}},
             {"properties": {"sheetId": 2, "title": "Занятость"}}]
    try:
        sheet_binding.pick_sheet(props, "Занятость")
    except Exception as caught:  # noqa: BLE001
        assert getattr(caught, "code", "") == "sheet_ambiguous"
    else:
        raise AssertionError("дубль имени прошёл молча")


def test_служебная_колонка_входит_в_шапку_после_записи():
    """Бот дописал `svoi` — сумма считается по листу после записи, а не до неё.

    Иначе следующее открытие сравнило бы новую шапку со старой суммой и навсегда
    получило `header_mismatch` (реестр урока E1-4).
    """
    binding = sheet_binding.Binding(key="schedule.workload", spreadsheet_id="s",
                                    sheet_title="Занятость", layout="monthly_blocks")
    session = sheet_binding.SheetSession(key="schedule.workload", spreadsheet_id="s",
                                         binding=binding, service=None, sheet_id=1,
                                         sheet_title="Занятость", modified_time=None,
                                         header=[["", "Дата", "Дарья"]])
    sheet_binding.note_header_cell(session, "D1", "svoi")
    assert session.header[0][3] == "svoi"
    assert (sheet_binding.header_checksum(session.header[0])
            == sheet_binding.header_checksum(["", "Дата", "Дарья", "svoi"]))
