"""Кнопки и тексты шага опроса — без aiogram: собираются списками (E8, docs/162 §14.4).

Зачем отдельно: `callback_data` ограничена 64 байтами у Telegram, а спецификация
держит более строгий предел — 18 байт в худшем случае (семизначный `run`, двузначный
код варианта). Проверять это на живом Telegram поздно, поэтому сборка и разбор кода
живут в чистом модуле, а тест `test_poll_keyboard.py` меряет длину до отправки.

Позиция вопроса в коде — `position`, а не `id`: обработчик не делает лишний запрос,
и в логах Telegram видно «вопрос 2», а не «id 481». Вариант — короткий код `a1`,
а не `id`: `id` растёт и когда-нибудь упрётся в лимит (D3).
"""

from __future__ import annotations

#: Предел `callback_data` для опроса. Telegram разрешает 64, но держим 18:
#: код собирается из коротких частей и остаётся читаемым в логах.
CB_LIMIT = 18

KIND_TITLES = {"single": "один из списка", "multi": "несколько из списка",
               "yesno": "да или нет", "number": "число", "text": "свободный ответ"}

#: Действия шага → префикс кода. `p:o` — вариант, `p:n` — «Дальше» и т. д.
ACTIONS = {"o": "o", "n": "n", "y": "y", "x": "x", "b": "b", "s": "s", "f": "f", "q": "q"}


def callback(action: str, run_id: int, position: int | None = None,
             code: str | None = None) -> str:
    """Собрать `callback_data` шага. Форма — `p:<action>:<run>[:<pos>[:<code>]]`."""
    parts = ["p", ACTIONS[action], str(int(run_id))]
    if position is not None:
        parts.append(str(int(position)))
    if code:
        parts.append(str(code))
    data = ":".join(parts)
    if len(data.encode()) > CB_LIMIT:
        raise ValueError(f"callback_data длиннее {CB_LIMIT} байт: {data}")
    return data


def parse(data: str) -> dict | None:
    """Разобрать код шага. `None` — не наш код или он испорчен (молча игнорируем)."""
    parts = str(data or "").split(":")
    if len(parts) < 3 or parts[0] != "p" or parts[1] not in ACTIONS:
        return None
    try:
        run_id = int(parts[2])
        position = int(parts[3]) if len(parts) > 3 else None
    except ValueError:
        return None
    return {"action": parts[1], "run_id": run_id, "position": position,
            "code": parts[4] if len(parts) > 4 else None}


#: Вопросы, у которых ответ приходит сообщением: вариантов у них нет (162 §5.2).
FREE_KINDS = ("text", "number")

#: Подпись кнопки входа в ответ сообщением. Одна на шаг и на приглашение:
#: человек видит в тексте ту же фразу, что на кнопке.
ANSWER_BUTTON = "Ответить сообщением"


def question_text(step: dict) -> str:
    """Текст сообщения вопроса. Первая строка — прогресс словами (AC-7).

    «вопрос N из M», а не полоска: в чате Telegram полос нет, а слово переносится
    в уведомление и в превью (docs/162 §5.1). На возврате к отвеченному вопросу
    добавляется «выбрано: …» — иначе человек не понимает, что ответ уже есть.
    """
    lines = [f"{step['title']} · вопрос {step['position']} из {step['total']}", ""]
    lines.append(step["text"])
    if step["kind"] == "number":
        lines += ["", f"Нажмите «{ANSWER_BUTTON}» и пришлите число."]
    elif step["kind"] == "text":
        lines += ["", f"Нажмите «{ANSWER_BUTTON}» и пришлите ответ."]
    chosen = _chosen_words(step)
    if chosen:
        lines += ["", f"выбрано: {chosen}"]
    elif step.get("chosen_text"):
        lines += ["", f"ваш ответ: {step['chosen_text']}"]
    return "\n".join(lines)


def _chosen_words(step: dict) -> str:
    codes = step.get("chosen") or []
    if not codes:
        return ""
    if step["kind"] == "yesno":
        labels = {"y": "Да", "n": "Нет"}
    else:
        labels = {option["code"]: option["label"] for option in step.get("options") or []}
    return ", ".join(str(labels.get(code, code)) for code in codes)


def keyboard_rows(step: dict) -> list[list[tuple[str, str]]]:
    """Кнопки шага: варианты, «Дальше», «Назад»/«Пропустить», «Другой ответ».

    Варианты — по одному в строку: подпись «Расписание студийцы» в две колонки
    обрезается (проверено мокапом §5.1). «Пропустить» — только у необязательного
    вопроса, «Назад» — везде, кроме первого (AC-6).
    """
    run_id = int(step["run_id"])
    position = int(step["position"])
    rows: list[list[tuple[str, str]]] = []
    if step["kind"] == "yesno":
        rows.append([("Да", callback("y", run_id, position)),
                     ("Нет", callback("x", run_id, position))])
    else:
        for option in step.get("options") or []:
            rows.append([(option["label"], callback("o", run_id, position, option["code"]))])
    if step["kind"] == "multi":
        rows.append([("Дальше", callback("n", run_id, position))])
    if step["free_text"]:
        rows.append([("Другое — напишу", callback("f", run_id, position))])
    elif step["kind"] in FREE_KINDS:
        # У `text`/`number` вариантов нет, поэтому вход в ответ сообщением — кнопкой,
        # а не состоянием: оно ставится по нажатию и переживает перезапуск gateway.
        rows.append([(ANSWER_BUTTON, callback("f", run_id, position))])
    nav: list[tuple[str, str]] = []
    if position > 1:
        nav.append(("Назад", callback("b", run_id, position)))
    if not step["required"]:
        nav.append(("Пропустить", callback("s", run_id, position)))
    if nav:
        rows.append(nav)
    return rows


def closed_text(step: dict) -> str:
    """Опрос закрыт: одно сообщение без кнопок (§14.4 п. 7)."""
    until = (step.get("closes_at") or "")[:10]
    return (f"Опрос закрыт {until}, ответы больше не принимаются." if until
            else "Опрос закрыт, ответы больше не принимаются.")


def done_text(step: dict) -> str:
    """Человек прошёл последний вопрос: ответов N из M."""
    return f"Спасибо, ответов {step['position']} из {step['total']}. Результаты увидит организатор."
