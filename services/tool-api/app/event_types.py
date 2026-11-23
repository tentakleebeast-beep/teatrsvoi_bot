"""Типы и цвета событий календаря: одно место правды (docs/development/190 §6–§8).

Зачем отдельный модуль. До этой карты тип события был зашит в три словаря кода
(названия в ядре, цвета в ядре и локальная копия названий в `assign.describe`),
а цвета — ещё и в файле палитры. Организатор не мог ни назначить цвет типу, ни
завести свой тип без правки кода и выката.
Теперь словарь живёт в таблице `calendar_event_type` (миграция 074), а код читает
его отсюда — и только отсюда (I-1 документа 190).

Правила модуля:

1. Буква и название — оформление, код — стабильный внутренний ключ. Код нового
   типа собирается транслитерацией названия и после создания не меняется, даже
   если название переименовали (I-2).
2. Активный тип полон: у него есть буква и цвет (это же держит `check` в схеме).
   Неактивные `meeting`/`other` живут без буквы, `project` — без цвета: старые
   события сохраняют тип, выбрать их при создании нельзя.
3. Цвет события — `colorId` Google. При записи берём цвет типа из настройки, при
   показе — из самого события; пустой `colorId` не ошибка (I-5).
4. Валидация списка типов ничего не сохраняет, пока не проверен весь список:
   любая ошибка — ответ с `field`/`code` элемента, база не тронута (§6.3).

Тесты: `tests/unit/test_event_types.py`, `test_event_type_validation.py`,
`test_event_type_recolor.py`, `test_event_types_no_palette_in_services.py`.
"""

from __future__ import annotations

import re

from app import core, google_targets
from app.errors import ApiError
from app.external import GOOGLE_API_ERRORS

#: Палитра Google: 11 цветов события и их русские названия (190 §6.2).
#: Те же слова, что в историческом файле палитры; шестнадцатеричные значения —
#: оформление в `static/webapp-ui.css` (`.ev-color-1…11`), в коде данных нет.
CALENDAR_COLORS: tuple[dict, ...] = (
    {"color_id": 1, "name": "Лаванда"},
    {"color_id": 2, "name": "Шалфей"},
    {"color_id": 3, "name": "Виноград"},
    {"color_id": 4, "name": "Фламинго"},
    {"color_id": 5, "name": "Банан"},
    {"color_id": 6, "name": "Мандарин"},
    {"color_id": 7, "name": "Павлин"},
    {"color_id": 8, "name": "Графит"},
    {"color_id": 9, "name": "Голубика"},
    {"color_id": 10, "name": "Базилик"},
    {"color_id": 11, "name": "Помидор"},
)

#: Допустимые цвета для проверки `color_id` (1…11, 190 §6.3).
COLOR_IDS = tuple(item["color_id"] for item in CALENDAR_COLORS)

#: Транслитерация названия нового типа в код: 33 буквы + `ё` (190 §6.3).
TRANSLIT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "u", "я": "a",
}

#: Границы полей типа (190 §6.3, §7.1): код строится сервером, буква — одна заглавная.
LETTER_RE = re.compile(r"^[А-ЯЁ]$")
MAX_CODE_LENGTH = 32
MAX_TITLE_LENGTH = 40

#: Что показать, когда тип не определён ни маркером, ни меткой (190 §3.2).
NO_KIND_TITLE = "Без типа"


def types(conn) -> list[dict]:
    """Все типы, включая неактивные: порядок — `sort_order, title` (190 §6.2)."""
    rows = conn.execute(
        "select code, letter, title, color_id, sort_order, is_active "
        "from calendar_event_type order by sort_order, title").fetchall()
    return [{"code": row["code"], "letter": row["letter"], "title": row["title"],
             "color_id": row["color_id"], "sort_order": row["sort_order"],
             "is_active": row["is_active"]} for row in rows]


def by_code(conn) -> dict[str, dict]:
    """Типы по коду: единственная карта, которой пользуются карточка и сверка."""
    return {item["code"]: item for item in types(conn)}


def by_letter(conn) -> dict[str, dict]:
    """Типы по букве метки. У неактивных `meeting`/`other` буквы нет — их тут нет."""
    return {item["letter"]: item for item in types(conn) if item["letter"]}


def titles(conn) -> dict[str, str]:
    """Код → название: подписи в карточке, уведомлениях и разборе заголовка."""
    return {item["code"]: item["title"] for item in types(conn)}


def colors(conn) -> dict[str, int | None]:
    """Код → `colorId` настройки. У типа без цвета — `None`, и это не ошибка."""
    return {item["code"]: item["color_id"] for item in types(conn)}


def letters(conn) -> dict[str, str | None]:
    """Код → буква метки. У `meeting`/`other` буквы нет."""
    return {item["code"]: item["letter"] for item in types(conn)}


def color_of(conn, code: str | None) -> int | None:
    """Цвет настройки для кода типа (при записи события в календарь)."""
    if not code:
        return None
    return colors(conn).get(code)


def letter_of(conn, code: str | None) -> str | None:
    """Буква метки для кода типа (при сборке `summary` вида `[С] Название`)."""
    if not code:
        return None
    return letters(conn).get(code)


def resolve(conn, svoi_kind: str | None, letter: str | None) -> dict:
    """Тип события по правилу I-3: маркер `svoi_kind`, затем метка `[X]`, затем «без типа».

    Возвращает `{"letter", "kind", "kind_title"}`; у ненайденного типа буква и код `None`,
    название — «Без типа». Порядок именно такой: маркер бота главнее текста заголовка.
    """
    mapping = by_code(conn)
    if svoi_kind and svoi_kind in mapping:
        item = mapping[svoi_kind]
        return {"letter": item["letter"], "kind": item["code"],
                "kind_title": item["title"]}
    if letter:
        found = by_letter(conn).get(letter)
        if found:
            return {"letter": found["letter"], "kind": found["code"],
                    "kind_title": found["title"]}
        return {"letter": letter, "kind": None, "kind_title": NO_KIND_TITLE}
    return {"letter": None, "kind": svoi_kind or None, "kind_title": NO_KIND_TITLE}


def slug(title: str) -> str:
    """Транслитерация названия в код: `Концерт` → `koncert` (190 §6.3).

    Всё, что не `[a-z0-9]`, становится `_`; повторы сжимаются; обрезка до 32 знаков;
    код обязан начинаться с буквы (так требует схема) — иначе берётся `tip`.
    """
    lowered = str(title or "").strip().lower()
    parts = [TRANSLIT.get(char, char) for char in lowered]
    raw = "".join(parts)
    cleaned = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    cleaned = re.sub(r"_{2,}", "_", cleaned)[:MAX_CODE_LENGTH]
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"tip_{cleaned}".strip("_")[:MAX_CODE_LENGTH]
    if len(cleaned) < 2:
        cleaned = f"{cleaned or 'tip'}_1"[:MAX_CODE_LENGTH]
    return cleaned


def next_code(conn, title: str, taken: set[str] | None = None) -> str:
    """Свободный код для нового типа: `koncert`, затем `koncert_2`, `koncert_3`."""
    base = slug(title)
    used = {item["code"] for item in types(conn)} | set(taken or ())
    if base not in used:
        return base
    suffix = 2
    while f"{base}_{suffix}"[:MAX_CODE_LENGTH] in used:
        suffix += 1
    return f"{base}_{suffix}"[:MAX_CODE_LENGTH]


def _problem(code: str, status: int, field: str, index: int, detail: str) -> dict:
    return {"code": code, "status": status, "field": field, "index": index,
            "detail": detail}


def validate(conn, items: list[dict], *, apply_mode: str | None) -> list[dict]:
    """Проверить весь список типов до записи (190 §6.3). Пустой список — всё верно.

    Возвращает список расхождений, каждое — с полем, кодом и HTTP-статусом. Первое
    расхождение становится ответом; база при этом не меняется вовсе.
    """
    problems: list[dict] = []
    if apply_mode not in ("all", "new"):
        problems.append(_problem("apply_required", 400, "apply", -1,
                                 "Не выбрано, применять ли цвета к событиям."))
    known = by_code(conn)
    seen_letters: dict[str, int] = {}
    seen_codes: set[str] = set()

    for index, item in enumerate(items):
        code = (item.get("code") or "").strip() or None
        letter = (item.get("letter") or "").strip() or None
        title = str(item.get("title") or "").strip()
        color_id = item.get("color_id")
        sort_order = item.get("sort_order")
        is_active = bool(item.get("is_active", True))

        existing = None
        if code is not None:
            if code not in known:
                problems.append(_problem("code_unknown", 404, "code", index,
                                         f"Типа с кодом {code} нет."))
                continue
            if code in seen_codes:
                problems.append(_problem("code_unknown", 404, "code", index,
                                         f"Код {code} прислан дважды."))
                continue
            seen_codes.add(code)
            existing = known[code]

        if not (1 <= len(title) <= MAX_TITLE_LENGTH):
            problems.append(_problem("title_bad", 400, "title", index,
                                     "Название — от 1 до 40 знаков."))
        if not isinstance(sort_order, int) or isinstance(sort_order, bool) \
                or not 0 <= sort_order <= 9999:
            problems.append(_problem("order_bad", 400, "sort_order", index,
                                     "Порядок — целое от 0 до 9999."))

        if letter is not None and not LETTER_RE.match(letter):
            problems.append(_problem("letter_bad", 400, "letter", index,
                                     "Буква — одна заглавная кириллическая."))
        elif letter is not None:
            if existing is not None and existing["letter"] != letter:
                problems.append(_problem(
                    "letter_immutable", 409, "letter", index,
                    f"Буква типа «{existing['title']}» не меняется: {existing['letter']}."))
            previous = seen_letters.get(letter)
            if previous is not None:
                problems.append(_problem("letter_taken", 409, "letter", index,
                                         f"Буква «{letter}» уже занята другим типом."))
            else:
                taken = next((item["code"] for item in known.values()
                              if item["letter"] == letter and item["code"] != code), None)
                if taken:
                    problems.append(_problem(
                        "letter_taken", 409, "letter", index,
                        f"Буква «{letter}» уже занята типом {taken}."))
            seen_letters[letter] = index

        if color_id is not None:
            if isinstance(color_id, bool) or color_id not in COLOR_IDS:
                problems.append(_problem("color_bad", 400, "color_id", index,
                                         "Цвет — число от 1 до 11."))
        if is_active and (letter is None or color_id is None):
            problems.append(_problem("color_required", 400, "color_id", index,
                                     "У активного типа задаются буква и цвет."))
    return problems


def save(conn, items: list[dict], *, actor_id: int | None,
         apply_mode: str) -> dict:
    """Записать проверенный список типов и вернуть то, что изменилось по цвету.

    Вызывается только после `validate`: здесь нет отказов валидации, но есть одна
    транзакция — либо весь список записан, либо ничего. Существующий тип без `code`
    получает код транслитерацией; `title` можно переименовать, код не меняется.
    """
    before = by_code(conn)
    changed_colors: list[str] = []
    taken: set[str] = set()

    for item in items:
        code = (item.get("code") or "").strip() or None
        letter = (item.get("letter") or "").strip() or None
        title = str(item.get("title") or "").strip()
        color_id = item.get("color_id")
        sort_order = int(item.get("sort_order") or 0)
        is_active = bool(item.get("is_active", True))

        if code is None:
            code = next_code(conn, title, taken)
            taken.add(code)
            conn.execute(
                """insert into calendar_event_type
                       (code, letter, title, color_id, sort_order, is_active, updated_by)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (code, letter, title, color_id, sort_order, is_active, actor_id))
            continue

        previous = before.get(code) or {}
        if previous.get("color_id") != color_id:
            changed_colors.append(code)
        conn.execute(
            """update calendar_event_type
                  set letter = %s, title = %s, color_id = %s, sort_order = %s,
                      is_active = %s, updated_at = now(), updated_by = %s
                where code = %s""",
            (letter, title, color_id, sort_order, is_active, actor_id, code))

    after = by_code(conn)
    core.audit(conn, "human", actor_id, "event_types.save", "calendar_event_type", "types",
               before={"types": sorted(before)}, after={"types": sorted(after),
                                                        "apply": apply_mode})
    return {"changed": changed_colors, "before": before, "after": after}


def recolor(conn, changed: list[str], *, apply_mode: str, actor_id: int | None,
            write_service=None) -> dict:
    """Перекрасить события изменённых типов (§6.5).

    `apply: "new"` не делает ни одного пишущего вызова. `apply: "all"` правит
    `colorId` только у событий изменённых типов и только в календаре своего режима:
    в переходном — черновой (`calendar.draft`), в целевом — боевой (`calendar.read`,
    решение владельца V-120 б, исключение из V-072). Людские события без маркера
    сюда не попадают: список строится по `event_plan`.
    """
    if apply_mode != "all" or not changed:
        return {"recolored": 0, "total": 0, "failed": [], "failed_more": 0}

    mode = google_targets.google_mode(conn)
    if mode == google_targets.MODE_TARGET:
        calendar_id = google_targets.read_id("calendar.read")
    else:
        try:
            calendar_id = google_targets.resolve("calendar.draft")
        except ApiError as exc:
            return {"recolored": 0, "total": 0, "failed": [],
                    "failed_more": 0, "error": exc.code}

    settings = colors(conn)
    rows = conn.execute(
        "select id, kind, gcal_event_id from event_plan where kind = any(%s) "
        "and gcal_event_id is not null order by starts_at", (changed,)).fetchall()
    service = write_service or core._calendar_write()
    recolored = 0
    failed: list[dict] = []
    failed_more = 0
    for row in rows:
        color_id = settings.get(row["kind"])
        if color_id is None:
            continue
        try:
            service.events().update(calendarId=calendar_id, eventId=row["gcal_event_id"],
                                    body={"colorId": str(color_id)}).execute()
            recolored += 1
        except GOOGLE_API_ERRORS as exc:
            if len(failed) < 50:
                failed.append({"plan_id": row["id"], "gcal_event_id": row["gcal_event_id"],
                               "error": str(exc)[:120]})
            else:
                failed_more += 1
    core.audit(conn, "human", actor_id, "event_types.recolor", "calendar_event_type",
               "types", before={"changed": sorted(changed)},
               after={"recolored": recolored, "total": len(rows),
                      "calendar": "read" if mode == google_targets.MODE_TARGET else "draft",
                      "apply": apply_mode})
    return {"recolored": recolored, "total": len(rows), "failed": failed,
            "failed_more": failed_more}
