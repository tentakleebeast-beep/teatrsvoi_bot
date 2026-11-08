#!/usr/bin/env python3
"""
Один путь алертов сторожа: очередь `notification`, а не прямая отправка (Ш-30, FR-H12).

`scripts/watchdog.sh` и `scripts/health_agent.py` раньше слали админу по-разному:
сторож — прямо в Telegram каждые 10 минут, агент — в очередь с тихими часами и лимитом
«3 в час». Здесь общая точка: алерт ставится в `notification` (`kind='health'`) с ключом
дедупликации «проблема + час», поэтому один и тот же сбой не повторяется ни между
запусками сторожа, ни между сторожем и health-агентом.

Прямая отправка (`send_telegram`) остаётся только аварийным резервом на случай, если
база недоступна: иначе алерт бы просто потерялся.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime


def normalize_problems(problems: list[str]) -> list[str]:
    """Строки проблем без пустых и повторов, порядок сохраняется."""
    seen: set[str] = set()
    result: list[str] = []
    for item in problems:
        text = str(item or "").strip().lstrip("·").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def fingerprint(problems: list[str]) -> str:
    """Устойчивый отпечаток набора проблем: порядок и повторы не важны."""
    text = "\n".join(sorted(normalize_problems(problems)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def dedupe_key(problems: list[str], moment: datetime | None = None,
               kind: str = "health") -> str:
    """Ключ «проблема + час»: в пределах часа один и тот же сбой — одна запись."""
    moment = moment or datetime.now(UTC)
    return f"{kind}:{moment.strftime('%Y%m%d%H')}:{fingerprint(problems)}"


def alert_body(title: str, problems: list[str]) -> str:
    lines = [f"{title}:"]
    lines += [f"· {item}" for item in normalize_problems(problems)]
    return "\n".join(lines)


def admin_ids(conn) -> list[int]:
    """Кому уходят аварийные алерты: действующие админы.

    `revoked_at is null` — роль отзывается пометкой строки (db/init/060), а не удалением:
    без фильтра отозванный админ продолжал бы получать алерты. Наследная колонка
    `participant.role` остаётся запасным путём, но если строку роли явно отозвали —
    она больше не считается.
    """
    rows = conn.execute("""
        select p.id from participant p
        where p.status = 'active' and p.tg_chat_id is not null
          and (exists (select 1 from participant_role r
                        where r.participant_id = p.id and r.role = 'admin'
                          and r.revoked_at is null)
               or (p.role = 'admin' and not exists (
                     select 1 from participant_role r
                      where r.participant_id = p.id and r.role = 'admin'
                        and r.revoked_at is not null)))""").fetchall()
    return [row[0] for row in rows]


def queue_admins(problems: list[str], title: str, moment: datetime | None = None,
                 db: str | None = None, body: str | None = None) -> int:
    """Ставит уведомление активным админам; возвращает число новых записей.

    Исключения пробрасываются: вызывающий решает, отправлять ли аварийный резерв.
    """
    import psycopg

    clean = normalize_problems(problems)
    if not clean:
        return 0
    moment = moment or datetime.now(UTC)
    if not db:
        # Своей строки подключения у модуля нет намеренно: адрес базы знает
        # точка входа (`scripts/_cli.dsn`), а не библиотека алертов. Иначе
        # опечатка в умолчании молча уводила бы запись не в ту базу.
        raise RuntimeError("queue_admins: не передана строка подключения (db=...)")
    payload = json.dumps({"title": title, "body": body or alert_body(title, clean)},
                         ensure_ascii=False)
    key = dedupe_key(clean, moment)
    queued = 0
    with psycopg.connect(db) as conn:
        for pid in admin_ids(conn):
            row = conn.execute("""
                insert into notification (recipient_id, kind, payload, dedupe_key, scheduled_at)
                values (%s, 'health', %s, %s, now())
                on conflict (dedupe_key, recipient_id) do nothing
                returning id""", (pid, payload, key)).fetchone()
            queued += 1 if row else 0
        conn.commit()
    return queued


def send_telegram(text: str) -> bool:
    """Аварийный резерв: прямая отправка админу, если очередь недоступна."""
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat = os.getenv("ADMIN_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:                                # noqa: BLE001 — резерв не должен падать
        return False
