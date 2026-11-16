"""Опросы: разбор и проверка данных без БД и без FastAPI (E8, docs/development/162 §14).

Зачем отдельный модуль: типы вопросов, коды вариантов, набор адресатов, сводка и
экспорт — это правила, а не запросы. Их проверяют unit-тесты (`tests/unit/test_poll_*`),
а обработчики (`app/api/polls.py`), страницы и роутер чата только вызывают.

Границы: здесь нет ни `psycopg`, ни `fastapi`, ни `aiogram` — модуль импортируется
тестами и страницами без поднятия сервиса. Коды ошибок — те же строки, что в
контракте §14.2 (`400 empty_title`, `bad_kind`, `too_many_options`, …).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from app.availability_coverage import plural
from services.shared import format as shared_format

#: Пять типов вопроса (D2). Варианты есть только у двух: у `yesno` два ответа
#: рисуются кодом, у `number`/`text` их нет вовсе.
KINDS = ("single", "multi", "yesno", "number", "text")
OPTION_KINDS = ("single", "multi")

#: Предел вариантов на вопрос: код двухбайтовый (`a1…z9`), 26 × 9 = 234.
MAX_OPTIONS = 234

#: Коды «да»/«нет» фиксированы: их не придумывает конструктор.
YESNO_CODES = (("y", "Да"), ("n", "Нет"))

#: Колонка CSV. Порядок полей — часть контракта (AC-11): по нему читает LibreOffice.
CSV_HEADER = ("вопрос", "тип", "человек", "ответ", "когда")

#: Предел темы опроса (`save` зовёт `clean_text(body.title, limit=300)`).
TITLE_LIMIT = 300

#: Суффикс темы копии (166 §2, D3): «Анкета организатору — копия».
COPY_SUFFIX = " — копия"

#: Длина, до которой срезается тема источника: суффикс входит в предел 300 (166 §2).
COPY_TITLE_LIMIT = 290


class DraftError(ValueError):
    """Черновик опроса не проходит проверку. `code` — код ошибки контракта."""

    def __init__(self, code: str, text: str = "") -> None:
        super().__init__(text or code)
        self.code = code
        self.text = text or code


def next_codes(count: int) -> list[str]:
    """Коды вариантов: `a1…a9`, `b1…b9`, … (буква — десяток позиции).

    Код, а не `id`: `id` растёт и когда-нибудь упрётся в лимит `callback_data`,
    а два байта хватает на 234 варианта (D3, §14.1 п. 4).
    """
    if count < 0:
        raise DraftError("bad_options", "Число вариантов не может быть отрицательным")
    if count > MAX_OPTIONS:
        raise DraftError("too_many_options",
                         f"Вариантов больше {MAX_OPTIONS}: конструктор столько не хранит")
    out: list[str] = []
    for index in range(count):
        letter = chr(ord("a") + index // 9)
        out.append(f"{letter}{index % 9 + 1}")
    return out


def clean_text(value: Any, *, limit: int = 2000) -> str:
    """Строка без управляющих символов и лишних пробелов; длиннее лимита — отказ."""
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if len(text) > limit:
        raise DraftError("too_long", f"Текст длиннее {limit} символов")
    return text


def validate_questions(raw: Iterable[dict]) -> list[dict]:
    """Проверить вопросы черновика и вернуть их в нормальном виде.

    Проверки — те, которые держит и схема, но ответ обязан быть кодом ошибки, а не
    `500` от Postgres: конструктор шлёт черновик целиком, и одна опечатка не должна
    ломать сохранение остального.
    """
    questions: list[dict] = []
    for index, item in enumerate(raw, start=1):
        position = int(item.get("position") or index)
        if position < 1:
            raise DraftError("bad_position", "Порядок вопроса начинается с 1")
        kind = str(item.get("kind") or "single")
        if kind not in KINDS:
            raise DraftError("bad_kind", f"Неизвестный тип вопроса: {kind}")
        text = clean_text(item.get("text"))
        if not text:
            raise DraftError("empty_question", f"Вопрос {position} без текста")
        free_text = bool(item.get("free_text"))
        if free_text and kind not in OPTION_KINDS:
            raise DraftError("bad_free_text",
                             "Свободный ответ бывает только у вопроса со списком")
        max_choices = item.get("max_choices")
        if max_choices is not None:
            max_choices = int(max_choices)
            if max_choices < 1:
                raise DraftError("bad_max_choices", "«Не больше» начинается с одного")
        options: list[dict] = []
        if kind in OPTION_KINDS:
            labels = [clean_text(option.get("label") if isinstance(option, dict) else option,
                                 limit=300)
                      for option in (item.get("options") or [])]
            labels = [label for label in labels if label]
            if not labels:
                raise DraftError("no_options", f"У вопроса {position} нет вариантов")
            options = [{"code": code, "position": number, "label": label}
                       for number, (code, label) in enumerate(
                           zip(next_codes(len(labels)), labels, strict=True), 1)]
        questions.append({
            "position": position,
            "text": text,
            "kind": kind,
            "required": bool(item.get("required", True)),
            "free_text": free_text,
            "max_choices": max_choices,
            "options": options,
        })
    if not questions:
        raise DraftError("no_questions", "В опросе нет ни одного вопроса")
    positions = [question["position"] for question in questions]
    if len(set(positions)) != len(positions):
        raise DraftError("bad_position", "Два вопроса с одинаковым порядком")
    return sorted(questions, key=lambda question: question["position"])


def draft_copy(source: dict, questions: Iterable[dict]) -> dict:
    """Тело `PollSaveIn` для копии опроса: только наполнение анкеты (166 §2, D3).

    Переносится тема с суффиксом « — копия», `intro`, `anonymous` и вопросы
    с вариантами — порядок, тип, обязательность, свободный ответ и «не больше».
    Не переносится ничего, что делает опрос отправленным: `closes_at`,
    `started_at`, `closed_at`, адресаты, зрители и прохождения. Коды вариантов
    пересчитывает `validate_questions` (`next_codes`): старые не переезжают,
    потому что ссылаться на них в копии некому.

    Вопросов в копии быть не может меньше одного: пустой источник — отказ
    `no_questions` тем же кодом, что у `save`.
    """
    items = []
    for index, question in enumerate(questions, start=1):
        items.append({
            "position": int(question.get("position") or index),
            "text": question.get("text"),
            "kind": str(question.get("kind") or "single"),
            "required": bool(question.get("required", True)),
            "free_text": bool(question.get("free_text")),
            "max_choices": question.get("max_choices"),
            "options": [{"label": option.get("label")}
                        for option in (question.get("options") or [])],
        })
    if not items:
        raise DraftError("no_questions", "В опросе нет вопросов: копировать нечего.")
    return {"title": copy_title(source.get("title")),
            "intro": clean_text(source.get("intro"), limit=2000) or None,
            "anonymous": bool(source.get("anonymous")),
            "questions": validate_questions(items)}


def copy_title(title: Any) -> str:
    """Тема копии: текст источника без хвоста и суффикс « — копия».

    Срез, а не `clean_text(limit=…)`: `clean_text` на длинном тексте не обрезает,
    а отказывает (`too_long`), и копия темы в 300 знаков падала бы на ровном месте.
    Предел темы у `save` — 300 знаков, суффикс входит в него, поэтому источник
    срезается до 290. Хвост длинной темы теряется — предел назван в 166 §2.
    """
    text = " ".join(str(title or "").replace("\x00", " ").split())
    return text[:COPY_TITLE_LIMIT].rstrip() + COPY_SUFFIX


def answer_codes(kind: str, codes: Iterable[str]) -> list[str]:
    """Привести набор выбранных кодов к правилам типа вопроса.

    `single`/`yesno` — ровно один код (последний выбор заменяет предыдущий),
    `multi` — порядок сохраняется, дубли убираются. У `number`/`text` кодов нет.
    """
    unique: list[str] = []
    for code in codes:
        code = str(code or "")
        if code and code not in unique:
            unique.append(code)
    if kind in ("single", "yesno"):
        return unique[-1:]
    if kind == "multi":
        return unique
    return []


def merge_choice(kind: str, current: Iterable[str], code: str,
                 allowed: Iterable[str] | None = None,
                 max_choices: int | None = None) -> list[str]:
    """Нажатие варианта: `single` заменяет, `multi` переключает (D3, §14.4 п. 3).

    Повторное нажатие того же кода у `multi` снимает выбор — человек так исправляет
    ошибку до «Дальше». У `single`/`yesno` предыдущий выбор просто заменяется,
    поэтому строка ответа остаётся одна (`on conflict`).
    """
    if kind in ("single", "yesno"):
        return [code] if code else []
    if kind != "multi":
        return []
    merged = [item for item in current]
    if code in merged:
        merged.remove(code)
    else:
        merged.append(code)
    if allowed is not None:
        permitted = set(allowed)
        merged = [item for item in merged if item in permitted]
    if max_choices:
        merged = merged[:max_choices]
    return merged


def parse_number(text: str) -> Decimal | None:
    """Число из сообщения: запятая и точка равноправны (D2, §14.4 п. 4).

    Возвращает `None`, если это не число: вызывающий отвечает «Не понял число»
    и остаётся на том же вопросе.
    """
    cleaned = " ".join(str(text or "").replace(",", ".").split())
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return value


def audience_ids(*, rules: Iterable[dict], include: Iterable[int], exclude: Iterable[int],
                 people: Iterable[dict],
                 project_members: dict[int, Iterable[int]]) -> list[int]:
    """Кого накрывает набор: объединение правил + галочки − исключения (D6, AC-3).

    `people` — действующие участники (`id`, `roles`); `project_members` — состав
    спектакля (множество `participant_id` из событий проекта). Исключение сильнее
    любого правила: «все организаторы, кроме меня» — нормальная просьба.
    """
    by_id: dict[int, set[str]] = {
        int(person["id"]): set(person.get("roles") or ()) for person in people
    }
    project_sets = {int(pid): {int(member) for member in members}
                    for pid, members in project_members.items()}
    chosen: set[int] = set()
    for rule in rules:
        kind = str(rule.get("kind") or "")
        if kind == "all_active":
            chosen.update(by_id)
        elif kind == "role":
            role = str(rule.get("role") or "")
            if role not in ("admin", "org", "master", "troupe"):
                raise DraftError("bad_rule", f"Неизвестная роль в наборе: {role}")
            chosen.update(pid for pid, roles in by_id.items() if role in roles)
        elif kind == "project":
            project_id = int(rule.get("project_id") or 0)
            if not project_id:
                raise DraftError("bad_rule", "Набор «по спектаклю» без спектакля")
            chosen.update(pid for pid in project_sets.get(project_id, set()) if pid in by_id)
        else:
            raise DraftError("bad_rule", f"Неизвестный набор: {kind}")
    chosen.update(int(pid) for pid in include if int(pid) in by_id)
    chosen.difference_update(int(pid) for pid in exclude)
    return sorted(chosen)


def answer_label(question: dict, codes: Iterable[str], text_value: str | None,
                 number_value: Any = None) -> str:
    """Ответ словами — для CSV, сводки и пометки «выбрано: …» на прошлом вопросе."""
    if number_value is not None:
        return _number_text(number_value)
    if text_value:
        return str(text_value)
    labels = {option["code"]: option["label"] for option in question.get("options") or []}
    if question["kind"] == "yesno":
        labels = dict(YESNO_CODES)
    return ", ".join(labels.get(code, code) for code in codes)


def summarize(question: dict, answers: Iterable[dict]) -> dict:
    """Сводка одного вопроса (AC-10). Формы — как в §14.2, по типу вопроса.

    `answers` — строки ответов этого вопроса с `participant_id`, `display_name`,
    `option_codes`, `text_value`, `number_value`, `skipped`, `answered_at`.
    Пропущенные вопросы в счётчики не входят, но и не теряются: `skipped` считается
    отдельно, чтобы «ответило 4 из 7» не выглядело поломкой.
    """
    rows = [row for row in answers if not row.get("skipped")]
    kind = question["kind"]
    answered = len(rows)
    skipped = sum(1 for row in answers if row.get("skipped"))
    if kind in ("single", "multi", "yesno"):
        options = (list(YESNO_CODES) if kind == "yesno"
                   else [(option["code"], option["label"])
                         for option in question.get("options") or []])
        counts = []
        for code, label in options:
            counts.append({"code": code, "label": label,
                           "n": sum(1 for row in rows if code in (row.get("option_codes") or []))})
        summary: dict = {"counts": counts, "answered": answered, "skipped": skipped}
        if kind == "multi":
            total = sum(len(row.get("option_codes") or []) for row in rows)
            summary["sum"] = total
            summary["sum_gt_answered"] = total > answered
        return summary
    if kind == "number":
        values = [row for row in rows if row.get("number_value") is not None]
        numbers = [Decimal(str(row["number_value"])) for row in values]
        return {
            "min": float(min(numbers)) if numbers else None,
            "avg": round(float(sum(numbers) / len(numbers)), 2) if numbers else None,
            "max": float(max(numbers)) if numbers else None,
            "values": [{"participant_id": row.get("participant_id"),
                        "display_name": row.get("display_name"),
                        "value": float(Decimal(str(row["number_value"])))}
                       for row in values],
            "answered": answered,
            "skipped": skipped,
        }
    return {"answers": [{"participant_id": row.get("participant_id"),
                         "display_name": row.get("display_name"),
                         "text": row.get("text_value") or "",
                         "answered_at": _iso(row.get("answered_at"))}
                        for row in rows],
            "answered": answered, "skipped": skipped}


def export_rows(poll: dict, questions: list[dict], answers: Iterable[dict],
                people: dict[int, str]) -> list[tuple[str, ...]]:
    """Строки CSV: один ответ — одна строка (AC-11)."""
    by_question = {int(question["id"]): question for question in questions}
    rows: list[tuple[str, ...]] = []
    for answer in answers:
        question = by_question.get(int(answer["question_id"]))
        if question is None or answer.get("skipped"):
            continue
        rows.append((
            f"{question['position']}. {question['text']}",
            question["kind"],
            people.get(int(answer["participant_id"]), ""),
            answer_label(question, answer.get("option_codes") or [],
                         answer.get("text_value"), answer.get("number_value")),
            _iso(answer.get("answered_at"))[:16].replace("T", " "),
        ))
    return rows


def export_csv(rows: Iterable[tuple[str, ...]]) -> str:
    """CSV с `;` и BOM: открывается в LibreOffice без пляски с кодировкой (AC-11)."""
    lines = [";".join(_csv_cell(cell) for cell in CSV_HEADER)]
    for row in rows:
        lines.append(";".join(_csv_cell(cell) for cell in row))
    return "\ufeff" + "\r\n".join(lines) + "\r\n"


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(char in text for char in ';"\n\r'):
        return '"' + text.replace('"', '""') + '"'
    return text


def export_markdown(poll: dict, questions: list[dict], summaries: dict[int, dict],
                    *, anonymous: bool = False) -> str:
    """Markdown-сводка, вставляется в журнал исследования без правки (AC-12)."""
    lines = [f"## {poll['title']}", ""]
    if poll.get("intro"):
        lines += [str(poll["intro"]), ""]
    for index, question in enumerate(questions, start=1):
        summary = summaries.get(int(question["id"]), {})
        lines += [f"## Вопрос {index}", "", f"**{question['text']}**", ""]
        if summary.get("counts"):
            for item in summary["counts"]:
                lines.append(f"- {item['label']}: {item['n']}")
            if summary.get("sum_gt_answered"):
                lines.append(f"- всего выборов: {summary['sum']} (больше ответивших: "
                             "у вопроса можно выбрать несколько)")
        elif "min" in summary:
            if summary.get("min") is None:
                lines.append("- чисел нет")
            else:
                lines.append(f"- минимум {summary['min']:g}, в среднем {summary['avg']:g}, "
                             f"максимум {summary['max']:g}")
        else:
            items = summary.get("answers") or []
            if not items:
                lines.append("- ответов нет")
            for item in items:
                author = "" if anonymous else f"{item.get('display_name') or 'участник'}: "
                lines.append(f"- {author}{item.get('text') or ''}")
        lines += ["", f"Ответили: {summary.get('answered', 0)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def esc(value: Any) -> str:
    """Экранирование для HTML на страницах опроса (страница — константа, данные — из БД)."""
    return "" if value is None else escape(str(value), quote=True)


def _number_text(value: Any) -> str:
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


# --- «Мои опросы»: одно чтение на три потребителя (E8-D4, docs/development/164 §12.2) --
# Три экрана спрашивают одно и то же: чат (`/tools/polls/mine`), счётчики Главной и
# список «Мои опросы». Отдельный `select` под каждый — тот дрейф, от которого уходил
# дизайн D5: у чата и приложения разошлись бы правила «какой опрос открыт». Поэтому
# запрос один, а фильтруют потребители: им нужны разные наборы из одних строк.


def my_runs(conn, participant_id: int) -> list[dict]:
    """Все прохождения человека: и открытые, и пройденные, и закрытые.

    Поля: `run_id`, `poll_id`, `title`, `status` (статус `poll_run`), `position`,
    `total`, `poll_status`, `closes_at`, `finished_at`, `closed_at`.
    `closed_at` — не для фильтра, а для подписи «опрос закрыт 12 ноября»: у опроса,
    закрытого организатором, срока `closes_at` может не быть вовсе.
    Порядок — `p.created_at desc, r.id` (как в `/tools/polls/mine`: свежий опрос выше).

    Соединение приходит аргументом: модуль по-прежнему не открывает его сам и
    импортируется тестами без базы.
    """
    rows = conn.execute(
        """select r.id as run_id, r.status, r.position, r.finished_at,
                  p.id as poll_id, p.title, p.status as poll_status,
                  p.closes_at, p.closed_at,
                  (select count(*) from poll_question q where q.poll_id = p.id) as total
             from poll_run r join poll p on p.id = r.poll_id
            where r.participant_id = %s
            order by p.created_at desc, r.id""",
        (participant_id,)).fetchall()
    return [dict(row) for row in rows]


def is_closed(run: dict, now: datetime | None = None) -> bool:
    """Опрос закрыт: организатор закрыл или вышел срок. Прохождение при этом любое."""
    if run.get("poll_status") == "closed":
        return True
    closes_at = run.get("closes_at")
    return bool(closes_at and closes_at <= (now or datetime.now(UTC)))


def is_open(run: dict, now: datetime | None = None) -> bool:
    """Прохождение ждёт ответа: начато или приглашено, а опрос ещё идёт.

    Это правило приложения. У чата своё, историческое (`poll_status = 'running'`
    без срока) — оно осталось в `api/polls.py`, чтобы контракт `/tools/polls/mine`
    не поехал (AC-18).
    """
    return run.get("status") in ("invited", "in_progress") and not is_closed(run, now)


def groups(runs: Iterable[dict], now: datetime | None = None) -> dict[str, list[dict]]:
    """Три группы «Моих опросов» в порядке показа: ждут ответа, пройденные, закрытые.

    Строка попадает ровно в одну группу: `done` сильнее срока (человек ответил —
    опрос для него пройден, даже если потом закрылся), а всё, что не открыто и не
    пройдено, — закрыто. Так `declined` (в E8 недостижим, но `check` его допускает)
    не теряется между группами.
    """
    result: dict[str, list[dict]] = {"open": [], "done": [], "closed": []}
    for run in runs:
        if run.get("status") == "done":
            result["done"].append(run)
        elif run.get("status") == "declined":
            # Резерв: в E8 отказа от опроса нет, но `check` схемы его допускает.
            result["closed"].append(run)
        elif is_open(run, now):
            result["open"].append(run)
        else:
            result["closed"].append(run)
    return result


def run_meta(run: dict, now: datetime | None = None) -> str:
    """Подпись строки: «3 вопроса · не начат», «вопрос 3 из 11 · продолжить»,
    «пройден 14 ноября · 11 из 11», «опрос закрыт 12 ноября».

    `invited` и `in_progress` различаются словами, а не цветом: «не начат» против
    «продолжить» человек читает, а цвет бейджа не читает никто.
    """
    total = int(run.get("total") or 0)
    position = int(run.get("position") or 0)
    status = run.get("status")
    if status == "done":
        day = _day_text(run.get("finished_at"))
        answered = position or total
        return (f"пройден {day} · {answered} из {total}" if day
                else f"пройден · {answered} из {total}")
    if status == "declined":
        return "опрос закрыт"
    if is_closed(run, now):
        day = _day_text(run.get("closed_at") or run.get("closes_at"))
        return f"опрос закрыт {day}" if day else "опрос закрыт"
    if status == "in_progress":
        return f"вопрос {max(position, 1)} из {total} · продолжить"
    return f"{total} {plural(total, 'вопрос', 'вопроса', 'вопросов')} · не начат"


def entry_meta(counts: dict) -> str:
    """Подпись строки-входа на Главной: «2 ждут ответа · 3 пройдено».

    Нулевая часть не печатается: «0 ждут ответа · 3 пройдено» — шум, а человеку
    важно только то, что у него есть.
    """
    open_count = int(counts.get("open") or 0)
    done_count = int(counts.get("done") or 0)
    parts = []
    if open_count:
        parts.append(f"{open_count} {plural(open_count, 'ждёт ответа', 'ждут ответа', 'ждут ответа')}")
    if done_count:
        parts.append(f"{done_count} пройдено")
    return " · ".join(parts)


def cancel_invites(conn, run_id: int, participant_id: int) -> None:
    """Отменить отложенное приглашение, если человек уже прошёл шаг (V-096).

    Приглашение — строка `notification` с `kind='poll'` и номером прохождения в
    `payload->>'run_id'`. Пока оно `scheduled` (push в 08:00) или `failed`
    (ошибка настройки, которую `retry_failed` вернул бы в очередь), ответ в
    приложении делает его ненужным: человек уже отвечает. `sent` и `skipped` не
    трогаем — отправленное не отзывается.

    `failed` в отмене не для полноты: `scripts/send_notifications.py:retry_failed`
    возвращает `failed` в очередь (`status='scheduled'`, `attempts=0`), и без этой
    строки push пришёл бы после пройденного опроса.
    """
    conn.execute(
        """update notification set status = 'cancelled',
                  last_error = 'пройдено в приложении'
            where recipient_id = %s and kind = 'poll' and sent_at is null
              and status in ('scheduled', 'failed')
              and payload->>'run_id' = %s""",
        (participant_id, str(run_id)))


def _day_text(value: Any) -> str:
    """Дата словами без дня недели: «14 ноября». Пустое значение — пустая строка."""
    return shared_format.human_date(value, with_weekday=False) if value else ""
