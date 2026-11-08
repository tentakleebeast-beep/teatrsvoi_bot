"""Одноразовые токены подтверждения предложений агента (A-3, docs/development/108 §8).

Агент не пишет в базу: он возвращает намерение `{tool, args, human_text}`. Между
«модель предложила» и «деньги учтены» стоит карточка с кнопкой, а между кнопкой и
записью — этот модуль:

  * `create` кладёт предложение в `agent_proposal` и выдаёт одноразовый токен
    (5 минут, привязан к чату, человеку и уже проверенным аргументам);
  * `claim` атомарно забирает токен под подтверждение: `update ... where status =
    'pending'` — два одновременных нажатия не создадут две записи, второе получит
    `token_used`;
  * `cancel` фиксирует отказ человека — в базу бюджета при этом ничего не идёт.

Запись в бюджет делает не этот модуль, а обычный `/tools/budget/commit`: токен —
только разрешение на вызов проверенного эндпоинта, а не второй путь записи.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from .. import core
from . import registry

#: Сколько живёт предложение. Пять минут — из карты A-3: дольше живёт устаревшая
#: карточка, короче — человек не успевает прочитать сумму и дату.
TTL = timedelta(minutes=5)

#: Инструмент агента, которым создаётся предложение, для каждого вида действия.
#: Виды действий — только те, у которых есть детерминированный эндпоинт записи:
#: бюджет (`/tools/budget/commit`), событие (`/tools/events/assign`), заметка (`note`).
PROPOSE_TOOLS = {"budget.tx": "propose_budget_tx",
                 "event.assign": "propose_event",
                 "note.create": "propose_note"}

#: Экран Mini App для каждого вида нехватки данных (решение владельца 05.11.2026).
#: Одна константа рядом с предложениями: модель не придумывает адрес, а называет
#: вид данных; адрес берётся отсюда.
OPEN_SCREENS = {
    "event": "/webapp/assign",
    "availability": "/webapp/week",
    "note": "/webapp/notes",
    "budget": "/webapp/tx",
    "files": "/webapp/files",
    "slots": "/webapp/slots",
    "people": "/webapp/people",
}

#: Текст входа в дом: что человек увидит вместо выдуманной даты или суммы.
OPEN_TEXT = "Заполните в приложении — я открою нужный экран"


def open_proposal(kind: str, text: str | None = None) -> dict:
    """«Вход в дом» вместо догадки: `{kind: open, screen, text}`.

    Неизвестный вид данных — общий экран файлов не подставляем: лучше открыть
    главный экран, чем соврать про конкретную форму.
    """
    key = str(kind or "").strip().lower()
    return {"kind": "open", "screen": OPEN_SCREENS.get(key, "/webapp/home"),
            "for": key or None, "text": (text or OPEN_TEXT)[:500]}


def _public(row: dict) -> dict:
    """Предложение в виде для ответа и карточки: без токена и служебных полей."""
    args = row.get("args") or {}
    if isinstance(args, str):
        args = json.loads(args)
    return {"id": row.get("id"), "tool": row.get("tool"), "args": args,
            "human_text": row.get("human_text") or "",
            "status": row.get("status"),
            "expires_at": row["expires_at"].isoformat(timespec="seconds")
                          if isinstance(row.get("expires_at"), datetime) else None}


def refusal(code: str, message: str, **extra: Any) -> dict:
    """Отказ подтверждения: код для ветвления, текст для человека."""
    return {"ok": False, "error": code, "message": message, **extra}


def create(conn, who: dict, chat_id: int, tool: str, args: dict | None,
           human_text: str = "") -> dict:
    """Проверить намерение инструментом предложения и выдать одноразовый токен.

    Аргументы проверяет тот же обработчик, что и в агентном шаге (категория из
    закрытого списка, сумма, дата не в будущем), поэтому в карточку и в токен
    попадают уже нормализованные данные, а не то, что прислал gateway.
    """
    agent_tool = PROPOSE_TOOLS.get(str(tool or ""))
    if agent_tool is None:
        return refusal("unknown_tool", f"действия «{tool}» в подтверждении нет")
    result = registry.execute(conn, who, agent_tool, args or {})
    if not result.get("ok"):
        # Ошибка инструмента (нет права, категория не из списка, сумма <= 0) —
        # это отказ, а не предложение: карточку показывать нечего.
        return refusal(str(result.get("error") or "proposal_failed"),
                       str(result.get("message") or "предложение не сложилось"),
                       allowed_categories=result.get("allowed_categories"))
    proposal = result["proposal"]
    token = secrets.token_urlsafe(24)
    row = conn.execute(
        """insert into agent_proposal
               (token, chat_id, participant_id, tool, args, human_text, expires_at)
           values (%s,%s,%s,%s,%s::jsonb,%s, now() + %s::interval)
           returning id, token, chat_id, participant_id, tool, args, human_text,
                     status, created_at, expires_at""",
        (token, int(chat_id or 0), int(who.get("id") or 0), proposal["tool"],
         json.dumps(proposal["args"], ensure_ascii=False),
         str(human_text or proposal.get("human_text") or "")[:1000],
         f"{int(TTL.total_seconds())} seconds")).fetchone()
    core.audit(conn, "bot", who.get("id"), "agent.propose", "agent_proposal",
               str(row["id"]),
               after={"tool": row["tool"], "args": proposal["args"],
                      "chat_id": int(chat_id or 0),
                      "expires_at": row["expires_at"].isoformat(timespec="seconds")})
    return {"ok": True, "token": row["token"], "ttl_seconds": int(TTL.total_seconds()),
            "proposal": _public(dict(row) | {"status": "pending"})}


def _load(conn, token: str) -> dict | None:
    return conn.execute(
        "select * from agent_proposal where token = %s", (str(token or ""),)).fetchone()


def _check_owner(row: dict, who: dict, chat_id: int) -> dict | None:
    """Токен привязан к чату и к человеку: чужая карточка ничего не подтверждает."""
    if int(row.get("chat_id") or 0) != int(chat_id or 0):
        return refusal("token_chat_mismatch",
                       "Это предложение из другого чата — подтвердить здесь нельзя.")
    if int(row.get("participant_id") or 0) != int(who.get("id") or 0):
        return refusal("token_foreign",
                       "Это предложение другого человека: подтверждает тот, кто просил.")
    return None


def _expire(conn, row: dict) -> None:
    conn.execute("update agent_proposal set status = 'expired', resolved_at = now()"
                 " where id = %s and status = 'pending'", (row["id"],))


def claim(conn, who: dict, token: str, chat_id: int) -> dict:
    """Атомарно забрать предложение под подтверждение (одно нажатие — одна запись)."""
    row = _load(conn, token)
    if row is None:
        return refusal("token_unknown",
                       "Такого предложения нет: попросите записать заново.")
    denied = _check_owner(row, who, chat_id)
    if denied:
        return denied
    if row["status"] == "confirmed":
        return refusal("token_used",
                       "Это предложение уже подтверждено — второй операции не будет.",
                       tx_id=(row.get("result") or {}).get("tx_id"))
    if row["status"] == "cancelled":
        return refusal("token_cancelled", "Предложение отменено — записывать нечего.")
    if row["status"] != "pending":
        return refusal("token_used", "Предложение уже закрыто.")
    if row["expires_at"] <= core.now():
        _expire(conn, row)
        return refusal("token_expired",
                       "Предложение устарело (прошло больше 5 минут) — попросите заново.")
    claimed = conn.execute(
        """update agent_proposal
              set status = 'confirmed', resolved_at = now(), confirmed_by = %s
            where id = %s and status = 'pending'
            returning id""",
        (int(who.get("id") or 0), row["id"])).fetchone()
    if claimed is None:
        # Параллельное нажатие успело раньше: запись всё равно будет одна.
        return refusal("token_used", "Это предложение уже подтверждено.")
    return {"ok": True, "proposal": _public(dict(row))}


def mark_written(conn, proposal_id: int, tx: dict, deduped: bool = False) -> None:
    """Запомнить, что именно записалось: повторное нажатие покажет ту же операцию."""
    payload = {"tx_id": tx.get("id"), "period": tx.get("period"),
               "amount": tx.get("amount"), "deduped": bool(deduped)}
    conn.execute("update agent_proposal set result = %s::jsonb where id = %s",
                 (json.dumps(payload, ensure_ascii=False), proposal_id))


def mark_failed(conn, proposal_id: int, error: str) -> None:
    """Запись не прошла: предложение закрыто, причина в журнале, дубля нет."""
    conn.execute("update agent_proposal set status = 'failed', error = %s,"
                 " resolved_at = now() where id = %s", (str(error)[:300], proposal_id))


def cancel(conn, who: dict, token: str, chat_id: int) -> dict:
    """Человек нажал «Отмена»: ничего не пишем, отказ — в журнал."""
    row = _load(conn, token)
    if row is None:
        return refusal("token_unknown", "Такого предложения нет.")
    denied = _check_owner(row, who, chat_id)
    if denied:
        return denied
    if row["status"] == "confirmed":
        return refusal("token_used",
                       "Операция уже записана — отменять её здесь нельзя.")
    note = "закрыто"
    if row["status"] == "pending":
        if row["expires_at"] <= core.now():
            _expire(conn, row)
            note = "истекло"
        else:
            conn.execute("update agent_proposal set status = 'cancelled',"
                         " resolved_at = now() where id = %s", (row["id"],))
            note = "отменено"
    core.audit(conn, "human", who.get("id"), "agent.cancel", "agent_proposal",
               str(row["id"]),
               after={"tool": row["tool"], "amount": (row.get("args") or {}).get("amount"),
                      "note": note, "chat_id": int(chat_id or 0)})
    return {"ok": True, "cancelled": True, "note": note,
            "proposal": _public(dict(row) | {"status": "cancelled"})}


def refuse_audit(conn, who: dict, decision: str, token: str, reason: str,
                 chat_id: int) -> None:
    """Отказ по токену — тоже событие: видно, что человек нажимал и почему не вышло."""
    core.audit(conn, "human", who.get("id"), f"agent.{decision}.refused",
               "agent_proposal", None,
               after={"reason": reason, "chat_id": int(chat_id or 0),
                      "token_hint": str(token or "")[:8]})
