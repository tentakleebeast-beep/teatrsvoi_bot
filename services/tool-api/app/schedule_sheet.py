"""
Запись занятости в лист «Расписание студийцы» (FR-C9).

Лист устроен по-человечески: люди в колонках, дни в строках, месяцы — блоками,
даты лежат в колонке B. Поэтому привязка не выдумывается заново:

  * строки берём из колонки B (там настоящие даты — сверяем, а не угадываем);
  * колонку человека ищем по шапке; не нашли или нашли двоих — это расхождение
    для листа «Сверка», а не повод писать наугад;
  * скрытая служебная колонка `svoi` хранит снимок того, что записал бот:
    `12=занят до 18:30|15=свободен`. Отличие снимка от клетки означает правку
    человека, которую надо перенести в базу.

Модуль чистый: на вход значения листа (список строк), на выход адреса и тексты.
Запросы к Google и решение «писать или нет» живут в вызывающем коде.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from app.availability_card import parse, to_hours

SERVICE_HEADER = "svoi"
PAIR_SEP = "|"
KV_SEP = "="


@dataclass(frozen=True)
class SheetCell:
    """Одна запись в лист: адрес в A1 и текст."""

    a1: str
    text: str


@dataclass
class Plan:
    """Что записать и о чём сообщить человеку."""

    cells: list[SheetCell] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    person_column: int | None = None
    service_column: int | None = None
    names_row: int | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


def column_letter(index: int) -> str:
    """1 → A, 26 → Z, 27 → AA."""
    if index < 1:
        raise ValueError("column index must be >= 1")
    letters = ""
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def column_index(letter: str) -> int:
    """A → 1, AA → 27."""
    index = 0
    for char in letter.upper():
        if not "A" <= char <= "Z":
            raise ValueError(f"bad column letter: {letter}")
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def cell_a1(row: int, column: int) -> str:
    return f"{column_letter(column)}{row}"


def parse_day_cell(value) -> date | None:
    """Ячейка колонки B → дата. Пустое или нечитаемое — None (расхождение, не догадка)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d.%m"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if fmt == "%d.%m":                     # без года — текущий (в листе так не пишут, но пусть)
            parsed = parsed.replace(year=datetime.now().year)
        return parsed
    return None


def _name_words(value) -> set[str]:
    """Значимые слова имени: без регистра, `ё → е`, без точек и запятых (docs/development/149 D9).

    `ё → е` появилось не из красоты: в листе «Алена Кондракова», в базе «Кондракова Алёна»,
    и без нормализации фамилия совпадала, а имя — нет, то есть человек молча получал
    `person_not_found` (замер 149 §2.3).
    """
    text = str(value or "").lower().replace("ё", "е").replace(".", " ").replace(",", " ")
    return {word for word in text.split() if len(word) > 2}


def _same_name(words: set[str], wanted: set[str]) -> bool:
    """Подпись колонки называет человека: два общих значимых слова или одно и то же слово.

    Одно общее слово из двух («Виктор Иванов» против «Виктор Мамлютов») человеком не является —
    совпадение по имени без фамилии, — поэтому правило требует двух слов, кроме случая,
    когда всё имя состоит из одного значимого слова.
    """
    common = words & wanted
    if not common:
        return False
    return len(wanted) == 1 or len(common) >= 2


def find_names_row(values: list[list], limit: int = 10) -> int | None:
    """Строка шапки: там, где больше всего непустых текстовых подписей. 1-based или None."""
    best_row, best_score = None, 0
    for index, row in enumerate(values[:limit], start=1):
        score = sum(1 for cell in row if isinstance(cell, str) and _name_words(cell))
        if score > best_score:
            best_row, best_score = index, score
    return best_row if best_score >= 2 else None


def find_person_columns(values: list[list], names_row: int, display_name: str,
                        aliases: Iterable[str] = ()) -> list[int]:
    """Колонки, чья подпись называет человека. Больше одной — неоднозначность.

    Подписи колонки хватает, чтобы совпасть с именем человека или с любым его псевдонимом:
    в листе люди подписаны то «Витя», то «Виктор Мамлютов», и заводить второй список имён
    для чтения и записи нельзя — разойдётся (одна функция на оба направления, 149 D9).
    """
    if names_row is None or not 1 <= names_row <= len(values):
        return []
    names = [_name_words(display_name)] + [_name_words(alias) for alias in aliases]
    names = [wanted for wanted in names if wanted]
    if not names:
        return []
    row = values[names_row - 1]
    found = []
    for column, cell in enumerate(row, start=1):
        words = _name_words(cell)
        if not words:
            continue
        if any(_same_name(words, wanted) for wanted in names):
            found.append(column)
    return found


def resolve_column(values: list[list], display_name: str,
                   aliases: Iterable[str] = ()) -> tuple[list[int], int | None]:
    """Где человек в листе: (колонки, строка шапки). 0 колонок — нет, 2 и больше — неясно."""
    names_row = find_names_row(values)
    columns = (find_person_columns(values, names_row, display_name, aliases)
               if names_row is not None else [])
    return columns, names_row


def find_day_rows(values: list[list], date_column: int = 2) -> dict[date, int]:
    """Строки с датами по колонке B. Дубли и мусор остаются на «Сверке»."""
    rows: dict[date, int] = {}
    for index, row in enumerate(values, start=1):
        cell = row[date_column - 1] if len(row) >= date_column else None
        day = parse_day_cell(cell)
        if day is not None and day not in rows:
            rows[day] = index
    return rows


def _last_filled(values: list[list]) -> int:
    """Последняя колонка с непустым значением. Хвостовые пустые клетки не считаем."""
    width = 0
    for row in values:
        for index in range(len(row), 0, -1):
            if str(row[index - 1] or "").strip():
                width = max(width, index)
                break
    return width


def _column_has_data(values: list[list], column: int) -> bool:
    return any(len(row) >= column and str(row[column - 1] or "").strip() for row in values)


def find_service_column(values: list[list], names_row: int | None) -> tuple[int, bool]:
    """
    Колонка служебного снимка и признак «её надо создать».

    Ищем подпись `svoi` в шапке. Если нет — берём через одну колонку справа от
    последней заполненной и пропускаем те, где уже что-то есть: служебная колонка
    не имеет права лечь поверх человеческих данных.
    """
    if names_row and names_row <= len(values):
        for column, cell in enumerate(values[names_row - 1], start=1):
            if str(cell or "").strip().lower() == SERVICE_HEADER:
                return column, False
    column = _last_filled(values) + 2
    while _column_has_data(values, column):
        column += 1
    return column, True


def snapshot_get(existing, person_id: int) -> str:
    """Что бот записал этому человеку в этой строке по снимку (пусто — не писал)."""
    for chunk in str(existing or "").split(PAIR_SEP):
        chunk = chunk.strip()
        if chunk.startswith(f"{person_id}{KV_SEP}"):
            return chunk.split(KV_SEP, 1)[1].strip()
    return ""


def merge_snapshot(existing, person_id: int, text: str) -> str:
    """
    Снимок строки: `12=занят до 18:30|15=свободен`. Правим только своего человека.

    Пустой текст убирает запись: день стёрт — значит и в снимке его быть не должно.
    """
    pairs: dict[str, str] = {}
    for chunk in str(existing or "").split(PAIR_SEP):
        chunk = chunk.strip()
        if not chunk or KV_SEP not in chunk:
            continue
        key, value = chunk.split(KV_SEP, 1)
        key = key.strip()
        if key.isdigit():
            pairs[key] = value.strip()
    if text:
        pairs[str(person_id)] = text
    else:
        pairs.pop(str(person_id), None)
    return PAIR_SEP.join(f"{key}{KV_SEP}{value}" for key, value in sorted(pairs.items()))


def plan_week(
    values: list[list],
    cards: dict[date, str],
    person_id: int,
    display_name: str,
    date_column: int = 2,
    aliases: Iterable[str] = (),
) -> Plan:
    """
    Карточки недели → адреса и тексты.

    `cards` — дата → канонический текст карточки (пустая строка значит «стёрто»).
    Возвращает план и список расхождений; при расхождениях писать нельзя.
    """
    plan = Plan()
    names_row = find_names_row(values)
    plan.names_row = names_row
    if names_row is None:
        plan.problems.append("names_row_not_found")
        return plan

    columns = find_person_columns(values, names_row, display_name, aliases)
    if not columns:
        plan.problems.append(f"person_not_found: {display_name}")
        return plan
    if len(columns) > 1:
        plan.problems.append(f"person_ambiguous: {display_name} в колонках {columns}")
        return plan
    person_column = columns[0]
    plan.person_column = person_column

    service_column, _ = find_service_column(values, names_row)
    plan.service_column = service_column

    day_rows = find_day_rows(values, date_column)
    header = values[names_row - 1]
    for day, text in sorted(cards.items()):
        row = day_rows.get(day)
        if row is None:
            plan.problems.append(f"day_row_not_found: {day.isoformat()}")
            continue
        plan.cells.append(SheetCell(cell_a1(row, person_column), text))
        existing = ""
        if len(values) >= row and len(values[row - 1]) >= service_column:
            existing = values[row - 1][service_column - 1]
        plan.cells.append(
            SheetCell(cell_a1(row, service_column),
                      merge_snapshot(existing, person_id, text))
        )

    if service_column > len(header):
        plan.cells.append(SheetCell(cell_a1(names_row, service_column), SERVICE_HEADER))
    return plan


def same_day(left: str, right: str) -> bool:
    """Один и тот же день занятости, даже если записан по-разному."""
    left_card, right_card = parse(left), parse(right)
    if left_card is None or right_card is None:
        return " ".join(left.split()).lower() == " ".join(right.split()).lower()
    if to_hours(left_card) != to_hours(right_card):
        return False
    return " ".join(left_card.note.split()).lower() == " ".join(right_card.note.split()).lower()


def snapshot_mismatches(
    values: list[list],
    person_id: int,
    display_name: str,
    date_column: int = 2,
    aliases: Iterable[str] = (),
) -> list[dict]:
    """
    Где человек правил руками после записи бота.

    Сравниваем клетку дня со снимком `svoi`: пустой снимок — бот сюда не писал;
    снимок совпал — правок нет; расходится — правку человека надо перенести в базу.
    В записи отдаём строку листа: по ней потом обновляется снимок.

    Сравнение смысловое, а не посимвольное: «занят до 19:00» и «свободен с 19:00» —
    одна и та же занятость, записанная с двух сторон, и расхождением не считаются.
    Сравниваем то, что реально ляжет в базу: часы дня и комментарий.
    Текст, который не разбирается, сравниваем как строку — угадывать в нём нечего.
    """
    names_row = find_names_row(values)
    if names_row is None:
        return []
    columns = find_person_columns(values, names_row, display_name, aliases)
    if len(columns) != 1:
        return []
    person_column = columns[0]
    service_column, created = find_service_column(values, names_row)
    if created:
        return []
    day_rows = find_day_rows(values, date_column)
    by_row = {row: day for day, row in day_rows.items()}
    out = []
    for row, day in sorted(by_row.items()):
        line = values[row - 1] if row - 1 < len(values) else []
        current = str(line[person_column - 1] or "").strip() if len(line) >= person_column else ""
        snapshot_raw = line[service_column - 1] if len(line) >= service_column else ""
        mine = snapshot_get(snapshot_raw, person_id)
        if not mine:
            continue
        if not same_day(current, mine):
            out.append({"day": day.isoformat(), "cell": current, "bot": mine, "row": row})
    return out
