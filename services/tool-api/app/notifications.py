"""
Правила уведомлений (docs/infrastructure/36): тихие часы, дедупликация, тексты сообщений.

Чистые функции без БД и сети — чтобы правила можно было проверить тестами.
Отправку делает scripts/send_notifications.py, планирование — scripts/plan_reminders.py.
Какие настройки действуют (частные или общие) решает `resolve_settings` (FR-G17).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from services.shared.format import money

QUIET_FROM = time(20, 0)
QUIET_TO = time(8, 0)
MAX_ATTEMPTS = 3


def hhmm(value, default: time = QUIET_FROM) -> str:
    """Время как «ЧЧ:ММ»: из `time` (колонка БД), строки «20:00:00» или пустого значения."""
    if value is None or value == "":
        return default.strftime("%H:%M")
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value).strip()[:5]


def resolve_settings(personal: dict | None, defaults: dict | None) -> dict:
    """Действующие настройки человека: частные важнее общих (FR-G17, решение П-8).

    Частная запись пишется целиком (экран профиля и чат отправляют все поля сразу),
    поэтому её наличие означает «человек настроил»: она перекрывает общую даже там,
    где значения совпадают. Нет частной — действует общая настройка студии; нет и её
    (админ-панель ещё не сохраняла) — значения по умолчанию из кода, 20:00–08:00 (FR-G3).
    """
    source = personal if personal is not None else (defaults or {})
    return {
        "kinds_muted": list(source.get("kinds_muted") or []),
        "quiet_from": hhmm(source.get("quiet_from"), QUIET_FROM),
        "quiet_to": hhmm(source.get("quiet_to"), QUIET_TO),
        "digest_enabled": source.get("digest_enabled", True),
    }



def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")[:2]
    return time(int(hours), int(minutes))


def in_quiet_hours(moment: datetime, quiet_from: time = QUIET_FROM,
                   quiet_to: time = QUIET_TO) -> bool:
    """
    Тихие часы: с 20:00 до 08:00. Ночь переходит через полночь, поэтому
    интервал разбивается на два случая.
    """
    current = moment.time()
    if quiet_from <= quiet_to:                     # интервал внутри суток
        return quiet_from <= current < quiet_to
    return current >= quiet_from or current < quiet_to


def is_stale(moment: datetime, scheduled_at: datetime, hours: float = 24.0) -> bool:
    # Запись прождала дольше допустимого: сообщение уже неактуально (docs/36).
    return moment - scheduled_at > timedelta(hours=hours)


def next_allowed(moment: datetime, quiet_from: time = QUIET_FROM,
                 quiet_to: time = QUIET_TO) -> datetime:
    """Ближайший момент, когда можно писать: либо сам момент, либо конец тихих часов.

    Через полночь (`20:00–08:00`) конец интервала — завтрашнее утро, но только если
    человек уже в вечерней части. Интервал внутри суток (`12:00–13:00`) кончается
    сегодня: общие тихие часы студии может задать админ, а не только код (FR-G17).
    """
    if not in_quiet_hours(moment, quiet_from, quiet_to):
        return moment
    target = datetime.combine(moment.date(), quiet_to, tzinfo=moment.tzinfo)
    if quiet_from > quiet_to and moment.time() >= quiet_from:   # вечер — ждём до утра
        target += timedelta(days=1)
    return target


def dedup_key(kind: str, chat_id: int, period: str) -> str:
    """
    Один и тот же смысл сообщения не создаётся дважды.
    period — «2026-11», «2026-11-23» (неделя) и т. п.
    """
    return f"{kind}:{chat_id}:{period}"


def week_start(day: date) -> date:
    """Понедельник недели, в которую попадает день."""
    return day - timedelta(days=day.weekday())


def availability_deadline(day: date, tz=None) -> datetime:
    """
    Крайний срок заполнения занятости — пятница недели.
    Напоминание ставим на пятницу 12:00, чтобы остался рабочий день.
    tz передаём, когда сравниваем с текущим моментом (иначе naive/aware не совместить).
    """
    monday = week_start(day)
    return datetime.combine(monday + timedelta(days=4), time(12, 0), tzinfo=tz)


def dues_text(period: str, amount_due: float, amount_paid: float) -> tuple[str, str]:
    """Текст напоминания о взносах."""
    left = amount_due - amount_paid
    title = f"Взнос за {period}"
    if left <= 0:
        body = f"Взнос за {period} закрыт: {money(amount_paid)}. Спасибо."
    else:
        body = (f"Взнос за {period}: начислено {money(amount_due)}, "
                f"оплачено {money(amount_paid)}, осталось {money(left)}.\n"
                f"Если уже оплатили — напишите «оплатил взнос {period}», я отмечу.")
    return title, body


def availability_text(week: date, filled_days: int, total_days: int = 7) -> tuple[str, str]:
    """Текст напоминания заполнить занятость на следующую неделю."""
    monday = week_start(week)
    sunday = monday + timedelta(days=6)
    title = "Занятость на неделю"
    if filled_days >= total_days:
        body = f"Неделя {monday:%d.%m}–{sunday:%d.%m}: всё заполнено, спасибо."
    else:
        body = (f"Неделя {monday:%d.%m}–{sunday:%d.%m}: отмечено {filled_days} из {total_days} дней.\n"
                f"Напишите «занят в среду после 18» или нажмите «Занятость».\n"
                f"Крайний срок — пятница 18:00.")
    return title, body


def should_skip(kind: str, prefs: dict | None) -> bool:
    """
    Персональные настройки из notification_pref: kinds_muted — список выключенных
    типов, digest_enabled — только для дайджеста.
    """
    if not prefs:
        return False
    muted = prefs.get("kinds_muted") or []
    if kind in muted:
        return True
    if kind == "digest" and prefs.get("digest_enabled") is False:
        return True
    return False


def plan_due_at(now: datetime, desired: datetime,
                quiet_from: time = QUIET_FROM, quiet_to: time = QUIET_TO) -> datetime:
    """Совмещаем желаемое время с тихими часами и с тем, что уже прошло."""
    moment = max(now, desired)
    return next_allowed(moment, quiet_from, quiet_to)
