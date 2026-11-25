"""Mini App: оболочка, авторизация и общие экранные данные."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from .. import core, notification_store, poll_store, ui_texts
from ..errors import ApiError
from ..external import DB_ERRORS, GOOGLE_API_ERRORS
from ..notifications import resolve_settings
from ..pages import layout as page_layout
from ..webapp import PERSONAL, InitDataError, issue_token, sections_for, validate_init_data
from . import finance

router = APIRouter()

#: Каталог статики сервиса. Этот модуль лежит в `app/api/`, а файлы — в `app/static/`:
#: после разреза на роутеры `Path(__file__).parent / "static"` указывал в несуществующий
#: `app/api/static` и отдавал 500 вместо стилей и оболочки (дефект 01.11.2026).
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def _static(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")

class WebAppAuthIn(core.ApiIn):
    init_data: str



@router.post("/webapp/auth")
def webapp_auth(body: WebAppAuthIn) -> dict:
    """Обменивает подписанный initData на короткоживущий токен."""
    try:
        parsed = validate_init_data(body.init_data, core.BOT_TOKEN)
    except InitDataError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, str(exc), code="init_data_invalid") from exc

    with core.pool.connection() as conn:
        person = conn.execute("select * from participant where tg_chat_id = %s",
                              (parsed["chat_id"],)).fetchone()
    if person is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED,
                       "Сначала зарегистрируйтесь в боте по коду приглашения.",
                       code="not_registered")
    if person["status"] == "archived":
        raise ApiError(status.HTTP_403_FORBIDDEN, "Доступ закрыт.", code="participant_archived")

    token = issue_token(parsed["chat_id"], core.WEBAPP_SECRET)
    return {"ok": True, "token": token, "expires_in": 1800,
            "participant": {"id": person["id"], "name": person["display_name"],
                            "status": person["status"]}}



@router.get("/webapp/tg.js")
def webapp_tg_sdk() -> Response:
    """SDK Telegram отдаём со своего домена.

    Без него `window.Telegram` не существует, `initData` пуст, и Mini App не может
    получить токен — так было на проде: страницы грузились, а вход не работал.
    Внешний `telegram.org` не подключаем: в РФ он недоступен, а ассеты должны
    приезжать с того же домена, что и страница.
    """
    content = _static("telegram-web-app.js")
    return Response(content=content, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/webapp/logo.png")
def webapp_logo() -> Response:
    """Знак студии для шапки Главной — слово «СВОИ» под маску (148 §2 D1).

    Собственный маршрут, а не общий `/webapp/static/*`: ради одного файла общая
    раздача не нужна, а `_static()` читает текст и для PNG не годится. Файл
    неизменяемый, поэтому кэш на сутки, как у SDK и оболочки.
    """
    content = (STATIC_DIR / "logo-svoi-word.png").read_bytes()
    return Response(content=content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})



def _one_line(value: object, limit: int) -> str:
    """Поле журнала маяка — одной строкой.

    Поля приходят от клиента без проверки, а перевод строки в них добавляет в журнал
    запись, которой не было (аудит 03.11.2026: `{"url": "x\\n2026-11-03 boot: ..."}`).
    Журнал — инструмент разбора «Mini App не открывается», подделка в нём дороже
    обычного шума, поэтому схлопываем любые пробельные последовательности в пробел
    до обрезки по длине.
    """
    return " ".join(str(value).split())[:limit]


@router.post("/webapp/boot")
async def webapp_boot(payload: dict | None = None) -> dict:
    """Маяк загрузки Mini App: что клиент видит на старте.

    Зачем: 02.11.2026 Mini App не открывался на iPhone и в Desktop, а в журнале не было
    ни одного POST /webapp/auth. Проверить было нечем: запросы в логах шли от прокси,
    а не с устройства. Теперь страница сама сообщает платформу, длину initData, адрес и
    текст JS-ошибки — по этому видно, передал ли Telegram данные.

    Сам initData не принимается и не хранится: только длина. Ответ всегда 200, чтобы
    маяк не влиял на поведение страницы.
    """
    data = payload if isinstance(payload, dict) else {}
    try:
        init_len = int(data.get("init_len") or 0)
    except (TypeError, ValueError):
        init_len = 0
    core.log.info(
        "boot: platform=%s init_len=%s sdk=%s ready=%s err=%s url=%s",
        _one_line(data.get("platform"), 20), init_len,
        _one_line(data.get("sdk"), 12), _one_line(data.get("ready"), 12),
        _one_line(data.get("error") or "", 150), _one_line(data.get("url") or "", 170))
    return {"ok": True}


@router.get("/webapp/shell.js")
def webapp_shell_js() -> Response:
    """Общий клиентский слой: тема Telegram и тихое обновление токена.

    Без него iOS в тёмной теме получал чёрный фон с тёмным текстом, а протухший
    за 30 минут токен давал «сессия истекла» на первом же действии.
    """
    content = _static("webapp-shell.js")
    return Response(content=content, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})



@router.get("/webapp/ui.css")
def webapp_ui_css() -> Response:
    """Дизайн-язык Mini App: токены и компоненты (docs/development/83).

    Отдельный слой классов `.ui-*`, чтобы страницы переводились по одной:
    подключение не меняет вид существующих экранов, пока они не используют классы.
    """
    content = _static("webapp-ui.css")
    return Response(content=content, media_type="text/css",
                    headers={"Cache-Control": "no-cache"})



@router.get("/webapp/form-data")
def webapp_form_data(who: dict = Depends(core.webapp_actor)) -> dict:
    """
    Справочники для формы поиска окон: люди, спектакли, дисциплины, помещения.
    Люди — с ролями, чтобы в списке было видно «Орлова (Наташа)».
    """
    with core.pool.connection() as conn:
        people = conn.execute("""
            select p.id, p.display_name,
                   coalesce(array_agg(a.alias) filter (where a.alias_kind = 'role'), '{}') as roles
            from participant p
            left join person_alias a on a.participant_id = p.id
            where p.status <> 'archived'
            group by p.id, p.display_name
            order by p.display_name""").fetchall()
        projects = conn.execute("select id, name, kind from project "
                                "where status <> 'archive' order by name").fetchall()
        disciplines = conn.execute("select code, name from discipline "
                                   "order by sort_order, name").fetchall()
        venues = conn.execute("select code, name from venue where is_active "
                              "order by sort_order").fetchall()
        mine = core.disciplines_out(conn, who["id"])
    return {"ok": True,
            "people": [{"id": r["id"], "name": r["display_name"],
                        "roles": list(r["roles"] or [])} for r in people],
            "projects": [dict(r) for r in projects],
            "disciplines": [dict(r) for r in disciplines],
            "venues": [dict(r) for r in venues],
            # дисциплины самого человека: у мастера — его предметы, у остальных пусто
            "my_disciplines": mine,
            "defaults": {"kind": "rehearsal", "min_hours": 2, "days": 7,
                         "venue": core.default_venue_code()}}



@router.get("/tools/howto")
def howto(who: dict = Depends(core.require("howto.read"))) -> dict:
    """Инструкции по ролям для раздела «Справка»: только своя роль, админу — все."""
    roles = set(who.get("roles") or [])
    sections: list[dict] = []
    for code, title in core.HOWTO_SECTIONS:
        if "admin" not in roles and code not in roles:
            continue
        text = core._read(core.HOWTO_DIR / f"{code}.md")
        if not text:
            continue
        sections.append({"role": code, "title": title, "text": text})
    return {"ok": True, "sections": sections}



@router.get("/tools/ui-texts")
def ui_texts_all(who: dict = Depends(core.current)) -> dict:
    """Все тексты интерфейса одним ответом — бот берёт их для «Что умею» и «Справки»."""
    return {"ok": True, "texts": ui_texts.texts()}



def _calendar_events(start: datetime, end: datetime, limit: int) -> list[dict] | None:
    """События промежутка из календаря студии: день, время, название, место.

    `None` — календарь не настроен или не ответил. Это не ошибка Главной: экран
    живёт без Google и говорит об этом словами (V-131, docs/reports/74-questions.md).
    Поле `day` служебное: по нему один ответ делится на «сегодня» и «завтра»,
    наружу оно не уходит — там только время, название и место.
    """
    if not core.CALENDAR_ID_READ:
        return None
    try:
        service = core._calendar()
        found = service.events().list(
            calendarId=core.CALENDAR_ID_READ,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=limit).execute().get("items", [])
    except GOOGLE_API_ERRORS:
        # адаптер календаря: главный экран живёт без расписания
        return None
    events = []
    for item in found:
        raw = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or ""
        events.append({"day": raw[:10],
                       "time": raw[11:16] if "T" in raw else "",
                       "title": item.get("summary") or "Событие",
                       "place": item.get("location") or ""})
    return events


def _event_out(event: dict) -> dict:
    """Событие для ответа Главной: время, название, место — без служебного дня."""
    return {"time": event["time"], "title": event["title"], "place": event["place"]}


def _calendar_blocks() -> tuple[dict, dict | None]:
    """«Сегодня» и «Завтра» одним походом в календарь (V-131).

    Один запрос к Google на оба блока: второй поход за следующим днём удваивал
    ожидание Главной, а данные те же — календарь читается от текущего момента до
    конца завтрашнего дня и делится по дате.

    Возвращает `(today, tomorrow)`:
    * `today` — `{}` без событий, иначе подпись дня и `events` списком, как «Завтра»:
      день не сводится к первому событию (жалоба 25.11 — в календаре три, на Главной одно);
    * `tomorrow` — `None`, если календарь не прочитан; иначе подпись даты словами
      и `events` списком (`[]` — день пуст, и экран говорит об этом словами).
    """
    now = datetime.now(core.TZ)
    next_day = (now + timedelta(days=1)).date()
    day_start = datetime.combine(next_day, time(0, 0), tzinfo=core.TZ)
    found = _calendar_events(now, day_start + timedelta(days=1), 40)
    if found is None:
        return {}, None
    today_events = [event for event in found if event["day"] == now.date().isoformat()]
    tomorrow_events = [event for event in found if event["day"] == next_day.isoformat()]
    today = ({"label": "сегодня, " + page_layout.WEEKDAYS_FULL[now.weekday()],
              "events": [_event_out(event) for event in today_events]} if today_events else {})
    tomorrow = {"label": "завтра, " + page_layout.human_date(next_day),
                "events": [_event_out(event) for event in tomorrow_events]}
    return today, tomorrow


def _calendar_url(caps) -> str:
    """Адрес «Весь календарь» по правам: сводка читает всю труппу.

    `/webapp/week/summary` закрыт `schedule.read.all` (FR-H9, docs/development/92 §7);
    без права ссылка вела бы на 403, поэтому человек получает свой экран недели.
    Решение серверное, как у остальных блоков Главной: страница — публичная константа.
    """
    return ("/webapp/week/summary" if page_layout.has(caps, "schedule.read.all")
            else "/webapp/week")



def _week_block(conn, participant_id: int) -> dict:
    """Сколько дней недели отмечено: один ответ на вопрос «я всё заполнил?»."""
    today = datetime.now(core.TZ).date()
    monday = today - timedelta(days=today.weekday())
    row = conn.execute("""
        select count(distinct day) as n from availability_hour
        where participant_id = %s and day between %s and %s""",
        (participant_id, monday, monday + timedelta(days=6))).fetchone()
    return {"title": page_layout.human_date(monday, with_weekday=False) + " — "
                     + page_layout.human_date(monday + timedelta(days=6), with_weekday=False),
            "marked": int(row["n"]), "days": 7}



def management_rows(caps, counts: dict[str, int]) -> list[dict]:
    """Строки «Управления» по реестру разделов: название, подпись, адрес, бейдж.

    Порядок — порядок записей в `SECTIONS`. Бейдж (`count`) добавляется только
    разделу, у которого в реестре указан счётчик.
    """
    rows = []
    for section in sections_for(caps, group="management"):
        row: dict = {"title": section.title, "meta": section.caption, "url": section.url}
        if section.count:
            row["count"] = counts.get(section.count, 0)
        rows.append(row)
    return rows


def personal_rows(caps) -> list[dict]:
    """Строки «Личного» на «Ещё» по реестру разделов: название, подпись, адрес.

    Отдаются данными, а не подстановкой в HTML: с 05b7d2b страницы Mini App —
    публичные константы без `Authorization`, и права на момент отдачи разметки
    неизвестны. Порядок — порядок записей в `SECTIONS`.
    """
    return [{"title": section.title, "meta": section.caption, "url": section.url}
            for section in sections_for(caps, group=PERSONAL)]


def attention_rows(caps, counts: dict[str, int]) -> list[dict]:
    """Строки «Требует внимания»: разделы с подписью `attention` и ненулевым счётчиком."""
    rows = []
    for section in sections_for(caps):
        if not section.attention or not section.count or not counts.get(section.count):
            continue
        rows.append({"title": section.title, "meta": section.attention,
                     "url": section.url, "count": counts[section.count]})
    return rows


def _polls_block(conn, participant_id: int) -> dict | None:
    """Строка-вход «Мои опросы» для Главной: счётчики и подпись (docs/164 §12.2).

    Считается тем же `poll_store.my_runs`, а не отдельным `count(*)`: два запроса
    разошлись бы в том, что считать открытым. `count` — открытые непройденные, он
    идёт бейджем; `polls = null` — у человека нет ни одного прохождения, и строка
    ему не нужна (как `debt = null`).
    """
    runs = poll_store.my_runs(conn, participant_id)
    if not runs:
        return None
    grouped = poll_store.groups(runs)
    open_count = len(grouped["open"])
    done_count = len(grouped["done"])
    return {"count": open_count, "open": open_count, "done": done_count,
            "meta": poll_store.entry_meta({"open": open_count, "done": done_count}),
            "url": "/webapp/polls/mine"}


@router.get("/webapp/home/data")
def webapp_home_data(who: dict = Depends(core.webapp_actor)) -> dict:
    """Данные «Главной» и «Управления» одним ответом (контракт — в pages/home.py).

    Недоступное по правам не приходит в браузер вовсе: строки собираются здесь из
    реестра разделов, а не прячутся стилями (docs/development/86 §7).
    """
    counts: dict[str, int] = {}
    with core.pool.connection() as conn:
        caps = core._caps_for(conn, who["id"])
        row = conn.execute("select role from participant where id = %s", (who["id"],)).fetchone()
        roles = core.resolve_roles(conn, who["id"])
        disciplines = core.disciplines_out(conn, who["id"])
        if page_layout.has(caps, "feedback.triage"):
            counts["feedback"] = conn.execute(
                "select count(*) as n from feedback where status in ('new', 'seen', 'planned')"
            ).fetchone()["n"]
        week = _week_block(conn, who["id"])
        polls = _polls_block(conn, who["id"])

    attention = attention_rows(caps, counts)
    management = management_rows(caps, counts)

    debt = None
    try:
        dues = finance.dues_me(who)
        current = dues.get("current") or {}
        rest = float(current.get("due") or 0) - float(current.get("paid") or 0)
        if rest > 0:
            debt = {"period": page_layout.human_month(current.get("period")),
                    "rest": rest, "due": float(current.get("due") or 0)}
    except DB_ERRORS:
        # чтение взносов не удалось: главный экран показывает остальное
        debt = None

    role = (row["role"] if row else "") or "troupe"
    # Педагог показывается своей ролью, а не старой витриной `participant.role`:
    # выдача `master` не переписывает основную роль человека (140 §2.1), и без
    # этой строки мастер видел бы на главной «студиец».
    if "master" in roles and not {"admin", "org"} & set(roles):
        role = "master"
    staff = bool(management)
    today, tomorrow = _calendar_blocks()
    return {
        "ok": True,
        "name": who.get("display_name") or "",
        "role": role,
        "role_label": core.ROLE_LABELS.get(role, role),
        "disciplines": disciplines,
        "staff": staff,
        "can_record": page_layout.has(caps, "budget.write"),
        "attention": attention,
        "today": today,
        # «Завтра» и вход в календарь студии (V-131): тем же ответом, что «Сегодня»,
        # чтобы Главная не делала второго запроса и не прыгала скелетом.
        "tomorrow": tomorrow,
        "calendar_url": _calendar_url(caps),
        "week": week,
        "debt": debt,
        # «Мои опросы»: строка-вход на Главной и счётчик бейджа (docs/164 §12.6).
        # `null` — у человека нет ни одного прохождения: узла в DOM не будет вовсе.
        "polls": polls,
        "more": {"management": management, "personal": personal_rows(caps)},
        "counts": counts,
    }



class NotificationSettingsIn(core.ApiIn):
    rehearsals: bool = True
    dues: bool = True
    schedule_changes: bool = True
    quiet_from: str = "20:00"   # решение заказчика 31.10.2026: FR-G3
    quiet_to: str = "08:00"



def _settings_out(row: dict | None, defaults: dict) -> dict:
    """Действующие настройки человека в виде формы экрана.

    Пока частной записи нет, экран показывает общие значения студии — человек видит,
    что получит на самом деле, а не «20:00–08:00 из кода» (FR-G17, О3).
    """
    effective = resolve_settings(row, defaults)
    return {
        **notification_store.kinds_to_form(effective["kinds_muted"]),
        "quiet_from": effective["quiet_from"],
        "quiet_to": effective["quiet_to"],
        # Откуда взялись значения: "personal" — свои, "defaults" — общие по студии.
        "source": "personal" if row is not None else "defaults",
    }



def _settings_load(participant_id: int) -> dict:
    """Тихие часы и типы уведомлений человека — одно чтение для экрана и чата (FR-G3)."""
    with core.pool.connection() as conn:
        row = notification_store.load_personal(conn, participant_id)
        defaults = notification_store.load_defaults(conn)
    return _settings_out(row, defaults)



def _settings_save(participant_id: int, body: NotificationSettingsIn) -> dict:
    """Запись частных настроек доставки — единственный путь для двух оболочек (FR-G3, О3).

    Mini App и чат зовут эту функцию, а не пишут `notification_pref` каждая сама:
    иначе экран и чат разойдутся в том, что считается тихими часами
    (`docs/architecture/25` §9.2 п.2 — «вторая оболочка вызывает обработчик»).
    """
    quiet_from = core.clock_text(body.quiet_from)
    quiet_to = core.clock_text(body.quiet_to)
    if quiet_from is None or quiet_to is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_time: формат ЧЧ:ММ")
    muted = notification_store.form_to_kinds(body.rehearsals, body.dues,
                                             body.schedule_changes)
    with core.pool.connection() as conn:
        notification_store.save_personal(conn, participant_id, muted,
                                         quiet_from, quiet_to)
        defaults = notification_store.load_defaults(conn)
    return _settings_out({"kinds_muted": muted, "quiet_from": quiet_from,
                          "quiet_to": quiet_to}, defaults)



@router.get("/webapp/notifications/settings")
def webapp_notifications_get(who: dict = Depends(core.webapp_actor)) -> dict:
    return {"ok": True, "settings": _settings_load(who["id"])}



@router.post("/webapp/notifications/settings")
def webapp_notifications_save(body: NotificationSettingsIn,
                              who: dict = Depends(core.webapp_actor)) -> dict:
    return {"ok": True, "settings": _settings_save(who["id"], body)}



@router.get("/tools/notifications/settings")
def notifications_get(who: dict = Depends(core.current)) -> dict:
    """Настройки доставки для чата: та же функция, что у экрана (FR-G3, строка О3).

    Права нет — свои тихие часы настраивает любой действующий участник, ровно как
    на экране `/webapp/notifications`. Ветка `session` в `docs/architecture/25` §9.4
    означает именно это: личная настройка, а не право.
    """
    return {"ok": True, "settings": _settings_load(who["id"])}



@router.post("/tools/notifications/settings")
def notifications_save(body: NotificationSettingsIn,
                       who: dict = Depends(core.current)) -> dict:
    """Записать настройки доставки из чата тем же обработчиком, что и с экрана."""
    return {"ok": True, "settings": _settings_save(who["id"], body)}

