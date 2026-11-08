"""Инструменты агента: только чтение (A-2, docs/development/108 §4).

Здесь живут сами обработчики — то, что инструмент реально делает с данными студии.
Реестр (`registry.py`) решает, кому инструмент доступен и попадает ли он в схему для
модели; этот модуль решает, что вернуть человеку.

Границы модуля проверяются тестом, а не обещанием:
  * ни один обработчик не меняет данные — все запросы к базе только `select`
    (`tests/unit/test_agent_tools.py` разбирает модуль и падает на любой записывающей команде);
  * `propose_budget_tx` возвращает намерение записи и в базу ничего не пишет: запись
    делает детерминированный эндпоинт после нажатия кнопки человеком (карта A-3);
  * отказ инструмента — честный `ok: false` с причиной, модель видит его как данные.

Почему обработчики отдельно от реестра: права и схемы — это политика доступа, а чтение
данных — предметная логика. Врозь их проще проверять: тест реестра не поднимает базу,
тест обработчиков не разбирается в RBAC.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .. import core
from ..errors import ApiError
from . import config

MAX_RESULT_CHARS = 6000
MAX_SCHEDULE_EVENTS = 30
MAX_OPS = 5

WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг",
               "пятница", "суббота", "воскресенье")

MONTHS_NOM = ("январь", "февраль", "март", "апрель", "май", "июнь",
              "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")


# ------------------------------------------------------------------ помощники
def _money(value: Any) -> float:
    return round(float(value or 0), 2)


def _local(moment: Any) -> str:
    if isinstance(moment, datetime):
        return moment.astimezone(core.TZ).isoformat(timespec="minutes")
    return str(moment or "")


def _period_human(period: str) -> str:
    """«2026-11» → «ноябрь 2026»: модель называет месяц и год фактом, а не из памяти."""
    try:
        year, month = period.split("-")[:2]
        return f"{MONTHS_NOM[int(month) - 1]} {int(year)}"
    except (ValueError, IndexError):
        return period


def has_cap(who: dict, *need: str) -> bool:
    """Есть ли хотя бы одна возможность (или полный доступ «*»)."""
    caps = list(who.get("caps") or [])
    return "*" in caps or any(code in caps for code in need)


# ------------------------------------------------------------------ инструменты
def run_now(conn, who: dict, args: dict) -> dict:
    """Дата и время Самары. Единственный источник даты для ответа агента."""
    moment = core.now()
    return {"ok": True, "iso": moment.isoformat(timespec="seconds"),
            "date": moment.date().isoformat(),
            "time": moment.strftime("%H:%M"),
            "weekday": WEEKDAYS_RU[moment.weekday()],
            "weekday_short": ("пн", "вт", "ср", "чт", "пт", "сб", "вс")[moment.weekday()],
            "timezone": "Самара, UTC+4",
            "human": f"{moment.strftime('%d.%m.%Y')}, {WEEKDAYS_RU[moment.weekday()]}"
                     f", {moment.strftime('%H:%M')}"}


def run_schedule(conn, who: dict, args: dict) -> dict:
    """Ближайшие события. «Смотреть всё» — только с правом schedule.read.all."""
    days = int(args.get("days") or 7)
    days = min(max(days, 1), 30)
    see_all = has_cap(who, "schedule.read.all")
    sql = """
        select id, title, kind, starts_at, ends_at, location, status
        from event_plan
        where status <> 'cancelled'
          and starts_at >= now()
          and starts_at <= now() + make_interval(days => %s)
    """
    params: list[Any] = [days]
    if not see_all:
        sql += " and participant_ids @> array[%s]::bigint[]"
        params.append(int(who.get("id") or 0))
    sql += " order by starts_at limit %s"
    params.append(MAX_SCHEDULE_EVENTS)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return {"ok": True, "days": days, "scope": "all" if see_all else "own",
            "events": [{"id": row["id"], "title": row["title"], "kind": row["kind"],
                        "starts_at": _local(row["starts_at"]),
                        "ends_at": _local(row["ends_at"]),
                        "location": row["location"], "status": row["status"]}
                       for row in rows]}


def run_availability(conn, who: dict, args: dict) -> dict:
    """Кто свободен и когда. Без schedule.read.all человек видит только свои часы."""
    raw_day = str(args.get("day") or "").strip()
    try:
        day = date.fromisoformat(raw_day) if raw_day else core.now().date()
    except ValueError:
        return {"ok": False, "error": "bad_day",
                "message": "день ожидается в виде YYYY-MM-DD"}
    see_all = has_cap(who, "schedule.read.all")
    if not see_all:
        rows = conn.execute(
            "select hour, is_free from availability_hour"
            " where participant_id = %s and day = %s order by hour",
            (int(who.get("id") or 0), day)).fetchall()
        marks = {int(row["hour"]): bool(row["is_free"]) for row in rows}
        return {"ok": True, "day": day.isoformat(), "scope": "own",
                "marked": bool(rows),
                "free_hours": [hour for hour, free in marks.items() if free],
                "busy_hours": [hour for hour, free in marks.items() if not free]}
    rows = conn.execute(
        """select p.display_name as name,
                  count(*) filter (where a.is_free) as free_hours,
                  count(*) as marked_hours
           from availability_hour a join participant p on p.id = a.participant_id
           where a.day = %s and p.status = 'active'
           group by p.id, p.display_name order by free_hours desc, p.display_name""",
        (day,)).fetchall()
    return {"ok": True, "day": day.isoformat(), "scope": "all",
            "people": [{"name": row["name"], "free_hours": int(row["free_hours"]),
                        "marked_hours": int(row["marked_hours"])} for row in rows]}


def run_budget(conn, who: dict, args: dict) -> dict:
    """Итоги месяца и последние операции. Только с правом budget.read."""
    period = str(args.get("period") or core.now().strftime("%Y-%m")).strip()
    try:
        datetime.strptime(period + "-01", "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "error": "bad_period",
                "message": "месяц ожидается в виде YYYY-MM"}
    totals = conn.execute(
        """select block, tx_type, sum(amount) as total, count(*) as n
           from budget_tx where period = %s and status = 'confirmed'
           group by block, tx_type order by block""", (period,)).fetchall()
    income = sum(_money(row["total"]) for row in totals if row["tx_type"] == "income")
    expense = sum(_money(row["total"]) for row in totals if row["tx_type"] == "expense")
    ops = conn.execute(
        """select t.tx_date, t.tx_type, t.amount, coalesce(c.name, 'Прочее') as category,
                  t.purpose
           from budget_tx t left join category c on c.id = t.category_id
           where t.status = 'confirmed' and t.period = %s
           order by t.tx_date desc, t.id desc limit %s""", (period, MAX_OPS)).fetchall()
    return {"ok": True, "period": period, "period_human": _period_human(period),
            "income": round(income, 2), "expense": round(expense, 2),
            "balance": round(income - expense, 2),
            "blocks": [{"block": row["block"], "tx_type": row["tx_type"],
                        "total": _money(row["total"]), "count": int(row["n"])}
                       for row in totals],
            "recent": [{"date": str(row["tx_date"]), "tx_type": row["tx_type"],
                        "amount": _money(row["amount"]), "category": row["category"],
                        "purpose": row["purpose"]} for row in ops]}


def run_dues(conn, who: dict, args: dict) -> dict:
    """Взносы: свои — каждому, сводку по студии — только с правом budget.read."""
    period = str(args.get("period") or core.now().strftime("%Y-%m")).strip()
    see_all = has_cap(who, "budget.read")
    if not see_all:
        row = conn.execute(
            "select amount_due, amount_paid, status from dues_charge"
            " where participant_id = %s and period = %s",
            (int(who.get("id") or 0), period)).fetchone()
        if row is None:
            return {"ok": True, "scope": "own", "period": period,
                    "period_human": _period_human(period), "charged": False,
                    "due": 0.0, "paid": 0.0, "debt": 0.0}
        due, paid = _money(row["amount_due"]), _money(row["amount_paid"])
        return {"ok": True, "scope": "own", "period": period,
                "period_human": _period_human(period), "charged": True,
                "due": due, "paid": paid, "debt": round(max(0.0, due - paid), 2),
                "status": row["status"]}
    total = conn.execute(
        """select sum(amount_due) as due, sum(amount_paid) as paid,
                  sum(greatest(amount_due - amount_paid, 0)) as debt
           from dues_charge where period = %s""", (period,)).fetchone()
    debtors = conn.execute(
        """select p.display_name as name,
                  sum(greatest(d.amount_due - d.amount_paid, 0)) as debt
           from dues_charge d join participant p on p.id = d.participant_id
           where d.period = %s group by p.id, p.display_name
           having sum(greatest(d.amount_due - d.amount_paid, 0)) > 0
           order by debt desc limit 5""", (period,)).fetchall()
    return {"ok": True, "scope": "all", "period": period,
            "period_human": _period_human(period),
            "due": _money(total["due"]), "paid": _money(total["paid"]),
            "debt": _money(total["debt"]),
            "debtors": [{"name": row["name"], "debt": _money(row["debt"])}
                        for row in debtors]}


def run_web_search(conn, who: dict, args: dict) -> dict:
    """Внешний поиск. Без адреса API честно недоступен, а не выдумывает ссылки."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "empty_query", "message": "пустой поисковый запрос"}
    url = config.web_search_url()
    if not url:
        return {"ok": False, "error": "web_search_unavailable",
                "message": "внешний поиск не настроен"}
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url, params={"q": query, "format": "json"})
        if response.status_code >= 400:
            return {"ok": False, "error": "web_search_failed",
                    "message": f"поиск ответил {response.status_code}"}
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": "web_search_failed", "message": str(exc)[:200]}
    hits = (data.get("results") or data.get("items") or [])[:5]
    # Поле описания зависит от движка: SearXNG в JSON отдаёт `content`, другие — `snippet`
    # или `body`. Без `content` модель получала заголовок и ссылку без описания
    # (дефект найден на живом dev-поиске 05.11.2026).
    return {"ok": True, "query": query, "results": [
        {"title": str(item.get("title") or "")[:200],
         "url": str(item.get("url") or item.get("link") or "")[:500],
         "snippet": str(item.get("snippet") or item.get("content")
                        or item.get("body") or "")[:400]}
        for item in hits if isinstance(item, dict)]}


def _needs_input(kind: str, message: str, missing: list[str] | None = None) -> dict:
    """Не хватает данных: это не ошибка вызова, а вход в дом.

    Петля превращает такой ответ в карточку `kind: open` со ссылкой на экран
    Mini App (решение владельца 05.11.2026): догадка вместо даты или суммы
    дороже, чем один переход по кнопке.
    """
    return {"ok": False, "error": "needs_input", "kind": kind,
            "missing": list(missing or []), "message": message}


def _people_ids(conn, names: list[str]) -> tuple[list[int], dict[str, list[dict]]]:
    """Имена людей → номера участников. Неоднозначность возвращаем, а не выбираем."""
    from ..api.events import _people_by_names  # общий разбор имён, не второй
    if not names:
        return [], {}
    found, ambiguous, _missed = _people_by_names(conn, list(names))
    return [person["id"] for person in found], ambiguous


def run_find_slots(conn, who: dict, args: dict) -> dict:
    """Окна для репетиции/тренинга и субаренды. Считает `find_windows`, не модель.

    Правило «данные студии — из инструментов» остаётся: обработчик вызывает тот же
    сервис, что и экран поиска окон (`/tools/schedule/slots`), а не пересчитывает
    занятость по-своему.
    """
    from ..api.schedule import SlotsIn, schedule_slots  # ленивый импорт: цикл модулей

    raw_people = args.get("participants") or []
    if isinstance(raw_people, str):
        raw_people = [raw_people]
    ids: list[int] = []
    names: list[str] = []
    ambiguous: dict[str, list[dict]] = {}
    for item in raw_people:
        if isinstance(item, int) or (isinstance(item, str) and item.strip().isdigit()):
            ids.append(int(item))
        else:
            names.append(str(item))
    if names:
        resolved, ambiguous = _people_ids(conn, names)
        ids.extend(resolved)
    if ambiguous:
        options = "; ".join(f"{name}: " + ", ".join(p["name"] for p in variants)
                            for name, variants in ambiguous.items())
        return _needs_input("people", f"Уточните, кого имели в виду — {options}",
                            ["participants"])

    policy = str(args.get("policy") or "rehearsal").strip().lower()
    payload: dict[str, Any] = {"policy": policy, "min_hours": int(args.get("min_hours") or 2),
                               "limit": min(max(int(args.get("limit") or 3), 1), 10),
                               "block_events": True}
    if ids:
        payload["participants"] = ids
    if args.get("date_from"):
        payload["date_from"] = str(args["date_from"])
    if args.get("date_to"):
        payload["date_to"] = str(args["date_to"])
    if args.get("venue"):
        payload["venue"] = str(args["venue"])
    try:
        result = schedule_slots(SlotsIn(**payload), who)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": "bad_arguments", "message": str(exc)[:200]}
    if not result.get("ok"):
        return {"ok": False, "error": "slots_failed",
                "message": str(result.get("error") or result.get("reason") or "")[:200]}
    return {"ok": True, "policy": policy, "policy_title": result.get("policy_title"),
            "windows": (result.get("windows") or [])[:5],
            "text": (result.get("text") or [])[:5],
            "people": result.get("people") or {},
            "resolved": result.get("resolved"),
            "venue": result.get("venue"),
            "reason": result.get("reason"),
            "meta": result.get("meta")}


def run_propose_event(conn, who: dict, args: dict) -> dict:
    """Черновик события: тип, люди, дата, время, зал. В календарь ничего не пишет.

    Карточку и проверки собирает существующий предпросмотр назначения
    (`/tools/events/preview`): занятость зала, правила состава, занятость людей.
    Здесь только перевод аргументов модели в тот же вызов и честный `needs_input`,
    когда для события не хватает даты, времени или людей.
    """
    from ..api.events import AssignIn, events_preview  # тот же путь, что у формы

    card = args.get("card") if isinstance(args.get("card"), dict) else None
    if card:
        # Повторная проверка готовой карточки (план, подтверждение): номер события
        # не берём из присланного, а собираем заново по тем же полям.
        args = {"kind": card.get("kind"), "date": card.get("date"),
                "from_hour": card.get("from_hour"), "to_hour": card.get("to_hour"),
                "venue": card.get("venue"), "title": card.get("title"),
                "participants": [person.get("id") for person in card.get("participants") or []
                                 if isinstance(person, dict)],
                "project_id": card.get("project_id"), "discipline": card.get("discipline")}

    missing = [name for name in ("kind", "date", "from_hour") if not args.get(name)]
    if missing:
        return _needs_input("event", "Для события не хватает: " + ", ".join(missing), missing)

    people = args.get("participants") or args.get("people") or []
    ids = [int(item) for item in people
           if isinstance(item, int) or (isinstance(item, str) and str(item).strip().isdigit())]
    names = [str(item) for item in people
             if not isinstance(item, int) and not str(item).strip().isdigit()]
    ambiguous: dict[str, list[dict]] = {}
    if names:
        resolved, ambiguous = _people_ids(conn, names)
        ids.extend(resolved)
    if ambiguous:
        options = "; ".join(f"{name}: " + ", ".join(p["name"] for p in variants)
                            for name, variants in ambiguous.items())
        return _needs_input("people", f"Уточните, кого имели в виду — {options}",
                            ["participants"])
    if not ids:
        return _needs_input("event", "Не понял, кто участвует в событии.", ["participants"])

    payload: dict[str, Any] = {"kind": str(args["kind"]), "date": str(args["date"]),
                               "from_hour": int(args["from_hour"]),
                               "participants": ids}
    if args.get("to_hour"):
        payload["to_hour"] = int(args["to_hour"])
    elif args.get("duration_hours"):
        payload["duration_hours"] = int(args["duration_hours"])
    for key in ("venue", "title", "discipline"):
        if args.get(key):
            payload[key] = str(args[key])
    if args.get("project_id"):
        payload["project_id"] = int(args["project_id"])
    result = events_preview(AssignIn(**payload), who)
    if not result.get("ok"):
        errors = result.get("errors") or []
        text = "; ".join(str(item.get("text") or item.get("code")) for item in errors)
        if result.get("input_error"):
            return _needs_input("event", text or "Для события не хватает данных",
                                [str(item.get("code") or "") for item in errors])
        return {"ok": False, "error": "event_not_ready", "message": text[:300],
                "errors": errors}
    ready = result["card"]
    warnings = result.get("warnings") or []
    people_text = ", ".join(person["name"] for person in ready.get("participants") or [])
    human = (f"Назначить {ready['kind_title'].lower()} «{ready['title']}» на "
             f"{ready['weekday']} {ready['date'][8:10]}.{ready['date'][5:7]} "
             f"{int(ready['from_hour']):02d}:00–{int(ready['to_hour']):02d}:00, "
             f"зал «{ready['venue_name']}»")
    if people_text:
        human += f", участники: {people_text}"
    human += "."
    if warnings:
        human += " " + " ".join(str(item.get("text") or "") for item in warnings)
    return {"ok": True, "proposal": {"tool": "event.assign", "args": {"card": ready},
                                     "human_text": human},
            "warnings": warnings}


def run_propose_note(conn, who: dict, args: dict) -> dict:
    """Черновик заметки: личная или общая. Пишется только после подтверждения."""
    from ..api import notes as notes_api  # те же проверки текста, что у формы

    text = str(args.get("body") or args.get("text") or "").strip()
    if not text:
        return _needs_input("note", "Не понял текст заметки.", ["body"])
    try:
        body = notes_api._clean_body(text)
        scope = notes_api._clean_scope(str(args.get("scope") or "personal"))
        title = notes_api._clean_title(str(args.get("title") or ""), body)
        tags = notes_api._clean_tags(args.get("tags") or [])
    except (ApiError, ValueError) as exc:            # отказ проверки входа — не 500
        return {"ok": False, "error": "bad_note", "message": str(exc)[:200]}
    kind = "общая" if scope == "shared" else "личная"
    return {"ok": True, "proposal": {
        "tool": "note.create",
        "args": {"title": title, "body": body, "scope": scope, "tags": tags},
        "human_text": f"Создать {kind} заметку «{title}»."}}


def proposal_text(tx_type: str, amount: float, category: str, tx_date: str) -> str:
    kind = "доход" if tx_type == "income" else "расход"
    try:
        day = datetime.fromisoformat(tx_date).strftime("%d.%m.%Y")
    except ValueError:
        day = tx_date
    return (f"Записать {kind}: {amount:.0f} ₽, категория «{category}», {day}. "
            f"Нажмите «Записать», чтобы подтвердить.")


def run_propose_budget(conn, who: dict, args: dict) -> dict:
    """Намерение записать операцию. В базу ничего не пишет — только предложение.

    Запись делает детерминированный `/tools/budget/commit` после нажатия кнопки
    в боте, поэтому «агент попросил» и «деньги учтены» — разные события.
    """
    tx_type = str(args.get("tx_type") or "expense").strip().lower()
    if tx_type not in ("expense", "income"):
        return {"ok": False, "error": "bad_type", "message": "tx_type: expense или income"}
    try:
        amount = float(str(args.get("amount") or 0).replace(",", "."))
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return {"ok": False, "error": "bad_amount", "message": "сумма должна быть больше нуля"}
    category = str(args.get("category") or "").strip()
    row = conn.execute("select name, direction from category where name = %s",
                       (category,)).fetchone() if category else None
    if row is None or row["direction"] != tx_type:
        allowed = [item["name"] for item in conn.execute(
            "select name from category where direction = %s order by name",
            (tx_type,)).fetchall()]
        return {"ok": False, "error": "bad_category",
                "message": "категория не из закрытого списка для этого направления; "
                           "выбери название ровно из allowed_categories",
                "allowed_categories": allowed}
    purpose = str(args.get("purpose") or "").strip()[:300]
    if not purpose:
        purpose = category
    raw_date = str(args.get("tx_date") or "").strip()
    try:
        tx_date = date.fromisoformat(raw_date) if raw_date else core.now().date()
    except ValueError:
        return {"ok": False, "error": "bad_date", "message": "дата ожидается YYYY-MM-DD"}
    if tx_date > core.now().date() + timedelta(days=1):
        return {"ok": False, "error": "bad_date",
                "message": "дата операции не может быть в будущем"}
    draft = {"tx_type": tx_type, "amount": round(amount, 2), "category": category,
             "purpose": purpose, "tx_date": tx_date.isoformat(),
             "counterparty": str(args.get("counterparty") or "").strip() or None}
    return {"ok": True, "proposal": {"tool": "budget.tx", "args": draft,
                                     "human_text": proposal_text(
                                         tx_type, amount, category, draft["tx_date"])}}
