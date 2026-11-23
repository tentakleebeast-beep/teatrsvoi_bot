"""Реестр объектов Google: единственное место, где сказано «кто я для этого файла».

Зачем (docs/development/186 §2.2). До этой карты адрес объекта брался там, где он
понадобился: бюджет писал по `BUDGET_SPREADSHEET_ID`, занятость — «в первый лист файла»,
календарь — «сначала черновой, потом боевой». Из-за этого боевой календарь правился
кодом, хотя дизайн 185 (D10) это запрещает, а `sheet_binding`/`row_map` лежали в схеме
мёртвыми.

Правила модуля:

1. Пишущий вызов Google возможен только с ключом из `WRITE_TARGETS`; ключа нет в
   реестре — `write_target_refused` (403), пустой идентификатор — `write_target_unset`
   (503). Молчаливого пропуска записи нет.
2. Живые листы и боевой календарь есть только в `READ_SOURCES`: писать в них нельзя
   даже по ошибке — ключа для этого не существует. Решение владельца V-120 б
   (перекраска `colorId` в боевом календаре) сознательно не реализовано обходным
   путём (`read_id` в пишущий вызов): ключ записи добавляется правкой этого реестра
   отдельной картой, до неё перекраска в `target` отказывает 409
   `recolor_target_forbidden` (`docs/development/190` I-7).
3. Идентификаторы не зашиты в код: значение берётся из настроек (модульные константы
   ядра, собранные из `Settings` при старте), а модуль знает только имя поля.
4. `google.mode` на набор объектов записи не влияет (дизайн D7): в переходном и целевом
   режиме пишем в одно и то же.

Тесты: `tests/unit/test_google_targets.py`, `tests/unit/test_google_write_boundary.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app import core
from app.errors import ApiError

SHEET = "sheet"
CALENDAR = "calendar"
DRIVE = "drive"

#: Режимы приложения (186 §2.3). `transitional` — боевой календарь и живые листы ещё
#: живут рядом с ботом; `target` — человека выгнали из бота в Google, выгрузка сделана.
MODE_TRANSITIONAL = "transitional"
MODE_TARGET = "target"
MODES = (MODE_TRANSITIONAL, MODE_TARGET)
DEFAULT_MODE = MODE_TRANSITIONAL

#: Права, требуемые у сервисного аккаунта на объект (186 §6). Нужны и отчёту
#: `scripts/google_rights.py`, и тестам: «редактор на объектах записи, читатель на живых».
ROLE_READER = "reader"
ROLE_WRITER = "writer"


@dataclass(frozen=True, slots=True)
class Target:
    """Объект Google, о котором бот знает: что это и по какой раскладке читать."""

    key: str
    kind: str
    layout: str = ""
    required_role: str = ROLE_WRITER
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def writable(self) -> bool:
        return self.key in WRITE_TARGETS


#: Объекты записи. Здесь только свои файлы: копия бюджета, копия занятости, тестовый
#: лист цветов и черновой календарь. Живых листов и боевого календаря тут нет и не будет.
WRITE_TARGETS: dict[str, Target] = {
    "budget.transactions": Target(
        "budget.transactions", SHEET, "flat_table",
        meta={"sheet_title": "Транзакции", "header_row": 1, "first_data_row": 2,
              "first_col": 1, "last_col": 13}),
    "schedule.workload": Target(
        "schedule.workload", SHEET, "monthly_blocks",
        meta={"header_row": 1, "first_col": 1, "meta": {"date_col": 2, "people_from": 4}}),
    "schedule.test": Target(
        "schedule.test", SHEET, "hour_matrix",
        # Шапка hour_matrix — только A1 «Час»: строка 1 хранит даты месяца и меняется
        # каждый месяц, поэтому суммой её крыть нельзя (иначе `header_mismatch`
        # навсегда после первой смены месяца). Часы и даты проверяет
        # `schedule_test_sheet.plan_probes` перед записью.
        meta={"header_row": 1, "first_col": 1, "last_col": 1,
              "meta": {"dates_row": 1,
                       "hours": [f"{hour:02d}:00" for hour in range(8, 24)]}}),
    "calendar.draft": Target("calendar.draft", CALENDAR),
}

#: Объекты чтения. Живые файлы и боевой календарь — читатель, не редактор.
READ_SOURCES: dict[str, Target] = {
    "schedule.human": Target("schedule.human", SHEET, "monthly_blocks", ROLE_READER),
    "budget.live": Target("budget.live", SHEET, "flat_table", ROLE_READER),
    "schedule.workload": Target("schedule.workload", SHEET, "monthly_blocks"),
    "schedule.test": Target("schedule.test", SHEET, "hour_matrix"),
    "calendar.read": Target("calendar.read", CALENDAR, required_role=ROLE_READER),
    "drive.root": Target("drive.root", DRIVE, required_role=ROLE_READER),
}

#: Ключ → идентификатор объекта. Атрибуты читаются напрямую, а не через `getattr` по
#: имени: иначе проверка мёртвого кода не видит, что константы ядра используются.
def _target_id(key: str) -> str:
    if key == "budget.transactions":
        value = core.BUDGET_SPREADSHEET_ID
    elif key == "schedule.workload":
        value = core.SCHEDULE_SPREADSHEET_ID
    elif key == "schedule.test":
        value = core.SCHEDULE_TEST_SPREADSHEET_ID
    elif key == "calendar.draft":
        value = core.CALENDAR_ID_DRAFT
    elif key == "schedule.human":
        value = core.SCHEDULE_HUMAN_SPREADSHEET_ID
    elif key == "budget.live":
        value = core.FINANCE_SOURCE_ID
    elif key == "calendar.read":
        value = core.CALENDAR_ID_READ
    elif key == "drive.root":
        value = core.DRIVE_ROOT_ID
    else:
        value = ""
    return str(value or "").strip()


def resolve(key: str) -> str:
    """Идентификатор объекта **записи** по ключу реестра.

    Ключ вне `WRITE_TARGETS` — `write_target_refused` (403): ключ не «пустая строка»,
    иначе новая запись в чужой объект прошла бы молча. Пустой id — `write_target_unset`
    (503): объект объявлен, но не настроен, и притворяться, что записали, нельзя.
    """
    if key not in WRITE_TARGETS:
        raise ApiError(403, "write_target_refused: объект не разрешён для записи",
                       code="write_target_refused")
    value = _target_id(key)
    if not value:
        raise ApiError(503, "write_target_unset: объект записи не настроен",
                       code="write_target_unset")
    return value


def read_id(key: str) -> str:
    """Идентификатор объекта чтения. Пустая строка — источник не настроен (не ошибка)."""
    if key not in READ_SOURCES:
        raise ApiError(400, "unknown_source: источника нет в реестре", code="unknown_source")
    return _target_id(key)


def target(key: str) -> Target:
    """Описание объекта чтения или записи (для отчётов и раскладок)."""
    found = WRITE_TARGETS.get(key) or READ_SOURCES.get(key)
    if found is None:
        raise ApiError(400, "unknown_source: объекта нет в реестре", code="unknown_source")
    return found


def allowed_for_write(key: str, conn=None) -> bool:
    """Разрешён ли ключ к записи в текущем режиме.

    Набор объектов записи в обоих режимах одинаков (дизайн D7) — режим здесь только
    для того, чтобы вопрос «а в target?» имел ответ в коде, а не в голове читателя.
    `conn` передаётся, когда чтение режима идёт в уже открытом соединении: иначе
    вызов открывал бы своё и в unit-тестах упирался в пул.
    """
    mode = google_mode(conn)
    return key in WRITE_TARGETS and mode in MODES


def _setting_value(row) -> str:
    """Значение из строки настроек: соединение ядра отдаёт словарь, тест — кортеж."""
    if row is None:
        return ""
    try:
        return str(row["value"] or "")
    except (TypeError, KeyError, IndexError):
        return str(row[0] or "")


def google_mode(conn=None) -> str:
    """Текущий режим приложения. Отсутствие строки — `transitional` (защита от БД до 072)."""
    if conn is not None:
        row = conn.execute("select value from app_setting where key = 'google.mode'").fetchone()
    else:
        with core.pool.connection() as own:
            row = own.execute("select value from app_setting where key = 'google.mode'").fetchone()
    value = _setting_value(row).strip()
    return value if value in MODES else DEFAULT_MODE


def set_google_mode(conn, mode: str, actor_id: int | None, backup: str | None = None) -> dict:
    """Переход режима. Право администратора проверяет роутер, здесь — правило и аудит.

    Переход в `target` без выгрузки запрещён (186 §2.3): выгрузку делает человек,
    бот её не создаёт, поэтому единственное, что он может проверить, — наличие ссылки.
    """
    if mode not in MODES:
        raise ApiError(400, "mode_unknown: неизвестный режим", code="mode_unknown")
    if mode == MODE_TARGET and not (backup or "").strip():
        raise ApiError(400, "mode_backup_required: перед переходом сделайте выгрузку",
                       code="mode_backup_required")
    before = google_mode(conn)
    conn.execute("""insert into app_setting (key, value) values ('google.mode', %s)
                    on conflict (key) do update set value = excluded.value""", (mode,))
    after = {"mode": mode, "backup": (backup or "").strip() or None}
    core.audit(conn, "human", actor_id, "google.mode.set", "app_setting", "google.mode",
               before={"mode": before}, after=after)
    return after
