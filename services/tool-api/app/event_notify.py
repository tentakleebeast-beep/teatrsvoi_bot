"""Уведомления об отмене и переносе события — один текст и один дедуп на два входа.

Зачем (V-133, `docs/reports/194`). Отмена и перенос доходят до людей двумя путями:
бот (`api/events.py`, отмена/перенос человеком в Mini App) и сверка с Google
(`calendar_sync.py`, организатор отменил или передвинул событие руками). Если каждый
путь соберёт свой текст и свой ключ дедупа, человек получит два разных уведомления об
одном событии либо ни одного: `events_cancel` пишет `event:<id>:cancelled`, и сверка
обязана писать тот же ключ, иначе двойная отмена пройдёт двумя рассылками.

Поэтому текст, payload и ключ дедупа живут здесь, а оба входа только зовут:

* `notify_cancelled(conn, plan, moment, reason)` — kind `event_cancelled`,
  дедуп `event:<id>:cancelled`;
* `notify_moved(conn, plan, old_start, start, to_hour, reason)` — kind `event_moved`,
  дедуп `event:<id>:moved:<новый start>`.

Получатели — участники события и создатель (`participants` + `created_by`). У людского
события без маркера `svoi_event_id` строки в `event_plan` нет, значит и получателей нет:
сверка такие события не уведомляет (`docs/development/186` §5).
"""

from __future__ import annotations

import json
from datetime import datetime

from app import core

#: Коды уведомлений — те же, что в `docs/development/57` (таблица маршрутов).
KIND_CANCELLED = "event_cancelled"
KIND_MOVED = "event_moved"

#: Заголовки, которые читает `scripts/send_notifications.py` (`payload.title`).
TITLE_CANCELLED = "Событие отменено"
TITLE_MOVED = "Событие перенесено"


def recipients(plan: dict) -> list[int]:
    """Кому слать: участники плюс создатель, без повторов, по возрастанию id."""
    ids = {int(pid) for pid in (plan.get("participant_ids") or []) if pid is not None}
    if plan.get("created_by"):
        ids.add(int(plan["created_by"]))
    return sorted(ids)


def dedupe_cancelled(plan_id: int) -> str:
    """Ключ дедупа отмены: `events_cancel` и сверка пишут один и тот же."""
    return f"event:{plan_id}:cancelled"


def dedupe_moved(plan_id: int, start: datetime) -> str:
    """Ключ дедупа переноса: новый `start`, поэтому второй перенос — новое уведомление."""
    return f"event:{plan_id}:moved:{start.isoformat()}"


def cancelled_payload(plan: dict, moment: datetime, reason: str | None,
                      title_ru: str) -> dict:
    """Payload отмены: дата и час события в зоне сервиса, причина — если есть.

    `title_ru` приходит от входа: название типа живёт в таблице `calendar_event_type`
    (`event_types.titles`), а этот модуль не открывает соединение ради подписи.
    """
    local = moment.astimezone(core.TZ)
    body = (f"{title_ru} «{plan.get('title')}» {local.strftime('%d.%m.%Y %H:%M')} "
            "отменяется.")
    if reason:
        body += f" Причина: {reason}"
    return {"event_id": plan.get("id"), "title": TITLE_CANCELLED, "body": body,
            "kind": plan.get("kind"), "date": local.date().isoformat(),
            "from_hour": local.hour, "reason": reason}


def moved_payload(plan: dict, old_start: datetime, start: datetime, to_hour: int,
                  reason: str | None, title_ru: str) -> dict:
    """Payload переноса: «было → стало», обе стороны в зоне сервиса."""
    was = old_start.astimezone(core.TZ)
    new = start.astimezone(core.TZ)
    body = (f"{title_ru} «{plan.get('title')}» переносится: было "
            f"{was.strftime('%d.%m.%Y %H:%M')}, стало {new.strftime('%d.%m.%Y %H:%M')}.")
    if reason:
        body += f" Причина: {reason}"
    return {"event_id": plan.get("id"), "title": TITLE_MOVED, "body": body,
            "kind": plan.get("kind"), "date": new.date().isoformat(),
            "from_hour": new.hour, "to_hour": to_hour,
            "was": was.isoformat(), "now": new.isoformat(), "reason": reason}


def queue(conn, kind: str, recipients_ids: list[int], payload: dict,
          dedupe_key: str) -> int:
    """Положить уведомление в очередь; повтор с тем же ключом и адресатом — молча.

    Возвращает число реально поставленных строк: `notified` в ответе API и в отчёте
    сверки — это они, а не длина списка получателей.
    """
    text = json.dumps(payload, ensure_ascii=False)
    sent = 0
    for pid in recipients_ids:
        inserted = conn.execute("""
            insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
            values (%s, %s, %s, %s, now())
            on conflict (dedupe_key, recipient_id) do nothing
            returning id""", (pid, kind, text, dedupe_key)).fetchone()
        sent += 1 if inserted else 0
    return sent


def notify_cancelled(conn, plan: dict, moment: datetime, reason: str | None,
                     title_ru: str) -> int:
    """Отмена: участникам и создателю, kind `event_cancelled`, дедуп по событию."""
    return queue(conn, KIND_CANCELLED, recipients(plan),
                 cancelled_payload(plan, moment, reason, title_ru),
                 dedupe_cancelled(plan["id"]))


def notify_moved(conn, plan: dict, old_start: datetime, start: datetime, to_hour: int,
                 reason: str | None, title_ru: str) -> int:
    """Перенос: участникам и создателю, kind `event_moved`, дедуп по новому времени."""
    return queue(conn, KIND_MOVED, recipients(plan),
                 moved_payload(plan, old_start, start, to_hour, reason, title_ru),
                 dedupe_moved(plan["id"], start))
