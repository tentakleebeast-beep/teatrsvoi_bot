"""Деньги проекта: событие — источник дохода, операция — производная (E6-2).

Единственное место, где событие превращается в операцию бюджета. Ручного
помесячного ввода аренды нет: у доходного события есть `revenue_amount`, и оно
порождает ровно одну строку `budget_tx` с `event_id` и `origin='event'`
(docs/development/196 §7).

Правила, которые держит этот модуль:

* одна операция на событие — повторный вызов возвращает существующую строку,
  а не ошибку и не вторую операцию (идемпотентность, §7.1);
* блок и статья выводятся из **типа события**, а не из `tx_type`: аренда зала —
  `sublease` / «Аренда (доход)», показ арендатора — `show_income` / «Продажа
  билетов» (§7.2–7.3);
* перенос в закрытый месяц отклоняется `409 period_closed`, событие не двигается
  (§7.5), отказ пишется в аудит `budget.period.refuse`;
* отмена события переводит операцию в `reversed`, строка остаётся — удаление
  запрещено (`docs/architecture/11` §5);
* I12: производная операция принадлежит проекту `kind='rental'`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import status

from . import core
from .category_alias import LEASE_INCOME, canonical
from .errors import ApiError

#: Статья показа арендатора: билеты, а не аренда зала.
SHOW_TICKETS = "Продажа билетов"

PERIOD_CLOSED_TEXT = "Месяц закрыт — перенос в него запрещён"


def _period_status(conn, period: str) -> str | None:
    """Статус периода бюджета: `open`, `closed` или None, если строки ещё нет."""
    row = conn.execute("select status from budget_period where period = %s",
                       (period,)).fetchone()
    return row["status"] if row else None


def ensure_period_open(conn, period: str, who: dict | None, action: str) -> None:
    """Отказ на запись в закрытый период: `409 period_closed` и запись в аудит.

    Проверка одна на два пути — перенос события с суммой и план проекта (§7.5).
    """
    if _period_status(conn, period) != "closed":
        return
    core.audit(conn, "human", (who or {}).get("id"), "budget.period.refuse",
               "budget_period", period, after={"action": action, "period": period})
    raise ApiError(status.HTTP_409_CONFLICT, PERIOD_CLOSED_TEXT, code="period_closed")


def derived_fields(event_kind: str, project_name: str, day) -> dict:
    """Блок, статья и назначение производной операции по типу события."""
    if event_kind == "rental":
        return {"block": "sublease", "category": LEASE_INCOME,
                "purpose": f"Аренда — {project_name} — {day:%d.%m.%Y}"}
    return {"block": "show_income", "category": SHOW_TICKETS,
            "purpose": f"Показ — {project_name} — {day:%d.%m.%Y}"}


def _category_id(conn, name: str, direction: str = "income") -> int:
    """ID статьи по каноническому имени; недостающую статью заводим."""
    name = canonical(name)
    row = conn.execute("select id from category where name = %s", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "insert into category (name, direction) values (%s, %s) returning id",
        (name, direction)).fetchone()["id"]


def _event(conn, event_id: int):
    return conn.execute(
        """select e.*, p.name as project_name, p.kind as project_kind,
                  p.status as project_status
             from event_plan e left join project p on p.id = e.project_id
            where e.id = %s""", (event_id,)).fetchone()


def _tx_for_event(conn, event_id: int):
    return conn.execute("select * from budget_tx where event_id = %s",
                        (event_id,)).fetchone()


def create_for_event(conn, event_id: int, who: dict | None = None):
    """Создать производную операцию для доходного события.

    Возвращает существующую строку, если операция уже есть (повторная
    синхронизация не плодит вторую), и None, если у события нет суммы: такое
    событие доходным не считается.
    """
    event = _event(conn, event_id)
    if event is None:
        return None
    amount = event["revenue_amount"]
    if amount is None or float(amount) <= 0:
        return None
    if event["project_id"] is None:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY,
                       "У доходного события нет проекта", code="unknown_project")

    existed = _tx_for_event(conn, event_id)
    if existed is not None:
        return existed

    start = event["starts_at"].astimezone(core.TZ)
    day = start.date()
    period = day.strftime("%Y-%m")
    fields = derived_fields(event["kind"], event["project_name"] or "", day)
    category_id = _category_id(conn, fields["category"])

    conn.execute("insert into budget_period (period, opened_at) values (%s, %s) "
                 "on conflict (period) do nothing", (period, f"{period}-01"))

    created = conn.execute(
        """insert into budget_tx (period, block, tx_date, tx_type, amount, category_id,
               project_id, purpose, status, origin, event_id, entered_by, confirmed_by,
               confirmed_at, rev, is_plan)
           values (%s,%s,%s,'income',%s,%s,%s,%s,'confirmed','event',%s,%s,%s,now(),1,false)
           on conflict do nothing
           returning *""",
        (period, fields["block"], day, amount, category_id, event["project_id"],
         fields["purpose"], event_id, (who or {}).get("id"), (who or {}).get("id"))
    ).fetchone()
    if created is None:                       # параллельная вставка того же события
        created = _tx_for_event(conn, event_id)
    if created is not None:
        core.audit(conn, "human", (who or {}).get("id"), "project.event.income",
                   "budget_tx", str(created["id"]),
                   after={"event_id": event_id, "amount": float(amount),
                          "block": fields["block"], "period": period})
    return created


def update_amount(conn, event_id: int, amount, who: dict | None = None):
    """Правка суммы события: `amount` производной операции и `rev+1` (§7.1)."""
    tx = _tx_for_event(conn, event_id)
    if tx is None:
        return None
    before = {"amount": float(tx["amount"]), "rev": tx["rev"]}
    updated = conn.execute(
        "update budget_tx set amount = %s, rev = rev + 1, updated_at = now() "
        "where id = %s returning *", (amount, tx["id"])).fetchone()
    core.audit(conn, "human", (who or {}).get("id"), "project.event.amount",
               "budget_tx", str(tx["id"]), before=before,
               after={"amount": float(amount), "rev": tx["rev"] + 1})
    return updated


def move_date(conn, event_id: int, new_start: datetime, who: dict | None = None,
              action: str = "event.move"):
    """Перенос: дата и период операции следуют за событием; закрытый месяц — отказ."""
    tx = _tx_for_event(conn, event_id)
    if tx is None:
        return None
    day = new_start.astimezone(core.TZ).date()
    period = day.strftime("%Y-%m")
    ensure_period_open(conn, period, who, action)
    conn.execute("insert into budget_period (period, opened_at) values (%s, %s) "
                 "on conflict (period) do nothing", (period, f"{period}-01"))
    before = {"tx_date": str(tx["tx_date"]), "period": tx["period"], "rev": tx["rev"]}
    updated = conn.execute(
        "update budget_tx set tx_date = %s, period = %s, rev = rev + 1, "
        "updated_at = now() where id = %s returning *",
        (day, period, tx["id"])).fetchone()
    core.audit(conn, "human", (who or {}).get("id"), "project.event.move",
               "budget_tx", str(tx["id"]), before=before,
               after={"tx_date": str(day), "period": period, "rev": tx["rev"] + 1})
    return updated


def cancel(conn, event_id: int, who: dict | None = None):
    """Отмена события: операция становится `reversed`, строка остаётся (§7.1)."""
    tx = _tx_for_event(conn, event_id)
    if tx is None:
        return None
    if tx["status"] == "reversed":
        return tx
    updated = conn.execute(
        "update budget_tx set status = 'reversed', rev = rev + 1, updated_at = now() "
        "where id = %s returning *", (tx["id"],)).fetchone()
    core.audit(conn, "human", (who or {}).get("id"), "project.event.cancel",
               "budget_tx", str(tx["id"]), before={"status": tx["status"]},
               after={"status": "reversed"})
    return updated
