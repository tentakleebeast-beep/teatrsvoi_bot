"""Приглашение — push через очередь, шаг диалога — нет (AC-13, §14.6).

Тихие часы и лимит 3/час относятся к неожиданным сообщениям, а не к ответам на
действие человека (D5). Здесь проверяется граница между двумя путями:

* `POST /webapp/polls/start` кладёт приглашения в `notification` (`kind='poll'`);
* роутер чата (`routers/polls.py`) в очередь не пишет вовсе — шаг уходит ответом
  на нажатие, иначе анкета из 11 вопросов растягивается на четыре часа.

Обоснование: FR-G18, AC-13, AC-4, `docs/development/162-bot-polls.md` §14.6.
"""

from __future__ import annotations

import pathlib

import send_notifications

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATEWAY_POLLS = ROOT / "services" / "tg-gateway" / "app" / "routers" / "polls.py"
API_POLLS = ROOT / "services" / "tool-api" / "app" / "api" / "polls.py"


def test_приглашение_ложится_в_очередь_видом_poll():
    source = API_POLLS.read_text(encoding="utf-8")
    assert "'poll'" in source and "insert into notification" in source
    assert "payload" in source and "markup" in source


def test_шаг_диалога_в_очередь_не_пишет():
    """Ни одной вставки в `notification` в роутере чата: это ответ на действие."""
    source = GATEWAY_POLLS.read_text(encoding="utf-8")
    assert "insert into notification" not in source
    assert "from notification" not in source


def test_тихие_часы_действуют_на_любую_запись_очереди():
    """`deliver` не различает вид: значит и `poll`, и `poll_reminder` переносятся утром."""
    source = (ROOT / "scripts" / "send_notifications.py").read_text(encoding="utf-8")
    assert "in_quiet_hours(moment" in source
    quiet = send_notifications.parse_hhmm("20:00")
    assert send_notifications.in_quiet_hours(
        __import__("datetime").datetime(2026, 11, 15, 21, 0), quiet,
        send_notifications.parse_hhmm("08:00")) is True


def test_лимит_три_в_час_общий_для_очереди():
    source = (ROOT / "scripts" / "send_notifications.py").read_text(encoding="utf-8")
    assert "sent_last_hour" in source and "max_per_hour" in source
    assert send_notifications.MAX_PER_HOUR == 3
