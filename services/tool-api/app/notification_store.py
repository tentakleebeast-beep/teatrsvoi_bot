"""
Хранение настроек уведомлений: общие — в `app_setting`, частные — в `notification_pref`.

FR-G17 (`docs/development/19`, решение П-8): общие настройки студии задаёт
администратор в админ-панели Mini App, частные — участник в профиле; частные важнее
общих. Очередь отправки и планировщик читают их одним правилом —
`app.notifications.resolve_settings`: есть частная запись — действует она, нет —
общая, нет и общей — значения по умолчанию из кода (20:00–08:00, FR-G3).

Источник значений один: экран профиля, экран админ-панели, чат и скрипты зовут эти
функции, а не пишут SQL каждая сама. Иначе экран и очередь разойдутся в том, что
считается тихими часами (это уже случалось: три разных значения в `docs/90` П-7).

Общие значения лежат в `app_setting` (таблица «ключ → текст», миграция `025`), новых
таблиц не заводим: значения читаются редко (раз на прогон очереди) и по одному ключу.
"""

from __future__ import annotations

#: Значения по умолчанию, если общей настройки ещё нет (FR-G3, решение 31.10.2026).
DEFAULTS_QUIET_FROM = "20:00"
DEFAULTS_QUIET_TO = "08:00"

#: Ключи `app_setting`. Префикс `notify_` держит их вместе и не даёт спутать
#: с `default_venue_code` — единственным другим значением той же таблицы.
KEY_QUIET_FROM = "notify_quiet_from"
KEY_QUIET_TO = "notify_quiet_to"
KEY_KINDS_MUTED = "notify_kinds_muted"

#: Поля формы ↔ коды типов в `notification_pref.kinds_muted`.
#: «Репетиции» на экране — это тип `availability`: напоминание заполнить занятость.
TYPE_KINDS: dict[str, str] = {
    "rehearsals": "availability",
    "dues": "dues",
    "schedule_changes": "schedule_change",
}


def kinds_to_form(muted: list[str] | tuple[str, ...] | None) -> dict[str, bool]:
    """Список выключенных типов → переключатели формы (включён = не в списке)."""
    codes = set(muted or ())
    return {field: kind not in codes for field, kind in TYPE_KINDS.items()}


def form_to_kinds(rehearsals: bool, dues: bool, schedule_changes: bool) -> list[str]:
    """Переключатели формы → список выключенных типов, в порядке `TYPE_KINDS`."""
    chosen = {"rehearsals": rehearsals, "dues": dues, "schedule_changes": schedule_changes}
    return [kind for field, kind in TYPE_KINDS.items() if not chosen[field]]


def _parse_kinds(value: str | None) -> list[str]:
    return [code.strip() for code in (value or "").split(",") if code.strip()]


def _dump_kinds(muted: list[str]) -> str:
    return ",".join(muted)


def load_defaults(conn) -> dict:
    """Общие настройки студии: `{kinds_muted, quiet_from, quiet_to}`.

    Пустая таблица — не ошибка: действуют значения по умолчанию из кода, а строки
    в `app_setting` появятся при первом сохранении из админ-панели.
    """
    rows = conn.execute(
        "select key, value from app_setting where key = any(%s)",
        ([KEY_QUIET_FROM, KEY_QUIET_TO, KEY_KINDS_MUTED],)).fetchall()
    values = {row["key"]: row["value"] for row in rows}
    return {
        "kinds_muted": _parse_kinds(values.get(KEY_KINDS_MUTED)),
        "quiet_from": values.get(KEY_QUIET_FROM) or DEFAULTS_QUIET_FROM,
        "quiet_to": values.get(KEY_QUIET_TO) or DEFAULTS_QUIET_TO,
    }


def save_defaults(conn, muted: list[str], quiet_from: str, quiet_to: str) -> None:
    """Записать общие настройки. Коммитит вызывающий: у него может быть своя транзакция."""
    for key, value in ((KEY_QUIET_FROM, quiet_from), (KEY_QUIET_TO, quiet_to),
                       (KEY_KINDS_MUTED, _dump_kinds(muted))):
        conn.execute(
            "insert into app_setting (key, value, updated_at) values (%s, %s, now()) "
            "on conflict (key) do update set value = excluded.value, "
            "updated_at = excluded.updated_at", (key, value))


def load_personal(conn, participant_id: int) -> dict | None:
    """Частная настройка человека или `None`, если он её не задавал.

    `None` — это «не настроено», а не «значения по умолчанию»: именно по нему
    `resolve_settings` решает, применять ли общую настройку.
    """
    row = conn.execute(
        "select kinds_muted, quiet_from, quiet_to, digest_enabled from notification_pref "
        "where participant_id = %s", (participant_id,)).fetchone()
    if row is None:
        return None
    return {
        "kinds_muted": list(row["kinds_muted"] or []),
        "quiet_from": row["quiet_from"].strftime("%H:%M"),
        "quiet_to": row["quiet_to"].strftime("%H:%M"),
        "digest_enabled": row["digest_enabled"],
    }


def save_personal(conn, participant_id: int, muted: list[str],
                  quiet_from: str, quiet_to: str) -> None:
    """Записать частную настройку целиком: частичной записи нет (FR-G3, О3)."""
    conn.execute(
        """insert into notification_pref
               (participant_id, kinds_muted, quiet_from, quiet_to, updated_at)
           values (%s, %s, %s::time, %s::time, now())
           on conflict (participant_id) do update
              set kinds_muted = excluded.kinds_muted,
                  quiet_from = excluded.quiet_from,
                  quiet_to = excluded.quiet_to,
                  updated_at = now()""",
        (participant_id, muted, quiet_from, quiet_to))
