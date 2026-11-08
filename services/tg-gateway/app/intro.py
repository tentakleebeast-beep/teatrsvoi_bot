"""Сценарий «знакомство»: опросник после регистрации (V-009, обращение №17).

Решение Виктора 28.10.2026: спросить имя (если известно только одно слово),
короткое имя для бота, телефон и день рождения (если их ещё нет в профиле),
обычное время в пути, спектакли, роли и интересные тренинги. Тариф не спрашиваем —
его ставит организатор после договорённости.

Ответы копятся в состоянии диалога и пишутся в профиль **одним запросом только
после подтверждения**: перед сохранением бот показывает сводку «так записать?».
Правила разбирают ответы без LLM; модель подключается, только когда правила
промолчали, и только про поле текущего шага.
"""

from __future__ import annotations

import re

# Порядок вопросов. Состав плана зависит от того, что уже известно в профиле
# (см. `build_plan`), поэтому это база, а не жёсткий сценарий.
STEPS = ("name", "alias", "phone", "birthday", "travel", "plays", "roles",
         "trainings", "manifest")

QUESTIONS = {
    "name": ("Как вас зовут полностью — имя и фамилия? Так вас будет видно "
             "в расписании и финансовых листах."),
    "alias": ("Как вас называть в боте коротко? Одно слово — так проще узнавать "
              "вас в сообщениях. Например: Витя."),
    "phone": ("Телефон для связи? Напишите номер — он виден только вам и организатору. "
              "Можно пропустить."),
    "birthday": ("День рождения? Напишите дату с годом: «12.03.1990». "
                 "Нужен, чтобы поздравлять и не ставить репетиции в этот день."),
    "travel": ("Сколько минут обычно занимает дорога до студии? Напишите числом "
               "(«40») или словами («полчаса», «час»)."),
    "plays": ("В каких спектаклях вы участвуете? Перечислите через запятую. "
              "Если пока ни в каких — пропустите."),
    "roles": ("Какие роли играете или хотели бы играть? Одна-две фразы."),
    "trainings": ("Какие тренинги и темы вам интересны? Например: «сценическая речь, "
                  "импровизация»."),
    "manifest": ("Что помощнику важно про вас знать? Одна-две фразы: «не ставьте "
                 "репетиции до 11», «приезжаю на машине, могу подвезти»."),
}

RETRY = {
    "name": "Не понял имя. Напишите имя и фамилию — например, «Иванов Виктор».",
    "alias": "Не понял имя. Напишите одно слово — как вас называть коротко.",
    "phone": "Не понял номер. Напишите цифрами, например «+7 917 123-45-67».",
    "birthday": "Не понял дату. Напишите с годом: «12.03.1990».",
    "travel": "Не понял время. Напишите число минут («40») или словами («полчаса», «час»).",
    "plays": "Не понял. Перечислите спектакли через запятую или нажмите «Пропустить».",
    "roles": "Не понял. Напишите фразу своими словами или нажмите «Пропустить».",
    "trainings": "Не понял. Напишите фразу своими словами или нажмите «Пропустить».",
    "manifest": "Не понял. Напишите фразу своими словами или нажмите «Пропустить».",
}

LABELS = {
    "name": "Имя",
    "alias": "Короткое имя",
    "phone": "Телефон",
    "birthday": "День рождения",
    "travel": "Дорога",
    "plays": "Спектакли",
    "roles": "Роли",
    "trainings": "Тренинги",
    "manifest": "Заметка помощнику",
}

# Шаг знакомства → поле `POST /tools/profile/me`.
FIELD = {
    "name": "display_name",
    "alias": "alias_add",
    "phone": "phone",
    "birthday": "birth_date",
    "travel": "travel_minutes",
    "plays": "plays",
    "roles": "roles_note",
    "trainings": "interests",
    "manifest": "ai_manifest",
}

INTRO_HEAD = ("Давайте познакомимся — несколько коротких вопросов, каждый можно "
              "пропустить. Это для расписания и подсказок; ответы видите только вы "
              "и организатор. Запишу в профиль только после вашего подтверждения.")

SUMMARY_HEAD = "Так записать? Проверьте, пожалуйста:"

DONE = ("Спасибо, записал. Помощник будет учитывать дорогу и подсказки, а в сообщениях "
        "вас узнают по короткому имени. Переделать можно командой /intro.")

CANCELLED = "Хорошо, ничего не записал. Вернуться можно командой /intro."

SKIP_WORDS = {
    "нет", "не", "не надо", "не хочу", "не знаю", "ничего", "пропустить",
    "пропуск", "позже", "потом", "дальше", "-", "—", ".", "нечего", "ничего нет",
}

_WORD_NUMBERS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
    "девяносто": 90, "сто": 100, "двести": 200,
}

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}

# Слова, которые не имя, даже если стоят первыми: «меня зовут» разобрано выше,
# а «меня Витя» правила раньше принимали за имя «меня».
_ALIAS_STOP = {"меня", "это", "я", "зовите", "зови", "можно", "зовут",
               "называйте", "да", "нет", "мое", "имя", "ник", "никнейм"}

# «час», «часа», «часов», «ч.» — умножаем число на 60.
_HOUR_RE = re.compile(r"\bчас(а|ов)?\b|\bч\.")

_ALIAS_LEADS = ("меня зовут", "зовут меня", "зовут", "называйте меня", "называйте",
                "называй меня", "называй", "можно просто", "просто")


def _norm(text: str | None) -> str:
    value = " ".join((text or "").replace("ё", "е").split())
    return value.strip(" .,!?;:—-\"'«»()")


def is_skip(text: str | None) -> bool:
    """«Нет», «не знаю», «пропустить» — вежливый отказ отвечать."""
    low = _norm(text).lower()
    if not low:
        return True
    return low in SKIP_WORDS or low.startswith("не хочу") or low.startswith("пропуст")


def build_plan(profile: dict | None) -> list[str]:
    """Какие вопросы задавать: известное не переспрашиваем.

    Имя спрашиваем, только если в профиле одно слово (короткое имя или ник из
    регистрации). Телефон и день рождения — только когда их ещё нет.
    Короткое имя, дорога, спектакли, роли, тренинги и заметка — всегда: их можно
    уточнить. Тариф не спрашиваем никогда (решение V-009).
    """
    profile = profile or {}
    plan: list[str] = []
    name = " ".join(str(profile.get("name") or "").split())
    if len(name.split()) < 2:
        plan.append("name")
    plan.append("alias")
    if not profile.get("phone"):
        plan.append("phone")
    if not profile.get("birth_date"):
        plan.append("birthday")
    plan.extend(["travel", "plays", "roles", "trainings", "manifest"])
    return plan


def next_step(plan: list[str] | tuple[str, ...], step: str) -> str | None:
    """Следующий шаг плана или None, если это был последний."""
    try:
        index = list(plan).index(step)
    except ValueError:
        return None
    return plan[index + 1] if index + 1 < len(plan) else None


def question(step: str) -> str:
    return QUESTIONS.get(step, "")


def parse_travel_minutes(text: str | None) -> int | None:
    """Дорога в минутах; None — если не поняли или вне 0…240."""
    low = _norm(text).lower()
    if is_skip(low):
        return None
    if "полчаса" in low:
        return 30
    hours = _HOUR_RE.search(low) is not None
    if "полтора" in low and hours:
        return 90
    if hours and re.search(r"\bпол(овину|овина)?\b", low):
        return 30

    digits = re.search(r"\d+(?:[.,]\d+)?", low)
    if digits:
        number = float(digits.group(0).replace(",", "."))
        minutes = round(number * 60) if hours else round(number)
        return minutes if 0 <= minutes <= 240 else None

    words = sum(_WORD_NUMBERS.get(token.strip(".,"), 0) for token in low.split())
    if words:
        minutes = words * 60 if hours else words
        return minutes if 0 <= minutes <= 240 else None
    if hours:                     # «час» без числа — ровно час
        return 60
    return None


def parse_alias(text: str | None) -> str | None:
    """Короткое имя: одно слово, 2…40 символов, без служебных фраз."""
    value = _norm(text)
    if is_skip(value):
        return None
    low = value.lower()
    for lead in _ALIAS_LEADS:
        if low.startswith(lead):
            value = _norm(value[len(lead):])
            break
    word = value.split()[0] if value.split() else ""
    word = word.strip(".,!?;:—-\"'«»()")
    if word.lower() in _ALIAS_STOP or not 2 <= len(word) <= 40:
        return None
    return word


def parse_name(text: str | None) -> str | None:
    """Полное имя: минимум два слова, 2…120 символов."""
    value = " ".join((text or "").split())
    if is_skip(value):
        return None
    if len(value.split()) < 2 or not 2 <= len(value) <= 120:
        return None
    return value


def parse_phone(text: str | None) -> str | None:
    """Телефон → `+7XXXXXXXXXX`; None, если цифр не 10…15 или это отказ."""
    raw = " ".join((text or "").split())
    if is_skip(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


def parse_birthday(text: str | None) -> str | None:
    """Дата рождения → ISO `YYYY-MM-DD`; None, если не разобрали (год обязателен)."""
    low = _norm(text).lower()
    if is_skip(low):
        return None
    number = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", low)
    if number:
        day, month, year = (int(g) for g in number.groups())
        return _iso_date(year, month, day)
    number = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", low)
    if number:
        year, month, day = (int(g) for g in number.groups())
        return _iso_date(year, month, day)
    words = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", low)
    if words and words.group(2) in _MONTHS:
        return _iso_date(int(words.group(3)), _MONTHS[words.group(2)],
                         int(words.group(1)))
    return None


def _iso_date(year: int, month: int, day: int) -> str | None:
    import datetime as dt
    try:
        value = dt.date(year, month, day)
    except ValueError:
        return None
    if not 1900 <= year <= 2020:
        return None
    return value.isoformat()


def parse_text_field(text: str | None, limit: int = 300) -> str | None:
    """Свободный текст (спектакли, роли, тренинги, заметка); отказ — None."""
    value = " ".join((text or "").split())
    if is_skip(value):
        return None
    return value[:limit].rstrip() or None


def parse_step(step: str, text: str | None) -> dict:
    """Ответ человека → фрагмент тела `POST /tools/profile/me` (пусто — пропуск)."""
    if step == "name":
        name = parse_name(text)
        return {"display_name": name} if name else {}
    if step == "alias":
        alias = parse_alias(text)
        return {"alias_add": alias} if alias else {}
    if step == "phone":
        phone = parse_phone(text)
        return {"phone": phone} if phone else {}
    if step == "birthday":
        birthday = parse_birthday(text)
        return {"birth_date": birthday} if birthday else {}
    if step == "travel":
        minutes = parse_travel_minutes(text)
        return {"travel_minutes": minutes} if minutes is not None else {}
    if step == "plays":
        plays = parse_text_field(text)
        return {"plays": plays} if plays else {}
    if step == "roles":
        roles = parse_text_field(text)
        return {"roles_note": roles} if roles else {}
    if step == "trainings":
        interests = parse_text_field(text)
        return {"interests": interests} if interests else {}
    if step == "manifest":
        manifest = parse_text_field(text, limit=600)
        return {"ai_manifest": manifest} if manifest else {}
    return {}


def render_value(step: str, value) -> str:
    """Значение для сводки: минуты, телефон и дата — по-человечески."""
    if value is None or value == "":
        return ""
    if step == "travel":
        return f"{int(value)} мин"
    if step == "birthday":
        year, month, day = str(value).split("-")
        return f"{day}.{month}.{year}"
    return str(value)


def summary(answers: dict) -> str:
    """Сводка перед записью: только заполненные поля, по строке на поле."""
    lines = []
    for step in STEPS:
        if step not in answers:
            continue
        text = render_value(step, answers[step])
        if text:
            lines.append(f"{LABELS[step]}: {text}")
    if not lines:
        return "Вы ничего не заполнили — записывать нечего."
    return SUMMARY_HEAD + "\n\n" + "\n".join(lines)


def build_patch(answers: dict) -> dict:
    """Ответы сценария → одно тело `POST /tools/profile/me`."""
    patch: dict = {}
    for step, field in FIELD.items():
        if step in answers and answers[step] not in (None, ""):
            patch[field] = answers[step]
    return patch


# Шаги, где правила могут промолчать и слово берёт модель (docs/21: правила первые).
LLM_STEPS = ("alias", "travel")


def sanitize_patch(patch: dict | None, step: str) -> dict:
    """Ответ модели → тело профиля; всё вне шага отбрасывается.

    Модель — такой же недоверенный источник, как человек: имя проверяется теми же
    правилами, минуты — диапазоном 0…240, заметка обрезается до 600 знаков.
    """
    if not isinstance(patch, dict):
        return {}
    if step == "alias":
        value = patch.get("alias_add")
        alias = parse_alias(value) if isinstance(value, str) else None
        return {"alias_add": alias} if alias else {}
    if step == "travel":
        value = patch.get("travel_minutes")
        if isinstance(value, bool) or not isinstance(value, int):
            return {}
        return {"travel_minutes": value} if 0 <= value <= 240 else {}
    if step == "manifest":
        value = patch.get("ai_manifest")
        note = parse_text_field(value, limit=600) if isinstance(value, str) else None
        return {"ai_manifest": note} if note else {}
    return {}
