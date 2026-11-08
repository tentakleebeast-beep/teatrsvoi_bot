#!/usr/bin/env python3
"""
Отправка уведомлений из таблицы `notification` (docs/infrastructure/36).

  python scripts/send_notifications.py             # показать, что готово к отправке
  python scripts/send_notifications.py --apply     # отправить в Telegram
  python scripts/send_notifications.py --limit 20
  python scripts/send_notifications.py --retry-failed --apply   # разобрать failed

Правила:
  * тихие часы 20:00–08:00: сначала частная настройка человека (`notification_pref`),
    если её нет — общая по студии (`app_setting`, задаётся в админ-панели, FR-G17),
    если нет и её — значения по умолчанию из кода; не отправляем, переносим на утро;
  * не больше NOTIFY_MAX_PER_HOUR сообщений в час одному человеку — остальные переносятся на час;
  * запись, прождавшая дольше NOTIFY_STALE_HOURS (по умолчанию 24 ч), не отправляется:
    после простоя задания старые сообщения теряют смысл;
  * после 3 неудачных попыток запись помечается failed и больше не берётся;
  * записи failed разбирает --retry-failed: свежая ошибка настройки
    возвращается в очередь, остальные помечаются skipped с причиной (Ш-35);
  * отправленное помечается sent.
"""

from __future__ import annotations

import argparse
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import psycopg
from _cli import add_tool_api_path, add_write_mode, apply_env, dsn, env, now_local, parser, writes
from psycopg.rows import dict_row

apply_env()
add_tool_api_path()

from app.notification_store import load_defaults  # noqa: E402
from app.notifications import (  # noqa: E402
    MAX_ATTEMPTS,
    in_quiet_hours,
    is_stale,
    next_allowed,
    parse_hhmm,
    resolve_settings,
)

DB = dsn()
TOKEN = env("TELEGRAM_TOKEN")
TZ_OFFSET = float(env("TZ_OFFSET_HOURS", "4"))
MAX_PER_HOUR = int(env("NOTIFY_MAX_PER_HOUR", "3"))
# Сколько часов запись может ждать отправки: завалившееся задание не должно
# разом выплёскивать всю накопившуюся очередь, старые сообщения уже неактуальны.
STALE_HOURS = float(env("NOTIFY_STALE_HOURS", "24"))

# Записи failed после устранения причины в очередь сами не возвращаются: attempts
# уже исчерпан. Повторять имеет смысл только ошибку настройки — причина была
# снаружи сообщения и уже устранена (Ш-35).
RETRY_MAX_AGE_HOURS = float(env("NOTIFY_RETRY_MAX_AGE_HOURS", "24"))
CONFIG_ERROR_MARKERS = ("TELEGRAM_TOKEN не задан", "HTTP 401", "HTTP 404",
                        "Unauthorized", "Not Found")


def is_config_error(last_error: str | None) -> bool:
    """Ошибка настройки, а не самого сообщения: нет токена или бот его не принял."""
    text = (last_error or "").strip().lower()
    return any(marker.lower() in text for marker in CONFIG_ERROR_MARKERS)


def send(chat_id: int, text: str) -> tuple[bool, str]:
    """Прямой вызов Bot API: рассылка не должна зависеть от polling-процесса."""
    if not TOKEN:
        return False, "TELEGRAM_TOKEN не задан"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return (resp.status == 200, "" if resp.status == 200 else f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:180]}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, str(exc)[:180]


def sent_last_hour(conn, recipient_id: int | None, chat_id: int | None,
                   moment: datetime) -> int:
    """
    Сколько сообщений уже ушло этому человеку за последний час.

    Считаем прежде всего по `recipient_id`: адресат есть у каждой записи очереди.
    Раньше считали только по `payload->>'chat_id'`, а у уведомлений об обращениях
    этого ключа нет — счётчик всегда был 0 и за один прогон уходило 27 сообщений
    двум людям вместо трёх (Ш-34). `chat_id` оставлен как второй признак: у старых
    записей `recipient_id` может быть пустым.
    """
    row = conn.execute("""
        select count(*) as cnt
        from notification n
        left join participant p on p.id = n.recipient_id
        where n.status = 'sent' and n.sent_at > %s
          and (n.recipient_id = %s
               or p.tg_chat_id = %s
               or n.payload->>'chat_id' = %s)""",
        (moment - timedelta(hours=1), recipient_id, chat_id,
         str(chat_id) if chat_id is not None else None)).fetchone()
    return int(row["cnt"])


def message_text(item: dict) -> str:
    payload = item["payload"] or {}
    text = payload.get("title") or "Напоминание"
    if payload.get("body"):
        text += "\n\n" + payload["body"]
    return text


def deliver(conn, moment: datetime, *, apply_changes: bool = True, limit: int = 50,
            max_per_hour: int = MAX_PER_HOUR, stale_hours: float = STALE_HOURS,
            sender=None, out=print) -> dict:
    """
    Разбирает очередь на момент `moment`. Возвращает счётчики.

    `sender` и `out` подменяются в тестах: правила лимита и тихих часов проверяются
    на настоящей БД, а Telegram не дёргается.
    """
    sender = sender or send
    ready = conn.execute("""
        select n.*, p.tg_chat_id, p.display_name,
               prefs.participant_id as pref_id, prefs.quiet_from, prefs.quiet_to
        from notification n
        join participant p on p.id = n.recipient_id
        left join notification_pref prefs on prefs.participant_id = p.id
        where n.status = 'scheduled' and n.scheduled_at <= %s
          and p.status = 'active' and p.tg_chat_id is not null
        order by n.scheduled_at limit %s""", (moment, limit)).fetchall()

    # Общие тихие часы студии читаем один раз на прогон: они одни на всех (FR-G17).
    defaults = load_defaults(conn)

    out(f"сейчас: {moment:%d.%m.%Y %H:%M} (UTC+{int(TZ_OFFSET)})")
    out(f"готово к отправке: {len(ready)}")

    sent = postponed = failed = stale = 0
    for item in ready:
        # Частная настройка важнее общей; нет частной — действуют общие тихие часы,
        # нет и их — значения по умолчанию из кода (FR-G17, П-8).
        personal = ({"quiet_from": item["quiet_from"], "quiet_to": item["quiet_to"]}
                    if item["pref_id"] is not None else None)
        quiet = resolve_settings(personal, defaults)
        quiet_from = parse_hhmm(quiet["quiet_from"])
        quiet_to = parse_hhmm(quiet["quiet_to"])

        age = moment - item["scheduled_at"]
        if is_stale(moment, item["scheduled_at"], stale_hours):
            if apply_changes:
                conn.execute("update notification set status='skipped', "
                             "last_error='просрочено: очередь не разбиралась "
                             f"больше {stale_hours:g} ч' where id=%s", (item["id"],))
            out(f"  {item['id']}: просрочено на {age.total_seconds() / 3600:.1f} ч — пропуск")
            stale += 1
            continue

        if in_quiet_hours(moment, quiet_from, quiet_to):
            wake = next_allowed(moment, quiet_from, quiet_to)
            if apply_changes:
                conn.execute("update notification set scheduled_at=%s where id=%s",
                             (wake, item["id"]))
            out(f"  {item['id']}: тихие часы, перенос на {wake:%d.%m %H:%M}")
            postponed += 1
            continue

        if sent_last_hour(conn, item["recipient_id"], item["tg_chat_id"],
                          moment) >= max_per_hour:
            # Лимит — не повод терять сообщение: переносим на час вперёд.
            later = moment + timedelta(hours=1)
            if apply_changes:
                conn.execute("update notification set scheduled_at=%s where id=%s",
                             (later, item["id"]))
            out(f"  {item['id']}: лимит {max_per_hour}/час — перенос на {later:%d.%m %H:%M}")
            postponed += 1
            continue

        text = message_text(item)

        if not apply_changes:
            out(f"  [dry-run] {item['display_name']}: {text.splitlines()[0]}")
            continue

        ok, error = sender(item["tg_chat_id"], text)
        if ok:
            conn.execute("update notification set status='sent', sent_at=%s, "
                         "attempts=attempts+1, last_error=null where id=%s",
                         (moment, item["id"]))
            sent += 1
        else:
            attempts = item["attempts"] + 1
            status = "failed" if attempts >= MAX_ATTEMPTS else "scheduled"
            conn.execute("update notification set attempts=%s, status=%s, last_error=%s "
                         "where id=%s", (attempts, status, error, item["id"]))
            failed += 1
            out(f"  {item['id']}: ошибка — {error}")

    if apply_changes:
        conn.commit()

    return {"ready": len(ready), "sent": sent, "failed": failed,
            "postponed": postponed, "stale": stale}


def retry_failed(conn, moment: datetime, *, apply_changes: bool = True,
                 max_age_hours: float = RETRY_MAX_AGE_HOURS, out=print) -> dict:
    """
    Разбирает записи failed, которые больше не берутся в отправку (Ш-35).

    * ошибка настройки и запись не старше max_age_hours — возвращаем в очередь
      (attempts = 0, scheduled_at = момент разбора), last_error оставляем как историю;
    * остальные failed — переводим в skipped с причиной: сообщение потеряло смысл
      или повторить его нельзя. Так очередь не выглядит сломанной.
    """
    rows = conn.execute(
        "select id, scheduled_at, last_error from notification "
        "where status = %s order by id", ("failed",)).fetchall()

    retried = skipped = 0
    for item in rows:
        note_id = item["id"]
        error = item["last_error"]
        age = moment - item["scheduled_at"]
        config = is_config_error(error)
        if config and age <= timedelta(hours=max_age_hours):
            if apply_changes:
                conn.execute(
                    "update notification set status = %s, attempts = 0, "
                    "scheduled_at = %s where id = %s",
                    ("scheduled", moment, note_id))
            out(f"  {note_id}: повтор после ошибки настройки: {error}")
            retried += 1
            continue

        if config:
            hours = age.total_seconds() / 3600
            reason = (f"ошибка настройки устранена, но запись ждала {hours:.1f} ч "
                      f"при пороге {max_age_hours:g} ч")
        else:
            detail = error or "без причины"
            reason = f"попытки исчерпаны: {detail}"
        if apply_changes:
            conn.execute("update notification set status = %s, last_error = %s "
                         "where id = %s", ("skipped", reason[:400], note_id))
        out(f"  {note_id}: skipped — {reason}")
        skipped += 1

    if apply_changes:
        conn.commit()
    return {"failed": len(rows), "retried": retried, "skipped": skipped}


def build_parser() -> argparse.ArgumentParser:
    """Аргументы запуска: по умолчанию ничего не отправляем."""
    ap = parser("Рассылка уведомлений из очереди")
    add_write_mode(ap)
    ap.add_argument("--limit", type=int, default=50, help="сколько записей взять за раз")
    ap.add_argument("--retry-failed", action="store_true",
                    help="вернуть в очередь неудачные, у которых прошла ошибка настройки")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    apply_changes = writes(args)
    limit = args.limit

    moment = now_local()
    with psycopg.connect(DB, row_factory=dict_row) as conn:
        if args.retry_failed:
            stats = retry_failed(conn, moment, apply_changes=apply_changes)
            print("неудачных: {}, возвращено в очередь: {}, помечено skipped: {}".format(
                stats["failed"], stats["retried"], stats["skipped"]))
        result = deliver(conn, moment, apply_changes=apply_changes, limit=limit)

    if apply_changes:
        print("\nотправлено {}, ошибок {}, перенесено {}, просрочено {}".format(
            result["sent"], result["failed"], result["postponed"], result["stale"]))
    else:
        print("\n(dry-run: ничего не отправлено)")


if __name__ == "__main__":
    main()
