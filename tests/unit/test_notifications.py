"""Правила уведомлений: тихие часы, дедупликация, тексты (docs/infrastructure/36)."""

from datetime import date, datetime, time, timedelta, timezone

from app.notifications import (
    availability_deadline,
    availability_text,
    dedup_key,
    dues_text,
    in_quiet_hours,
    is_stale,
    next_allowed,
    parse_hhmm,
    plan_due_at,
    resolve_settings,
    should_skip,
    week_start,
)

TZ = timezone(timedelta(hours=4))          # Самара


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def test_тихие_часы_вечером():
    assert in_quiet_hours(dt(2, 20, 0))
    assert in_quiet_hours(dt(2, 23, 30))
    assert in_quiet_hours(dt(3, 7, 59))


def test_днём_не_тихо():
    assert not in_quiet_hours(dt(2, 8, 0))
    assert not in_quiet_hours(dt(2, 12, 0))
    assert not in_quiet_hours(dt(2, 19, 59))


def test_утренний_перенос():
    assert next_allowed(dt(2, 22, 15)) == dt(3, 8, 0)
    assert next_allowed(dt(2, 12, 0)) == dt(2, 12, 0)


def test_интервал_внутри_суток_кончается_сегодня():
    """FR-G17: общие тихие часы задаёт админ — интервал может не пересекать полночь."""
    quiet_from, quiet_to = time(12, 0), time(13, 0)
    assert next_allowed(dt(2, 12, 15), quiet_from, quiet_to) == dt(2, 13, 0)
    assert next_allowed(dt(2, 10, 0), quiet_from, quiet_to) == dt(2, 10, 0)


def test_просроченное_планируется_на_сейчас():
    desired = dt(1, 12, 0)
    now = dt(2, 12, 0)
    assert plan_due_at(now, desired) == now


def test_поздний_вечер_переносится_на_утро():
    assert plan_due_at(dt(2, 21, 0), dt(2, 10, 0)) == dt(3, 8, 0)


def test_ключ_дедупликации():
    assert dedup_key("dues", 111, "2026-11") == "dues:111:2026-11"
    assert dedup_key("dues", 111, "2026-11") == dedup_key("dues", 111, "2026-11")
    assert dedup_key("dues", 111, "2026-11") != dedup_key("dues", 112, "2026-11")
    assert dedup_key("dues", 111, "2026-11") != dedup_key("dues", 111, "2026-12")


def test_начало_недели_и_дедлайн():
    assert week_start(date(2026, 11, 26)) == date(2026, 11, 23)     # четверг → понедельник
    assert week_start(date(2026, 11, 23)) == date(2026, 11, 23)     # понедельник
    deadline = availability_deadline(date(2026, 11, 26))
    assert deadline.date() == date(2026, 11, 27) and deadline.hour == 12


def test_текст_взносов_долг():
    title, body = dues_text("2026-10", 10000, 0)
    assert title == "Взнос за 2026-10"
    assert "осталось 10 000 ₽" in body


def test_текст_взносов_закрыт():
    _, body = dues_text("2026-10", 10000, 10000)
    assert "закрыт" in body


def test_текст_занятости_незаполненной():
    _, body = availability_text(date(2026, 11, 26), filled_days=2)
    assert "отмечено 2 из 7" in body
    assert "23.11–29.11" in body


def test_текст_занятости_заполненной():
    _, body = availability_text(date(2026, 11, 26), filled_days=7)
    assert "всё заполнено" in body


def test_отключённые_уведомления_пропускаются():
    assert should_skip("dues", {"kinds_muted": ["dues"]})
    assert should_skip("dues", {"kinds_muted": ["availability", "dues"]})
    assert not should_skip("dues", {"kinds_muted": []})
    assert not should_skip("dues", None)
    assert not should_skip("system", {"kinds_muted": ["dues"]})


def test_дайджест_управляется_отдельным_флагом():
    assert should_skip("digest", {"digest_enabled": False})
    assert not should_skip("digest", {"digest_enabled": True})


def test_просроченное_уведомление():
    moment = dt(3, 12)
    assert not is_stale(moment, dt(2, 12), 24)
    assert not is_stale(moment, dt(2, 13), 24)
    assert is_stale(moment, dt(2, 11), 24)
    assert is_stale(moment, dt(2, 12), 12)
    assert not is_stale(moment, moment, 24)


# --- FR-G17: частные настройки важнее общих (решение П-8, карта Ш-11) ------------

COMMON = {"kinds_muted": ["dues"], "quiet_from": "21:00", "quiet_to": "09:00"}


def test_fr_g17_частные_тихие_часы_перекрывают_общие():
    """FR-G17: если человек настроил свои тихие часы, действуют они, а не студийные."""
    personal = {"kinds_muted": [], "quiet_from": "23:00", "quiet_to": "06:30"}
    effective = resolve_settings(personal, COMMON)
    assert (effective["quiet_from"], effective["quiet_to"]) == ("23:00", "06:30")
    assert not in_quiet_hours(dt(2, 21, 30), parse_hhmm(effective["quiet_from"]),
                              parse_hhmm(effective["quiet_to"]))


def test_fr_g17_без_частных_применяются_общие():
    """FR-G17: без частной записи действует общая настройка студии, включая типы."""
    effective = resolve_settings(None, COMMON)
    assert (effective["quiet_from"], effective["quiet_to"]) == ("21:00", "09:00")
    assert effective["kinds_muted"] == ["dues"]
    assert should_skip("dues", effective)
    assert not should_skip("availability", effective)


def test_fr_g17_частная_запись_перекрывает_и_типы_и_часы():
    """FR-G17: частная запись перекрывает общую целиком, а не по отдельным полям."""
    personal = {"kinds_muted": ["availability"], "quiet_from": "20:00", "quiet_to": "08:00"}
    effective = resolve_settings(personal, COMMON)
    assert effective["kinds_muted"] == ["availability"]
    assert should_skip("availability", effective)
    assert not should_skip("dues", effective)      # общая запись выключала взносы


def test_fr_g17_без_настроек_действуют_умолчания_из_кода():
    """FR-G3: ни частной, ни общей записи — 20:00–08:00 и все типы включены."""
    effective = resolve_settings(None, None)
    assert (effective["quiet_from"], effective["quiet_to"]) == ("20:00", "08:00")
    assert effective["kinds_muted"] == []
    # Значения БД приходят временем, строкой и «ЧЧ:ММ:СС» — форма одна.
    assert resolve_settings({"quiet_from": time(22, 0), "quiet_to": "07:45:00"}, None)[
        "quiet_from"] == "22:00"
    assert resolve_settings({"quiet_from": "22:00:00"}, None)["quiet_from"] == "22:00"
