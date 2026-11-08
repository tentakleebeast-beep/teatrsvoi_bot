"""Ядро tool-api: настройки, БД, модели и общие помощники."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, model_validator

from . import llm, ui_texts
from .errors import ApiError
from .external import GOOGLE_API_ERRORS
from .privacy import phone_for
from .rules import WORK_DAY_END, WORK_DAY_START
from .settings import Settings
from .venue import busy_for_venue, parse_freebusy
from .version import project_root, read_git_sha
from .webapp import InitDataError, verify_token

settings = Settings.from_env()          # единственное чтение окружения — см. settings.py

TZ = settings.tz                        # Самара, UTC+4

STARTED_AT = datetime.now(UTC)          # /health: uptime и started_at

GIT_SHA = read_git_sha(project_root())           # сборка, которую реально запустили

DATABASE_URL = settings.database_url

ADMIN_CHAT_ID = settings.admin_chat_id

TOOL_API_TOKEN = settings.tool_api_token

ENV_NAME = settings.env_name



# Запись в лист из dev-контура выключена: иначе тестовые операции уезжают в боевую
# таблицу (разбор — docs/reports/84 и карта канбана t_a422101b). Проверка самой
# записи включается явно: ALLOW_DEV_SHEET_WRITE=1 (правило — Settings.sheet_write_enabled).
SHEET_WRITE_ENABLED = settings.sheet_write_enabled

DEV_SHEET_ERROR = "dev: запись в лист отключена (ALLOW_DEV_SHEET_WRITE=1 чтобы проверить запись)"

INVITE_TTL_DAYS = settings.invite_ttl_days

INVITE_MAX_ATTEMPTS = 3

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"



# Mini App и бюджет/календарь: значения тоже берутся из settings (раньше читались
# в двух других местах файла — теперь в одном блоке).
WEBAPP_SECRET = settings.webapp_secret_effective

BOT_TOKEN = settings.telegram_token

GOOGLE_SA_JSON = settings.google_sa_json

BUDGET_SPREADSHEET_ID = settings.budget_spreadsheet_id

SCHEDULE_SPREADSHEET_ID = settings.schedule_spreadsheet_id

SCHEDULE_SHEET_TITLE = settings.schedule_sheet_title

CALENDAR_ID_READ = settings.calendar_id_read

CALENDAR_ID_VENUE = settings.calendar_id_venue     # актуальный календарь зала, только чтение

CALENDAR_ID_DRAFT = settings.calendar_id_draft     # черновой, для обкатки

# Потолок архива папки: ZIP собирается во временный файл, и без потолка папка на
# 200 МБ занимает 200 МБ в /tmp сервера (аудит ponytail). Настраивается ZIP_MAX_BYTES.
ZIP_MAX_BYTES = settings.zip_max_bytes



def _make_pool(dsn: str) -> ConnectionPool:
    return ConnectionPool(
        dsn, min_size=1, max_size=8, open=False,
        kwargs={"row_factory": dict_row},
    )



pool = _make_pool(settings.database_url)



def configure(cfg: Settings) -> None:
    """Явная подстановка настроек вместо подмены `os.environ` до импорта (R-4).

    Тесты передают сюда тестовую БД и токен; в проде настройки читаются один раз,
    в `Settings.from_env()` при импорте. Фабрика приложения с явной передачей
    появится на этапе B, когда main.py разложится на роутеры (docs/reports/79).
    """
    global settings, TZ, DATABASE_URL, ADMIN_CHAT_ID, TOOL_API_TOKEN, ENV_NAME
    global SHEET_WRITE_ENABLED, INVITE_TTL_DAYS, WEBAPP_SECRET, BOT_TOKEN
    global GOOGLE_SA_JSON, BUDGET_SPREADSHEET_ID, SCHEDULE_SPREADSHEET_ID
    global SCHEDULE_SHEET_TITLE, CALENDAR_ID_READ, CALENDAR_ID_VENUE, CALENDAR_ID_DRAFT
    global ZIP_MAX_BYTES
    global pool

    if not pool.closed:
        pool.close()
    settings = cfg
    TZ = cfg.tz
    DATABASE_URL = cfg.database_url
    ADMIN_CHAT_ID = cfg.admin_chat_id
    TOOL_API_TOKEN = cfg.tool_api_token
    ENV_NAME = cfg.env_name
    SHEET_WRITE_ENABLED = cfg.sheet_write_enabled
    INVITE_TTL_DAYS = cfg.invite_ttl_days
    WEBAPP_SECRET = cfg.webapp_secret_effective
    BOT_TOKEN = cfg.telegram_token
    GOOGLE_SA_JSON = cfg.google_sa_json
    BUDGET_SPREADSHEET_ID = cfg.budget_spreadsheet_id
    SCHEDULE_SPREADSHEET_ID = cfg.schedule_spreadsheet_id
    SCHEDULE_SHEET_TITLE = cfg.schedule_sheet_title
    CALENDAR_ID_READ = cfg.calendar_id_read
    CALENDAR_ID_VENUE = cfg.calendar_id_venue
    CALENDAR_ID_DRAFT = cfg.calendar_id_draft
    ZIP_MAX_BYTES = cfg.zip_max_bytes
    pool = _make_pool(cfg.database_url)



def resolve_roles(conn, participant_id: int) -> list[str]:
    row = conn.execute("select roles from v_participant_roles where participant_id = %s",
                       (participant_id,)).fetchone()
    return _clean_roles(row["roles"]) if row else []



def _clean_roles(roles: list | None) -> list[str]:
    """v_participant_roles собирает роли через LEFT JOIN + array_agg: у человека без строк
    в participant_role получается {NULL}, а не пустой массив."""
    return [role for role in (roles or []) if role]



def resolve_caps(conn, participant_id: int) -> list[str]:
    rows = conn.execute(
        "select capability from v_participant_capabilities where participant_id = %s",
        (participant_id,)).fetchall()
    return sorted(r["capability"] for r in rows)



def ensure_role_row(conn, participant_id: int, role: str) -> None:
    """Строка `participant_role` — единственный источник прав (`v_participant_capabilities`).

    Без неё человек с ролью в `participant.role` получает «Возможностей: 0»
    (обращение №24: регистрация создавала запись участника, но не строку роли).
    """
    conn.execute("""insert into participant_role (participant_id, role)
                    values (%s, %s) on conflict do nothing""", (participant_id, role))



# Схема БД описывается один раз — файлами db/init/*.sql, накат и учёт — Alembic
# (docs/reports/132). Второго описания здесь нет намеренно: `DDL` с подмножеством
# таблиц поднимал сервис на отставшей базе, и Mini App падал 500-й на новых колонках.


def now() -> datetime:
    return datetime.now(TZ)



def audit(conn, actor_type: str, actor_id: int | None, action: str,
          entity_type: str, entity_id: str | None, before: Any = None,
          after: Any = None, correlation_id: str | None = None,
          source: Any = None) -> None:
    """Append-only журнал с hash-цепочкой."""
    row = conn.execute(
        "select hash from audit_event order by id desc limit 1"
    ).fetchone()
    prev_hash = row["hash"] if row else "genesis"
    payload = {"ts": now().isoformat(), "actor_type": actor_type, "actor_id": actor_id,
               "action": action, "entity_type": entity_type, "entity_id": entity_id,
               "before": before, "after": after}
    digest = hashlib.sha256(
        (prev_hash + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)).encode()
    ).hexdigest()
    conn.execute(
        """insert into audit_event
           (ts, actor_type, actor_id, action, entity_type, entity_id,
            before, after, correlation_id, source, prev_hash, hash)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (now(), actor_type, actor_id, action, entity_type, entity_id,
         json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
         json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
         correlation_id,
         json.dumps(source or {}, ensure_ascii=False, default=str),
         prev_hash, digest),
    )



# Telegram пропускает сообщение до 4096 символов; всё, что длиннее, — не человеческий ввод.
MAX_TEXT_LEN = 5000



def _check_input(value: Any, path: str = "body") -> None:
    """Общий контракт входа для всех моделей (smoke docs/reports/62, S-7, S-11, S-12).

    - `\\x00` Postgres не хранит: такой текст падал бы при записи отложенным 500;
    - текст длиннее MAX_TEXT_LEN — бесплатная нагрузка на разбор и раздувание логов;
    - NaN/Infinity в JSON — не число, а ответ 422 с ними не сериализуется.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{path}: недопустимый символ NUL")
        if len(value) > MAX_TEXT_LEN:
            raise ValueError(f"{path}: текст длиннее {MAX_TEXT_LEN} символов")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: число должно быть конечным")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_input(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_input(item, f"{path}[{index}]")



class ApiIn(BaseModel):
    """База всех входных моделей: проверка входа одна на весь API, а не по полю."""

    @model_validator(mode="before")
    @classmethod
    def _input_contract(cls, data: Any) -> Any:
        _check_input(data)
        return data



log = logging.getLogger("tool-api")



def actor(
    x_actor_chat_id: int | None = Header(default=None, alias="X-Actor-Chat-Id"),
    authorization: str | None = Header(default=None),
) -> dict:
    if not hmac.compare_digest(authorization or "", f"Bearer {TOOL_API_TOKEN}"):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "Неверный сервисный токен",
                       code="bad_service_token")
    if x_actor_chat_id is None:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Нужен заголовок X-Actor-Chat-Id",
                       code="actor_chat_id_required")

    with pool.connection() as conn:
        row = conn.execute("select * from participant where tg_chat_id = %s",
                           (x_actor_chat_id,)).fetchone()

    if row is None and x_actor_chat_id != ADMIN_CHAT_ID:
        raise ApiError(status.HTTP_403_FORBIDDEN, "Участник не найден", code="unknown_participant")

    if row is None:
        # владелец до bootstrap: полный доступ, но записи ещё нет
        return {"id": 0, "tg_chat_id": ADMIN_CHAT_ID, "role": "admin",
                "display_name": "admin", "status": "active", "person_key": None,
                "drive_folder": None, "roles": ["admin"], "caps": ["*"]}

    with pool.connection() as conn:
        roles = resolve_roles(conn, row["id"])
        caps = resolve_caps(conn, row["id"])
    row["roles"] = roles
    row["caps"] = caps
    if not roles:
        row["roles"] = [row["role"]]
    return row



def current(who: dict = Depends(actor)) -> dict:
    """Любой действующий участник: без проверки конкретной возможности."""
    if who.get("status") not in ("active",):
        raise ApiError(status.HTTP_403_FORBIDDEN, "Участник не активирован",
                       code="participant_inactive")
    return who



def require(capability: str):
    def dep(who: dict = Depends(actor)) -> dict:
        # архивный участник не пользуется правами, даже если они остались в таблицах
        if who.get("status") not in ("active",):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "participant_archived")
        caps = who.get("caps") or []
        if "*" not in caps and capability not in caps:
            with pool.connection() as conn:
                audit(conn, "human", who.get("id"), "access.denied", "capability", capability,
                      after={"required": capability, "roles": who.get("roles", [])})
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"capability required: {capability}")
        return who
    return dep



def _participant_out(p: dict, roles: list[str] | None = None,
                     caps: list[str] | None = None) -> dict:
    return {"id": p["id"], "chat_id": p["tg_chat_id"], "display_name": p["display_name"],
            "person_key": p.get("person_key"), "role": p["role"], "status": p["status"],
            "drive_folder": p.get("drive_folder"),
            "roles": roles if roles is not None else p.get("roles", [p["role"]]),
            "capabilities": caps if caps is not None else p.get("caps", [])}



def _person_card(participant_id: int, viewer_id: int | None = None,
                 viewer_caps: list[str] | None = None) -> dict:
    """Карточка человека для админ-панели: только чтение (Ш-50, обращение №25).

    Правки остаются за существующими действиями (кнопки «Роли» и «Возможности»,
    тариф): карточка ничего не меняет, поэтому и права у неё только на чтение.
    Телефон чужого человека маскируется, если нет `participants.contact`.
    """
    with pool.connection() as conn:
        row = conn.execute("select * from participant where id = %s",
                           (participant_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        roles_row = conn.execute(
            "select roles from v_participant_roles where participant_id = %s",
            (participant_id,)).fetchone()
        roles = _clean_roles(roles_row["roles"]) if roles_row else [row["role"]]
        caps = resolve_caps(conn, participant_id)
        aliases = conn.execute(
            "select alias, alias_kind from person_alias where participant_id = %s "
            "order by id", (participant_id,)).fetchall()
        shows = conn.execute(
            """select id, title, starts_at, status from event_plan
               where kind = 'show' and %s = any(participant_ids)
               order by starts_at desc limit 5""", (participant_id,)).fetchall()
        feedback = conn.execute(
            """select id, kind, status, created_at, left(text, 200) as text
               from feedback where participant_id = %s order by id desc limit 5""",
            (participant_id,)).fetchall()
    birthday = row.get("birth_date") or row.get("birthday")
    today = datetime.now(TZ).date()
    return {"ok": True, "data": {
        **_participant_out(row, roles, caps),
        "aliases": [{"alias": a["alias"], "kind": a["alias_kind"]} for a in aliases],
        "travel_minutes": row["travel_minutes"],
        "phone": phone_for(viewer_id, participant_id, row["phone"], viewer_caps),
        "birthday": birthday.isoformat() if birthday else None,
        "tariff": _tariff_state(row, today.replace(day=1)),
        "tariff_next": _tariff_state(row, _next_month_start(today)),
        "plays": row["plays"],
        "roles_note": row["roles_note"],
        "shows": [{"id": r["id"], "title": r["title"],
                   "starts_at": r["starts_at"].isoformat(),
                   "status": r["status"]} for r in shows],
        "feedback": [{"id": r["id"], "kind": r["kind"], "status": r["status"],
                      "created_at": r["created_at"].isoformat(),
                      "text": r["text"]} for r in feedback],
    }}



def webapp_actor(authorization: str | None = Header(default=None)) -> dict:
    """
    Кто открыл Mini App. Токен выдаётся после проверки initData,
    поэтому здесь достаточно проверить подпись и срок.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no_webapp_token")
    try:
        chat_id = verify_token(authorization.split(" ", 1)[1], WEBAPP_SECRET)
    except InitDataError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, str(exc), code="init_data_invalid") from exc

    with pool.connection() as conn:
        row = conn.execute("select * from participant where tg_chat_id = %s",
                           (chat_id,)).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_registered")
        if row["status"] == "archived":
            raise ApiError(status.HTTP_403_FORBIDDEN, "Доступ закрыт.", code="participant_archived")
        if row["status"] == "blocked":
            # блокировка закрывает доступ и в Mini App, а не только в чате (Ш-07, FR-A9):
            # иначе человек с уже открытой сессией продолжал бы читать и писать
            raise ApiError(status.HTTP_403_FORBIDDEN, "Доступ закрыт.", code="participant_blocked")
    return {"id": row["id"], "chat_id": chat_id, "status": row["status"],
            "display_name": row["display_name"]}



KIND_TITLES_RU = {"rehearsal": "Репетиция", "show": "Показ", "training": "Тренинг",
                  "rental": "Аренда", "meeting": "Встреча"}

WEEKDAY_TITLES = ["понедельник", "вторник", "среда", "четверг", "пятница",
                  "суббота", "воскресенье"]



def _people_with_aliases(conn, include_archived: bool = False) -> list[dict]:
    """Люди со всеми именами, по которым их можно узнать: имя, ключ, псевдонимы."""
    rows = conn.execute("""
        select p.id, p.display_name, p.person_key, p.role, p.status, p.tg_chat_id,
               coalesce(array_agg(a.alias) filter (where a.alias_kind <> 'role'), '{}') as aliases,
               coalesce(array_agg(a.alias) filter (where a.alias_kind = 'role'), '{}') as role_aliases
          from participant p
          left join person_alias a on a.participant_id = p.id
         where (%s or p.status <> 'archived')
         group by p.id
         order by p.display_name""", (include_archived,)).fetchall()
    return [dict(row) for row in rows]



# «Просьба о доработке» — вид обращения, а не отдельное действие (Ш-12, О1):
# человек просит новую функцию тем же путём `POST /tools/feedback`.
FEEDBACK_KINDS = {"bug": "Проблема", "wish": "Пожелание", "wish-fl": "Просьба о доработке",
                  "question": "Вопрос", "praise": "Хорошо работает"}

FEEDBACK_STATUS_TITLES = {"seen": "Обращение посмотрели", "planned": "Взяли в план",
                          "done": "Обращение сделано", "rejected": "Обращение отклонено"}



GRID_STATES = ("empty", "free", "busy", "uncertain")

GRID_HOURS = range(WORK_DAY_START, WORK_DAY_END)   # рабочий день 08:00–23:00



def _month_days(month: str) -> list[date]:
    first = datetime.strptime(month, "%Y-%m").date()
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]



SEARCH_LIMIT = 40



# Раздел «Справка» (Ш-49, V-010). Тексты — файлы репозитория `bot/manifests/help/<роль>.md`,
# а не строки кода: их правит заказчик, а не разработчик. Право `howto.read` пока только
# у администратора, поэтому раздел виден только ему (db/init/038_howto_read.sql).
HOWTO_DIR = Path(__file__).resolve().parents[3] / "bot" / "manifests" / "help"

HOWTO_SECTIONS = (("admin", "Администратор"), ("org", "Организатор"), ("troupe", "Актёр труппы"))



def _sheets():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    cred = service_account.Credentials.from_service_account_file(
        GOOGLE_SA_JSON, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=cred, cache_discovery=False)



def default_venue_code() -> str:
    with pool.connection() as conn:
        row = conn.execute("select value from app_setting "
                           "where key = 'default_venue_code'").fetchone()
    return (row["value"] if row else "stage") or "stage"



def venue_busy_for(code: str, date_from: date, date_to: date,
                   raw_events: list[dict] | None = None) -> tuple[list, dict]:
    """
    Занятость пространства (docs/architecture/47).

    Два источника: свой календарь зала (freebusy) или общий календарь
    с пометкой `svoi_venue` в событии. Второй нужен, когда залы живут
    в одном календаре и различаются только свойством события.
    """
    with pool.connection() as conn:
        row = conn.execute("select code, name, calendar_id from venue where code = %s",
                           (code,)).fetchone()
    if row is None:
        return [], {"venue": code, "name": "", "source": "unknown", "applied": False}

    info = {"venue": code, "name": row["name"], "source": "events", "applied": True}

    if row["calendar_id"]:
        try:
            payload = _calendar().freebusy().query(body={
                "timeMin": datetime.combine(date_from, datetime.min.time(), TZ).isoformat(),
                "timeMax": datetime.combine(date_to + timedelta(days=1),
                                            datetime.min.time(), TZ).isoformat(),
                "items": [{"id": row["calendar_id"]}],
            }).execute()
            info["source"] = "freebusy"
            return parse_freebusy(payload, row["calendar_id"]), info
        except GOOGLE_API_ERRORS as exc:
            # адаптер календаря: занятость зала — best effort, окна считаем по отметкам
            log.warning("freebusy зала %s недоступен: %s", code, str(exc)[:120])
            info["applied"] = False
            return [], info

    events = raw_events if raw_events is not None else fetch_events(date_from, date_to)
    if not events:
        info["applied"] = False
        info["reason"] = "no_events"
        return [], info
    return busy_for_venue(events, code, default_venue_code()), info



def fetch_events(date_from: date, date_to: date,
                 draft: bool = False) -> list[dict]:
    """
    События календаря за период. Best effort: если календарь недоступен,
    поиск окон работает только по отметкам занятости (docs/architecture/45).
    """
    cid = CALENDAR_ID_DRAFT if draft else CALENDAR_ID_READ
    if not cid:
        return []
    try:
        service = _calendar()
        data = service.events().list(
            calendarId=cid,
            timeMin=datetime.combine(date_from, datetime.min.time(), TZ).isoformat(),
            timeMax=datetime.combine(date_to + timedelta(days=1), datetime.min.time(),
                                     TZ).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=500).execute()
        return data.get("items") or []
    except GOOGLE_API_ERRORS as exc:
        # адаптер календаря: без него поиск окон идёт только по отметкам занятости
        log.warning("календарь недоступен для поиска окон: %s", str(exc)[:150])
        return []



MONTHS_RU = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль",
             "август", "сентябрь", "октябрь", "ноябрь", "декабрь")



WEEK_DAYS = 7



WEEKDAY_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня",
                   "июля", "августа", "сентября", "октября", "ноября", "декабря")



def page_texts(*sections: str) -> dict[str, str]:
    """Тексты для страницы: секции конфигурации, слитые в один объект.

    Подписи живут в `config/ui-texts.yaml`: заказчик правит формулировки и видит
    результат на следующем открытии страницы, без релиза.
    """
    merged: dict[str, str] = {}
    for name in sections:
        merged.update(ui_texts.section(name))
    return merged



def _next_month_start(day: date) -> date:
    """Первый день следующего месяца — с него действует новый тариф (FR-B7)."""
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)



def _tariff_state(person: dict, period_start: date) -> dict:
    """Тариф, который начисление возьмёт за месяц, начинающийся в `period_start`."""
    tariff = person["dues_tariff"]
    tariff_from = person["dues_tariff_from"]
    applies = (tariff is not None and float(tariff) > 0
               and (tariff_from is None or tariff_from <= period_start))
    return {"tariff": float(tariff) if tariff is not None else None,
            "tariff_from": tariff_from.isoformat() if tariff_from else None,
            "amount": float(tariff) if applies else None}



class DuesTariffIn(ApiIn):
    tariff: float | None = Field(default=None, ge=0, le=1_000_000,
                                 description="взнос в месяц, ₽; null — снять тариф")



BUDGET_CATS = [
    ("Тренинги и занятия", ["тренинг"]),
    # фамилий получателей нет и здесь: подсказки приходят из данных (app/participant_names.py)
    ("Гонорары и доли", ["гонорар", "доля", "ов"]),
    ("Аренда помещения", ["аренд"]),
    ("Коммунальные платежи", ["комуналк", "коммуналк", "электрик", "вода", "жкх"]),
    ("Закупки к спектаклям", ["закупк"]),
    ("Реклама и полиграфия", ["программк", "афиш", "макет", "репертуарк", "реклам", "печат"]),
    ("Ремонт и содержание", ["ремонт", "кран", "раковин", "замок", "линолеум", "краск", "прожектор"]),
    ("Фото/видео", ["съемк", "видео", "фото", "монтаж", "оператор"]),
    ("Реквизит и костюмы", ["реквизит", "костюм", "ткан", "декорац"]),
    ("Расходники и хозтовары", ["кофе", "чай", "бумаг", "картридж", "цвет", "бутыл"]),
]

EXP_VERBS = ["расход", "потрат", "оплат", "заплат", "купил", "отдал", "перевел", "снял", "закуп"]

INC_VERBS = ["доход", "приход", "пришл", "поступ", "сдал", "выручк", "пожертвован"]



MANIFEST_DIR = Path(__file__).resolve().parents[3] / "bot" / "manifests"



# Параметры подключения к LLM — в одном месте (app/llm.py, docs/infrastructure/75 §7.4).
# Читаются из .env при старте; старые имена OPENROUTER_API_KEY/ASSISTANT_MODEL работают
# как алиасы, поэтому существующие .env и документация не ломаются.
LLM_API_KEY = llm.api_key()

LLM_MODEL = llm.model()

_prompt_cache: dict[tuple[str, str | None], str] = {}



def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""



def _calendar():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    cred = service_account.Credentials.from_service_account_file(
        GOOGLE_SA_JSON, scopes=["https://www.googleapis.com/auth/calendar"])
    return build("calendar", "v3", credentials=cred, cache_discovery=False)



# палитра из config/calendar-palette.yaml: цвет задаётся на событии
EVENT_COLORS = {"rental": 10, "show": 11, "rehearsal": 9,
                "training": 5, "meeting": 8, "other": 8}



def _webapp_viewer(who: dict) -> dict:
    """Участник Mini App вместе с ролями и правами, как их считает сервер бота."""
    with pool.connection() as conn:
        roles = resolve_roles(conn, who["id"]) or ["troupe"]
        caps = resolve_caps(conn, who["id"])
    return dict(who, roles=roles, caps=caps, status=who.get("status") or "active")



def _webapp_require(who: dict, capability: str) -> dict:
    """Право на действие в Mini App: те же правила, что у `require()`.

    `require()` — зависимость FastAPI, а здесь обработчик зовётся напрямую;
    без этой проверки действие осталось бы без контроля прав.
    """
    viewer = _webapp_viewer(who)
    if viewer["status"] not in ("active",):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "participant_archived")
    caps = viewer.get("caps") or []
    if "*" not in caps and capability not in caps:
        with pool.connection() as conn:
            audit(conn, "human", viewer.get("id"), "access.denied", "capability", capability,
                  after={"required": capability, "roles": viewer.get("roles", []),
                         "via": "webapp"})
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"capability required: {capability}")
    return viewer



def _caps_for(conn, participant_id: int) -> set[str]:
    rows = conn.execute(
        "select capability from v_participant_capabilities where participant_id = %s",
        (participant_id,)).fetchall()
    return {row["capability"] for row in rows}



def _who_caps(who: dict) -> list[str]:
    """Возможности человека для отрисовки навигации (страницы — константы)."""
    with pool.connection() as conn:
        return sorted(_caps_for(conn, who["id"]))



# Плитки хаба: раздел → (ключ, иконка, заголовок, подсказка, адрес, нужные права).
HOME_SECTIONS = (
    ("Моё", (
        ("week", "🗓", "Занятость", "неделя, шаблон, отметки", "/webapp/days", ()),
        ("dues", "💸", "Мои взносы", "начислено и оплачено", "/webapp/dues", ("budget.read.self",)),
        ("profile", "👤", "Профиль", "анкета и тариф", "/webapp", ()),
        ("notifications", "🔔", "Уведомления", "что и когда приходит", "/webapp/notifications", ()),
    )),
    ("Студия", (
        ("schedule", "📅", "Расписание", "ближайшие события", "/webapp/schedule", ()),
        ("slots", "🔍", "Поиск окон", "когда всем удобно", "/webapp/slots", ()),
        ("feedback", "💬", "Обратная связь", "вопрос или предложение", "/webapp/feedback", ()),
    )),
    ("Люди", (
        ("pending", "📥", "Заявки", "ждут решения", "/webapp/admin/t/pending", ("registration.approve",)),
        ("people", "👥", "Участники", "активные и ожидающие", "/webapp/admin/t/people", ("participants.view",)),
        ("invites", "✉️", "Приглашения", "коды для новых", "/webapp/admin/t/invites", ("registration.code.issue",)),
        ("archive", "🗄", "Архив", "вернуть человека", "/webapp/admin/t/archive", ("participants.manage",)),
    )),
    ("Деньги", (
        ("tx", "₽", "Расход и доход", "запись в бюджет и лист", "/webapp/tx", ("budget.write",)),
        ("budget", "📊", "Бюджет", "месяц, статьи, план и факт", "/webapp/admin/t/budget", ("budget.read",)),
        ("reconcile", "🧾", "Сверка листов", "база против листа", "/webapp/reconcile", ("budget.import",)),
    )),
    ("Система", (
        ("broadcast", "📣", "Рассылка", "шаблон, текст, отправка", "/webapp/broadcast", ("notify.send",)),
        ("panel", "🛠", "Админ-панель", "права, журнал, сверка", "/webapp/admin/t/rights", ("participants.manage",)),
    )),
)


ROLE_LABELS = {"admin": "администратор", "org": "организатор", "troupe": "студиец"}



CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

