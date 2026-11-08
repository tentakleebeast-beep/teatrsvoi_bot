"""
Авторизация Mini App (docs/development/43) и реестр разделов (docs/development/92 §10).

Telegram передаёт в WebView строку `initData` с подписью. Проверяем её по
официальному алгоритму, выдаём короткоживущий токен и дальше работаем с ним.

Зачем отдельный токен, если есть initData: initData нельзя отозвать, он живёт
долго и его можно случайно залогировать. Токен ограничен по времени, подписан
нашим секретом и не содержит ничего, кроме chat_id.

Здесь же лежит `SECTIONS` — единственный список разделов Mini App: возможность,
название, адрес, подпись. Из него строятся подпись четвёртой вкладки, блок
«Управление», блок «Требует внимания», строка «Файлы студии» и маршруты экранов
разделов. Реестр отвечает только на вопрос «что показывать»; права на данные
по-прежнему проверяются в эндпоинтах.

Модуль чистый (кроме hmac/hashlib): тесты не поднимают FastAPI.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

DEFAULT_TTL_SECONDS = 1800          # полчаса работы в WebView
MAX_AUTH_AGE = 3600                 # initData старше часа не принимаем


class InitDataError(ValueError):
    """Подпись не сошлась, данные просрочены или испорчены."""


def check_secrets(env: str, webapp_secret: str, tool_api_token: str) -> None:
    """
    В проде токен сервиса не имеет права подписывать токены людей.

    Локально `WEBAPP_SECRET` может быть пустым и берётся из `TOOL_API_TOKEN` —
    так удобнее. На проде это означало бы, что утечка сервисного токена даёт
    возможность выписать себе токен Mini App на любого человека.
    """
    if env != "prod":
        return
    if not webapp_secret or webapp_secret == tool_api_token:
        raise ValueError("WEBAPP_SECRET must differ from TOOL_API_TOKEN in prod")


def _secret_key(bot_token: str) -> bytes:
    """Первый шаг алгоритма: HMAC от строки "WebAppData" с ключом-токеном бота."""
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def validate_init_data(init_data: str, bot_token: str,
                       max_age: int = MAX_AUTH_AGE,
                       now: int | None = None) -> dict:
    """
    Проверяет подпись initData и возвращает разобранные поля.

    Бросает InitDataError, если подпись неверна, поля нет или данные просрочены.
    Пустой ответ лучше поддельного: Mini App без подписи не должен получать данные.
    """
    if not init_data or not bot_token:
        raise InitDataError("empty_init_data")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("no_hash")

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    expected = hmac.new(_secret_key(bot_token), check_string.encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("bad_signature")

    auth_date = int(pairs.get("auth_date") or 0)
    current = now if now is not None else int(time.time())
    if auth_date <= 0 or current - auth_date > max_age:
        raise InitDataError("expired")

    user = {}
    if "user" in pairs:
        try:
            user = json.loads(pairs["user"])
        except json.JSONDecodeError as exc:
            raise InitDataError("bad_user") from exc

    chat_id = (user.get("id")
               or (json.loads(pairs["chat"]).get("id") if "chat" in pairs else None))
    if not chat_id:
        raise InitDataError("no_user")

    return {"chat_id": int(chat_id), "user": user, "auth_date": auth_date,
            "username": user.get("username")}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_token(chat_id: int, secret: str, ttl: int = DEFAULT_TTL_SECONDS,
                now: int | None = None) -> str:
    """Короткоживущий токен: payload в base64 + подпись."""
    issued = now if now is not None else int(time.time())
    payload = _b64(json.dumps({"chat_id": chat_id, "exp": issued + ttl}).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_token(token: str, secret: str, now: int | None = None) -> int:
    """Возвращает chat_id из токена. Бросает InitDataError при любой проблеме."""
    if not token or "." not in token:
        raise InitDataError("no_token")
    payload, signature = token.rsplit(".", 1)
    expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        raise InitDataError("bad_token_signature")
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, TypeError) as exc:
        # подпись уже проверена: кривой payload — ошибка клиента, не сбой сервиса
        raise InitDataError("bad_token_payload") from exc

    current = now if now is not None else int(time.time())
    if int(data.get("exp", 0)) < current:
        raise InitDataError("token_expired")
    return int(data["chat_id"])


# --- какие разделы админ-панели видит человек ---------------------------------
# Обращение №14: панель в чате упирается в лимит сообщения и не даёт таблиц.
# Раздел открывается по той же возможности, что и кнопка `panel:*` в боте.
# Это отдельная ось от `SECTIONS`: здесь блоки JSON-ответа панели бота, у них своя
# семантика («заявки» открывает и `participants.manage`, а не только согласование)
# и свой порядок, как в панели бота. Названий и адресов у блоков нет — только ключи.
ADMIN_BLOCKS: dict[str, tuple[str, ...]] = {
    "people": ("participants.view", "participants.manage"),
    "pending": ("registration.approve", "participants.manage"),
    # «Права» — не просмотр, а управление: матрица роль → возможности отдаётся тому,
    # кто выдаёт права (FR-A6, docs/development/92 §2.4). С `participants.view` панель
    # показывала организатору вход, а действие отвечало 403 (Ш-02, Л4).
    "rights": ("participants.manage",),
    "budget": ("budget.read",),
    "feedback": ("feedback.triage",),
    "audit": ("audit.view",),
    "invites": ("registration.code.issue",),
}


def allowed_admin_blocks(caps: list[str]) -> list[str]:
    """Разделы панели по возможностям, в порядке ADMIN_BLOCKS.

    Проверка дублируется на сервере: страница может скрыть вкладку, но данные
    отдаются только тем, у кого есть право на этот раздел.
    """
    if "*" in caps:
        return list(ADMIN_BLOCKS)
    return [name for name, need in ADMIN_BLOCKS.items()
            if any(code in caps for code in need)]


# --- какие действия в панели человеку разрешены -------------------------------
# Ш-33: страница перестала быть «только чтение». Каждое действие проверяется на
# сервере той же возможностью, что и одноимённая кнопка бота; список ниже нужен
# странице, чтобы не рисовать кнопку, которая всё равно получит 403.
ADMIN_ACTIONS: dict[str, tuple[str, ...]] = {
    "approve_pending": ("registration.approve",),
    "manage_rights": ("participants.manage",),
    "set_tariff": ("budget.write",),
    # Д6: начисление взносов за месяц — то же право, что у `POST /tools/dues/accrue`.
    "accrue_dues": ("budget.write",),
    # Ш-37: повтор записи в лист — то же право, что у эндпоинта
    # `/tools/budget/sheet-retry` (`budget.write`).
    "retry_sheet": ("budget.write",),
    # Приглашения: код выпускается кнопкой в панели, а не командой в чате.
    "issue_codes": ("registration.code.issue",),
}


def allowed_admin_actions(caps: list[str]) -> dict[str, bool]:
    """Разрешённые действия панели: имя действия → можно ли его звать."""
    if "*" in caps:
        return {name: True for name in ADMIN_ACTIONS}
    return {name: any(code in caps for code in need)
            for name, need in ADMIN_ACTIONS.items()}


# --- разделы Mini App: один реестр вместо пяти списков ------------------------
# До этого «какой раздел видит человек с такой возможностью» было описано в пяти
# местах: навигация (`pages/layout.py`), маршруты экранов (`pages/routes.py`),
# строка файлов (`pages/home.py`) и список «Управления» (`api/webapp_api.py`).
# Добавление раздела требовало четырёх правок, а забытая давала либо невидимый
# раздел, либо ссылку, которая всё равно получит 403.
#
# Теперь правда одна: `SECTIONS`. Порядок записей — порядок строк в «Управлении»
# (сначала то, что чаще требует решения). Из реестра строятся:
#   * подпись четвёртой вкладки — есть ли у человека хоть один управленческий раздел;
#   * список «Управление» (`api/webapp_api.py`);
#   * блок «Требует внимания» — разделы с подписью `attention`;
#   * строка «Файлы студии» в «Личном» — в ответе данных (`api/webapp_api.py`);
#   * маршрут экрана раздела — записи с `page` (константа в `pages/admin.py`).
# Реестр говорит только «что показывать». Права на данные остаются на эндпоинтах:
# скрытая строка — удобство, а не защита.

MANAGEMENT = "management"   # строка в «Управлении», даёт подпись 4-й вкладки
PERSONAL = "personal"       # строка в «Личном» на «Ещё»
# Вложенный экран: свой адрес и своя запись с `page`, но строки в «Управлении» нет —
# вход в него лежит на родительском экране (так устроены «Люди → Взносы»).
NESTED = "nested"


@dataclass(frozen=True)
class WebappSection:
    """Раздел Mini App: одна возможность, одно название, один адрес, одна подпись."""

    key: str
    caps: tuple[str, ...]
    title: str
    url: str
    caption: str = ""
    group: str = MANAGEMENT
    count: str = ""        # ключ счётчика в ответе `/webapp/home/data` для бейджа
    attention: str = ""    # подпись в блоке «Требует внимания», пусто — не сигнал
    page: str = ""         # имя константы экрана в `app.pages.admin`


SECTIONS: tuple[WebappSection, ...] = (
    WebappSection("pending", ("registration.approve",), "Заявки",
                  "/webapp/admin/t/pending", "ждут решения", count="pending",
                  attention="ждут решения", page="PENDING_PAGE"),
    WebappSection("people", ("participants.view",), "Люди",
                  "/webapp/admin/t/people", "роли, блокировка, архив", page="PEOPLE_PAGE"),
    # Д6/Д8: вложенный экран «Люди → Взносы» начисляет взносы и правит тариф.
    # Право — `budget.write`, как у путей записи; поэтому организатору не нужна
    # админ-панель бота (расхождение 4.5 отчёта 122).
    WebappSection("dues", ("budget.write",), "Взносы",
                  "/webapp/admin/t/dues", "тариф и начисление",
                  group=NESTED, page="DUES_ADMIN_PAGE"),
    WebappSection("invites", ("registration.code.issue",), "Приглашения",
                  "/webapp/admin/t/invites", "коды для новых", page="INVITES_PAGE"),
    WebappSection("rights", ("participants.manage",), "Права",
                  "/webapp/admin/t/rights", "роль и возможности", page="RIGHTS_PAGE"),
    WebappSection("archive", ("participants.manage",), "Архив",
                  "/webapp/admin/t/archive", "вернуть человека", page="ARCHIVE_PAGE"),
    WebappSection("feedback", ("feedback.triage",), "Обращения",
                  "/webapp/admin/t/feedback", "очередь разбора", count="feedback",
                  attention="открытые", page="FEEDBACK_QUEUE_PAGE"),
    WebappSection("audit", ("audit.view",), "Аудит",
                  "/webapp/admin/t/audit", "последние события", page="AUDIT_PAGE"),
    WebappSection("broadcast", ("notify.send",), "Рассылка",
                  "/webapp/broadcast", "текст и адресаты"),
    WebappSection("reconcile", ("budget.import",), "Сверка листов",
                  "/webapp/reconcile", "база против таблицы"),
    WebappSection("services", ("admin.panel",), "Сервисы",
                  "/webapp/services", "состояние приложений"),
    WebappSection("agent", ("admin.panel",), "Разбор агента",
                  "/webapp/admin/t/agent", "шаги, инструменты, ответ", page="AGENT_PAGE"),
    # FR-G17 (О5): общие значения уведомлений — админ-панель; частные — профиль (О3).
    WebappSection("notifications", ("admin.panel",), "Уведомления",
                  "/webapp/admin/t/notifications", "тихие часы по умолчанию",
                  page="NOTIFICATION_DEFAULTS_PAGE"),
    # Личный раздел на «Ещё»: пока открыт администратору (92 §9.1).
    WebappSection("files", ("admin.panel",), "Файлы студии",
                  "/webapp/files", "записи, фонограммы, тексты", group=PERSONAL),
    # Заметки — личное у всех: право `notes.write` есть у каждой действующей роли,
    # поэтому строка видна и актёру, а не только администратору (FR-F7).
    WebappSection("notes", ("notes.write",), "Заметки",
                  "/webapp/notes", "личные и общие, с тегами", group=PERSONAL),
)


def section_visible(section: WebappSection, caps: list[str] | tuple[str, ...] | set[str] | None) -> bool:
    """Открыт ли раздел человеку. Достаточно любой из его возможностей, `*` — всё."""
    current = set(caps or ())
    return "*" in current or any(code in current for code in section.caps)


def sections_for(caps: list[str] | tuple[str, ...] | set[str] | None, *,
                 group: str | None = None) -> list[WebappSection]:
    """Разделы человека в порядке реестра, при желании — только одной группы."""
    return [section for section in SECTIONS
            if (group is None or section.group == group) and section_visible(section, caps)]


def page_sections() -> tuple[WebappSection, ...]:
    """Разделы со своим экраном: маршрут и страница берутся из записи реестра."""
    return tuple(section for section in SECTIONS if section.page)
