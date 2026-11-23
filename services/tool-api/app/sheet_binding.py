"""Протокол записи в лист Google: привязка, шапка, свежесть, дедуп, проверка фактом.

Зачем (docs/development/186 §4). Записи в лист жили в трёх разных местах и по трём
разным правилам: бюджет дописывал строку без сверки заголовков, занятость брала
«первый лист файла», тестовый лист вообще был снаружи сервиса. Здесь один порядок
шагов на все листы:

    ключ реестра → режим → `sheet_binding` → лист по имени → сумма шапки →
    свежесть файла → план и дедуп по `row_map` → запись → перечитывание → `row_map`

Модуль не знает, что именно пишется: на вход приходят адреса и тексты, на выход —
признак «лист подтвердил». Расчёт адресов остаётся в домене (`schedule_sheet.py`):
он и есть вычисление адресов **после** сверки шапки.

Тесты: `tests/unit/test_sheet_binding.py`, `test_row_map_protocol.py`,
`test_sheet_freshness.py`, `test_schedule_sheet_title.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app import core, google_targets
from app.errors import ApiError

#: Разделитель ячеек шапки: `\x1f` (unit separator) не встречается в подписях человека
#: и не даёт склеить «a b» из двух разных колонок.
HEADER_SEP = "\x1f"

#: Поля метаданных листа. `properties.modifiedTime` в Sheets API не существует (это
#: свойство файла Drive): запрос с ним отвечает 400 «invalid argument», поэтому время
#: правки берётся у Drive отдельно (`file_modified_time`).
SHEET_FIELDS = "properties.title,sheets.properties(sheetId,title)"
DRIVE_FIELDS = "modifiedTime"


def norm_cell(value: Any) -> str:
    """Нормализация ячейки шапки: схлопнуть пробелы, обрезать края, регистр сохранить."""
    return " ".join(str(value if value is not None else "").split())


def header_checksum(header_row: Sequence[Any], first_col: int = 1, last_col: int | None = None) -> str:
    """Контрольная сумма шапки: `sha256` от `\\x1f`-склейки нормированных ячеек.

    Пустые ячейки внутри диапазона дают пустые куски, хвостовые пустые колонки
    обрезаются до `last_col`, если он задан (`docs/development/186` §4.2).
    """
    start = max(int(first_col or 1) - 1, 0)
    end = len(header_row) if last_col is None else max(int(last_col), start)
    cells = [header_row[index] if index < len(header_row) else "" for index in range(start, end)]
    payload = HEADER_SEP.join(norm_cell(cell) for cell in cells)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def content_hash(values: Any) -> str:
    """Хеш содержимого для `row_map.content_hash`: что именно бот записал в клетку."""
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def parse_time(value: Any) -> datetime | None:
    """RFC3339 из Google (`modifiedTime`, `lastEditTime`) → datetime с зоной."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Binding:
    """Строка `sheet_binding`: где лист и какая у него шапка."""

    key: str
    spreadsheet_id: str
    sheet_title: str
    layout: str
    header_row: int = 1
    first_col: int = 1
    last_col: int | None = None
    header_checksum: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    last_read_at: datetime | None = None
    last_edit_time: datetime | None = None

    @property
    def title(self) -> str:
        return (self.sheet_title or "").strip()


def _row_get(row, name: str, index: int, default=None):
    """Поле строки БД: соединение ядра отдаёт словарь, тестовое — кортеж."""
    if row is None:
        return default
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        return row[index] if index < len(row) else default


def load_binding(conn, key: str) -> Binding | None:
    """Привязка по ключу реестра. Нет строки — None (решение принимает вызывающий)."""
    row = conn.execute("""
        select key, spreadsheet_id, sheet_title, layout, header_row,
               first_col, last_col, header_checksum, meta, last_read_at, last_edit_time
        from sheet_binding where key = %s""", (key,)).fetchone()
    if row is None:
        return None
    meta = _row_get(row, "meta", 8, {}) or {}
    return Binding(
        key=str(_row_get(row, "key", 0, key)),
        spreadsheet_id=str(_row_get(row, "spreadsheet_id", 1, "") or ""),
        sheet_title=str(_row_get(row, "sheet_title", 2, "") or ""),
        layout=str(_row_get(row, "layout", 3, "") or ""),
        header_row=int(_row_get(row, "header_row", 4, 1) or 1),
        first_col=int(_row_get(row, "first_col", 5, 1) or 1),
        last_col=_row_get(row, "last_col", 6),
        header_checksum=_row_get(row, "header_checksum", 7),
        meta=dict(meta) if isinstance(meta, dict) else {},
        last_read_at=_row_get(row, "last_read_at", 9),
        last_edit_time=_row_get(row, "last_edit_time", 10),
    )


def require_binding(conn, key: str) -> Binding:
    """Лист без привязки не пишется: `binding_missing` (409) вместо догадки."""
    binding = load_binding(conn, key)
    if binding is None:
        raise ApiError(409, "binding_missing: лист не привязан к боту",
                       code="binding_missing")
    return binding


def pick_sheet(properties: Iterable[dict], title: str) -> tuple[int, str]:
    """Лист **по имени**: пустое или повторяющееся имя — `sheet_ambiguous` (409).

    «Первый лист файла» запрещён (186 §4.3 шаг 4): именно так бот писал в чужой лист,
    когда `SCHEDULE_SHEET_TITLE` был пуст.
    """
    wanted = (title or "").strip()
    if not wanted:
        raise ApiError(409, "sheet_ambiguous: лист не определён однозначно",
                       code="sheet_ambiguous")
    found = [(item.get("properties") or {}) for item in properties]
    matches = [props for props in found if str(props.get("title") or "").strip() == wanted]
    if len(matches) != 1:
        raise ApiError(409, f"sheet_ambiguous: лист «{wanted}» найден {len(matches)} раз",
                       code="sheet_ambiguous")
    return int(matches[0]["sheetId"]), str(matches[0]["title"])


def stale_reason(modified_time: datetime | None, last_edit_time: datetime | None) -> bool:
    """Файл новее последнего чтения? Тогда записывать нельзя до перечитывания.

    Неизвестное `modifiedTime` (подделка в тестах, старый ответ API) свежесть не
    ломает: сравнивать нечего, а отказ «на всякий случай» остановил бы запись в dev.
    """
    if modified_time is None or last_edit_time is None:
        return False
    return modified_time > last_edit_time


def file_modified_time(spreadsheet_id: str, drive: Any = None) -> datetime | None:
    """Время правки файла из Drive: Sheets API этого свойства не отдаёт (186 §4.3 шаг 6).

    Содержимое не читается — только `modifiedTime`. Любой сбой (нет прав, нет ключа,
    старый клиент) даёт `None`: без времени свежесть просто не блокирует запись, а
    шапка и перечитывание продолжают защищать от записи поверх правки человека.
    """
    try:
        client = drive if drive is not None else core._drive_read()
        answer = client.files().get(fileId=spreadsheet_id, fields=DRIVE_FIELDS).execute()
    except (*core.GOOGLE_API_ERRORS, ValueError):
        # время правки — подсказка, а не данные: без него свежесть просто не блокирует
        return None
    return parse_time(answer.get("modifiedTime"))


def ensure_fresh(modified_time: datetime | None, binding: Binding) -> None:
    if stale_reason(modified_time, binding.last_edit_time):
        raise ApiError(409, "sheet_changed_since_read: файл изменился — обновите и повторите",
                       code="sheet_changed_since_read")


@dataclass(slots=True)
class SheetSession:
    """Открытый лист: привязка, клиент и подтверждённая шапка."""

    key: str
    spreadsheet_id: str
    binding: Binding
    service: Any
    sheet_id: int
    sheet_title: str
    modified_time: datetime | None
    header: list[list]

    @property
    def a1(self) -> str:
        """Диапазон этого листа в нотации A1: `'Транзакции'!A1`."""
        return f"'{self.sheet_title}'!"


def _service(key: str, write: bool):
    """Клиент Google по природе объекта: лист — Sheets, календарь — Calendar."""
    if google_targets.target(key).kind == google_targets.CALENDAR:
        return core._calendar_write() if write else core._calendar()
    return core._sheets() if write else core._sheets_read()


def _resolve_title(key: str, binding: Binding) -> str:
    """Имя листа: привязка, иначе (для занятости) настройка `SCHEDULE_SHEET_TITLE`."""
    title = binding.title
    if title:
        return title
    if key == "schedule.workload":
        return str(core.SCHEDULE_SHEET_TITLE or "").strip()
    return ""


def _header_values(service, spreadsheet_id: str, title: str, header_row: int) -> list[list]:
    return service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'!A{header_row}:AZ{header_row}",
        majorDimension="ROWS").execute().get("values", [])


def open_sheet(key: str, *, conn, write: bool = True, service: Any = None,
               drive: Any = None) -> SheetSession:
    """Шаги 1–6 протокола: ключ, режим, привязка, лист по имени, шапка, свежесть.

    Возвращает открытый лист. Любой шаг может отказать кодом из 186 §9 — и это
    правильный исход: писать наугад бот не умеет.
    """
    if write:
        spreadsheet_id = google_targets.resolve(key)
    elif key in google_targets.READ_SOURCES:
        spreadsheet_id = google_targets.read_id(key)
    else:
        # ключ записи тоже можно читать: чтение — подмножество, и превью листа бота
        # не должно требовать отдельного ключа чтения
        spreadsheet_id = google_targets.resolve(key)
    if not spreadsheet_id:
        raise ApiError(503, "write_target_unset: объект записи не настроен",
                       code="write_target_unset")
    if write and not google_targets.allowed_for_write(key, conn):
        raise ApiError(403, "write_target_refused: объект не разрешён для записи",
                       code="write_target_refused")

    binding = require_binding(conn, key)
    service = service if service is not None else _service(key, write)
    title = _resolve_title(key, binding)
    if not title:
        raise ApiError(409, "sheet_ambiguous: лист не определён однозначно",
                       code="sheet_ambiguous")

    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id,
                                          fields=SHEET_FIELDS).execute()
    except core.GOOGLE_API_ERRORS as exc:
        raise _google_error(exc, "sheet_unavailable") from None

    sheet_id, sheet_title = pick_sheet(meta.get("sheets") or [], title)
    modified = parse_time((meta.get("properties") or {}).get("modifiedTime"))
    if modified is None:
        modified = file_modified_time(spreadsheet_id, drive)

    header = _header_values(service, spreadsheet_id, sheet_title, binding.header_row)
    row = header[0] if header else []
    computed = header_checksum(row, binding.first_col, binding.last_col)
    if binding.header_checksum and binding.header_checksum != computed:
        raise ApiError(409, "header_mismatch: заголовки листа изменились — запись остановлена",
                       code="header_mismatch")
    if write:
        ensure_fresh(modified, binding)

    return SheetSession(key=key, spreadsheet_id=spreadsheet_id, binding=binding,
                        service=service, sheet_id=sheet_id, sheet_title=sheet_title,
                        modified_time=modified, header=header)


def note_header_cell(session: SheetSession, a1: str, text: str) -> None:
    """Учесть записанную клетку шапки в памяти сессии.

    Бот сам дописывает служебную колонку `svoi` в шапку листа занятости. Если её не
    учесть, `remember_header` запомнит сумму шапки **до** записи, следующее открытие
    увидит новую шапку и честно откажет `header_mismatch` — запись встала бы навсегда
    после первой же недели. Поэтому сумму считаем по состоянию после записи.
    """
    box = str(a1 or "").split("!")[-1]
    row = int("".join(ch for ch in box if ch.isdigit()) or 0)
    if row != session.binding.header_row:
        return
    letters = "".join(ch for ch in box if ch.isalpha())
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    if index <= 0:
        return
    if not session.header:
        session.header.append([])
    line = session.header[0]
    while len(line) < index:
        line.append("")
    line[index - 1] = text


def remember_header(conn, session: SheetSession) -> None:
    """Запомнить сумму шапки при первой записи: следующий прогон сверит её, а не угадает."""
    row = session.header[0] if session.header else []
    computed = header_checksum(row, session.binding.first_col, session.binding.last_col)
    if session.binding.header_checksum:
        return
    conn.execute("update sheet_binding set header_checksum = %s, updated_at = now() "
                 "where key = %s", (computed, session.key))


def touch_after_write(conn, session: SheetSession) -> None:
    """Свежесть после записи: `last_read_at = now()`, `last_edit_time` — время файла."""
    conn.execute("update sheet_binding set last_read_at = now(), last_edit_time = %s, "
                 "updated_at = now() where key = %s",
                 (session.modified_time or datetime.now(UTC), session.key))


def touch_after_read(conn, key: str, modified_time: datetime | None) -> None:
    """Отметка чтения: `last_read_at` всегда, `last_edit_time` — когда время файла известно."""
    if modified_time:
        conn.execute("update sheet_binding set last_read_at = now(), last_edit_time = %s, "
                     "updated_at = now() where key = %s", (modified_time, key))
    else:
        conn.execute("update sheet_binding set last_read_at = now(), updated_at = now() "
                     "where key = %s", (key,))


def write_cells(session: SheetSession, cells: Iterable[tuple[str, str]],
                *, value_input_option: str = "RAW") -> None:
    """Запись готовых клеток одним `batchUpdate`. Отказ Google — `sheet_write_failed` (502)."""
    data = [{"range": f"{session.a1}{a1}", "values": [[text]]} for a1, text in cells]
    if not data:
        return
    try:
        session.service.spreadsheets().values().batchUpdate(
            spreadsheetId=session.spreadsheet_id,
            body={"valueInputOption": value_input_option, "data": data}).execute()
    except core.GOOGLE_API_ERRORS as exc:
        raise _google_error(exc, "sheet_write_failed") from None


def verify_cells(session: SheetSession, cells: Iterable[tuple[str, str]]) -> list[dict]:
    """Перечитывание тех же адресов: «записали» без проверки не считается (186 §4.3 шаг 9)."""
    pairs = list(cells)
    if not pairs:
        return []
    ranges = [f"{session.a1}{a1}" for a1, _ in pairs]
    try:
        answer = session.service.spreadsheets().values().batchGet(
            spreadsheetId=session.spreadsheet_id, ranges=ranges).execute()
    except core.GOOGLE_API_ERRORS as exc:
        raise _google_error(exc, "sheet_verify_failed") from None
    mismatched = []
    for (a1, text), value_range in zip(pairs, answer.get("valueRanges", []), strict=False):
        rows = value_range.get("values") or [[""]]
        got = str((rows[0] or [""])[0] or "")
        if norm_cell(got) != norm_cell(text):
            mismatched.append({"cell": a1, "expected": text, "got": got})
    return mismatched


def row_number(a1_range: str | None) -> int | None:
    """Номер строки из `updates.updatedRange` (`'Транзакции'!A7:M7` → 7)."""
    text = str(a1_range or "")
    digits = ""
    for char in text.split("!")[-1]:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else None


def append_row(session: SheetSession, row: Sequence[Any],
               *, value_input_option: str = "USER_ENTERED") -> int | None:
    """Дописать строку в лист. Возвращает номер строки из ответа (не «на глаз»)."""
    try:
        answer = session.service.spreadsheets().values().append(
            spreadsheetId=session.spreadsheet_id, range=f"{session.a1}A1",
            valueInputOption=value_input_option, insertDataOption="INSERT_ROWS",
            body={"values": [list(row)]}).execute()
    except core.GOOGLE_API_ERRORS as exc:
        raise _google_error(exc, "sheet_write_failed") from None
    return row_number((answer.get("updates") or {}).get("updatedRange"))


def update_row(session: SheetSession, row: Sequence[Any], sheet_row: int,
               *, value_input_option: str = "USER_ENTERED") -> None:
    """Перезаписать существующую строку: повтор операции не добавляет вторую (дедуп)."""
    width = len(row)
    last_col = session.binding.last_col or width
    rng = f"{session.a1}A{sheet_row}:{column_name(min(width, last_col))}{sheet_row}"
    try:
        session.service.spreadsheets().values().update(
            spreadsheetId=session.spreadsheet_id, range=rng,
            valueInputOption=value_input_option,
            body={"values": [list(row)]}).execute()
    except core.GOOGLE_API_ERRORS as exc:
        raise _google_error(exc, "sheet_write_failed") from None


def column_name(index: int) -> str:
    """1 → A, 27 → AA. Нужен для диапазона перезаписи строки."""
    letters = ""
    while index > 0:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters or "A"


def read_row(session: SheetSession, sheet_row: int, width: int) -> list[str]:
    """Прочитать записанную строку по её адресу — для проверки фактом."""
    rng = f"{session.a1}A{sheet_row}:{column_name(width)}{sheet_row}"
    try:
        values = session.service.spreadsheets().values().get(
            spreadsheetId=session.spreadsheet_id, range=rng,
            majorDimension="ROWS").execute().get("values", [])
    except core.GOOGLE_API_ERRORS as exc:
        raise _google_error(exc, "sheet_verify_failed") from None
    line = values[0] if values else []
    return [norm_cell(line[index]) if index < len(line) else "" for index in range(width)]


def verify_row(session: SheetSession, row: Sequence[Any], sheet_row: int) -> list[str]:
    """Сверить строку с тем, что записали. Пустой список — лист подтвердил."""
    expected = [norm_cell(cell) for cell in row]
    got = read_row(session, sheet_row, len(expected))
    return [f"{index + 1}:{expected[index]!r}!={got[index]!r}"
            for index in range(len(expected)) if expected[index] != got[index]]


def hide_column(session: SheetSession, column: int) -> None:
    """Скрыть служебную колонку (`svoi`): людям она не нужна, машине — да."""
    try:
        session.service.spreadsheets().batchUpdate(
            spreadsheetId=session.spreadsheet_id,
            body={"requests": [{"updateDimensionProperties": {
                "range": {"sheetId": session.sheet_id, "dimension": "COLUMNS",
                          "startIndex": column - 1, "endIndex": column},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser"}}]}).execute()
    except core.GOOGLE_API_ERRORS as exc:
        # скрытие колонки — оформление, а не данные: запись уже состоялась
        core.log.warning("не удалось скрыть служебную колонку: %s", str(exc)[:120])


def row_map_find(conn, binding_key: str, entity_type: str, entity_id: str,
                 cell_key: str | None = None) -> dict | None:
    """Строка `row_map` для сущности: адрес и хеш последней записи бота."""
    row = conn.execute("""
        select id, sheet_row, col_from, col_to, cell_key, content_hash, is_deleted
        from row_map
        where binding_key = %s and entity_type = %s and entity_id = %s
          and coalesce(cell_key, '') = coalesce(%s, '')""",
        (binding_key, entity_type, entity_id, cell_key)).fetchone()
    if row is None:
        return None
    return {"id": _row_get(row, "id", 0), "sheet_row": _row_get(row, "sheet_row", 1),
            "col_from": _row_get(row, "col_from", 2), "col_to": _row_get(row, "col_to", 3),
            "cell_key": _row_get(row, "cell_key", 4),
            "content_hash": _row_get(row, "content_hash", 5),
            "is_deleted": _row_get(row, "is_deleted", 6)}


def row_map_save(conn, binding_key: str, entity_type: str, entity_id: str,
                 *, sheet_row: int | None = None, col_from: int | None = None,
                 col_to: int | None = None, cell_key: str | None = None,
                 content_hash: str | None = None) -> None:
    """Запомнить адрес записи. `on conflict` тут не годится: уникальность — выражение
    `coalesce(cell_key,'')`, а Postgres не принимает его в `on conflict (...)`."""
    found = row_map_find(conn, binding_key, entity_type, entity_id, cell_key)
    if found is None:
        conn.execute("""
            insert into row_map (binding_key, entity_type, entity_id, sheet_row,
                                 col_from, col_to, cell_key, content_hash, last_seen_at,
                                 is_deleted)
            values (%s, %s, %s, %s, %s, %s, %s, %s, now(), false)""",
            (binding_key, entity_type, entity_id, sheet_row, col_from, col_to,
             cell_key, content_hash))
        return
    conn.execute("""
        update row_map set sheet_row = coalesce(%s, sheet_row),
                           col_from = coalesce(%s, col_from),
                           col_to = coalesce(%s, col_to),
                           content_hash = coalesce(%s, content_hash),
                           last_seen_at = now(), is_deleted = false
        where id = %s""",
        (sheet_row, col_from, col_to, content_hash, found["id"]))


def _google_error(exc: Exception, code: str) -> ApiError:
    """Отказ Google → код конверта. 401/403 — `no_access`, остальное — переданный код."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (401, 403):
        return ApiError(403, "no_access: нет доступа к файлу. Проверьте права бота",
                        code="no_access")
    texts = {"sheet_unavailable": "Лист недоступен — повторите позже",
             "sheet_write_failed": "Не удалось записать в лист",
             "sheet_verify_failed": "Лист не подтвердил запись",
             "calendar_unavailable": "Календарь недоступен",
             "drive_unavailable": "Google Drive недоступен — повторите позже"}
    return ApiError(502, f"{code}: {texts.get(code, 'Google недоступен')}", code=code)
