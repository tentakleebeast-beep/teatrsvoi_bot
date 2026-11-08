"""Финансы: взносы, тарифы, источники."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from services.shared.dues import dues_lines, month_title

from .. import core

router = APIRouter()

@router.get("/webapp/my/dues")
def webapp_my_dues(who: dict = Depends(core.webapp_actor)) -> dict:
    """То же, что /tools/dues/me, но по токену Mini App: человек видит свои взносы."""
    return dues_me(who)



@router.get("/tools/dues/me")
def dues_me(who: dict = Depends(core.require("budget.read.self"))) -> dict:
    """Свои начисления и свой тариф: человек видит, из чего складывается сумма."""
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select period, amount_due, amount_paid, paid_on, status
            from dues_charge where participant_id = %s order by period desc limit 12""",
            (who["id"],)).fetchall()
        person = conn.execute("select dues_tariff, dues_tariff_from from participant "
                              "where id = %s", (who["id"],)).fetchone()
    today = datetime.now(core.TZ).date()
    # Человеку нужен не реестр начислений, а ответ «за этот месяц учтено или нет».
    period = today.strftime("%Y-%m")
    month = next((row for row in rows if str(row["period"])[:7] == period), None)
    current = ({
        "period": period,
        "due": float(month["amount_due"] or 0),
        "paid": float(month["amount_paid"] or 0),
        "settled": float(month["amount_paid"] or 0) >= float(month["amount_due"] or 0),
        "charged": True,
    } if month else {"period": period, "due": 0.0, "paid": 0.0,
                     "settled": False, "charged": False})
    debt = sum(max(0.0, float(row["amount_due"] or 0) - float(row["amount_paid"] or 0))
               for row in rows if str(row["period"])[:7] != period)
    payload = {"ok": True, "data": rows, "current": current, "debt": debt,
               "tariff": core._tariff_state(person, today.replace(day=1)),
               "next": core._tariff_state(person, core._next_month_start(today))}
    # Один текст и один период для чата и экрана (FR-B7, Д12): строки строит общая
    # функция `services/shared/dues.py`, чат печатает их как есть, экран — тоже.
    payload["summary"] = {"period": period, "title": month_title(period),
                          "lines": dues_lines(payload)}
    return payload



class DuesAccrueIn(core.ApiIn):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
                        description="месяц начисления, YYYY-MM")



@router.post("/tools/dues/accrue")
def dues_accrue(body: DuesAccrueIn,
                who: dict = Depends(core.require("budget.write"))) -> dict:
    """Начисляет взносы за месяц по тарифу, действующему на начало месяца (FR-B7).

    Идемпотентно по `participant_id + period`: повторный вызов за тот же месяц
    не перезаписывает уже начисленное (`on conflict do nothing`), поэтому ручные
    правки суммы и оплаты не затираются. Новый тариф действует со следующего
    месяца — это правило выставляет тот, кто меняет тариф (Ш-07), а начисление
    просто берёт тариф, чей `dues_tariff_from` не позже первого дня периода.
    """
    month_start = datetime.strptime(body.period + "-01", "%Y-%m-%d").date()
    eligible = """
        from participant p
        where p.status = 'active'
          and p.dues_tariff is not null and p.dues_tariff > 0
          and (p.dues_tariff_from is null or p.dues_tariff_from <= %s)"""
    with core.pool.connection() as conn:
        candidates = conn.execute(f"select count(*) c {eligible}",
                                  (month_start,)).fetchone()["c"]
        created = conn.execute(f"""
            insert into dues_charge (participant_id, period, amount_due)
            select p.id, %s, p.dues_tariff {eligible}
            on conflict (participant_id, period) do nothing
            returning participant_id, amount_due""",
            (body.period, month_start)).fetchall()
        total = float(sum(float(row["amount_due"]) for row in created))
        core.audit(conn, "human", who.get("id"), "dues.accrue", "dues_charge", body.period,
              after={"period": body.period, "created": len(created),
                     "skipped": candidates - len(created), "amount_total": total})
    return {"ok": True, "period": body.period, "created": len(created),
            "skipped": candidates - len(created), "amount_total": total,
            "rows": [{"participant_id": row["participant_id"],
                      "amount_due": float(row["amount_due"])} for row in created]}



_PERIOD = r"^\d{4}-(0[1-9]|1[0-2])$"


def _period_or_current(period: str | None) -> str:
    """Месяц раздела «Взносы»: явный, иначе текущий по часам сервиса."""
    return period or datetime.now(core.TZ).date().strftime("%Y-%m")


@router.get("/webapp/admin/dues")
def webapp_admin_dues(period: str | None = Query(default=None, pattern=_PERIOD),
                      who: dict = Depends(core.webapp_actor)) -> dict:
    """Раздел «Взносы»: тарифы людей и начисления за месяц (`budget.write`, Д6/Д8).

    Право то же, что у путей записи (`POST /tools/dues/accrue`,
    `POST /tools/participants/{id}/dues-tariff`): организатор видит тариф и
    начисление без `participants.manage` и без входа в админ-панель бота.
    """
    core._webapp_require(who, "budget.write")
    month = _period_or_current(period)
    month_start = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select p.id, p.display_name, p.status, p.dues_tariff, p.dues_tariff_from,
                   c.amount_due, c.amount_paid, (c.participant_id is not null) as charged
            from participant p
            left join dues_charge c
              on c.participant_id = p.id and c.period = %s
            where p.status = 'active'
            order by p.display_name""", (month,)).fetchall()
        totals_by_period = conn.execute("""
            select period, count(*) as charged, sum(amount_due) as due,
                   sum(amount_paid) as paid
            from dues_charge group by period order by period desc limit 12""").fetchall()

    data = [{"id": row["id"], "display_name": row["display_name"],
             "status": row["status"],
             "tariff": (float(row["dues_tariff"])
                        if row["dues_tariff"] is not None else None),
             "tariff_from": (row["dues_tariff_from"].isoformat()
                             if row["dues_tariff_from"] else None),
             "amount_due": float(row["amount_due"] or 0),
             "amount_paid": float(row["amount_paid"] or 0),
             "charged": bool(row["charged"]),
             # Тариф действует на начало месяца — то же правило, что у начисления.
             "tariff_applies": (row["dues_tariff"] is not None
                                and float(row["dues_tariff"]) > 0
                                and (row["dues_tariff_from"] is None
                                     or row["dues_tariff_from"] <= month_start))}
            for row in rows]
    periods = {row["period"]: {"period": row["period"],
                               "title": month_title(row["period"]),
                               "charged": row["charged"],
                               "due": float(row["due"] or 0),
                               "paid": float(row["paid"] or 0)}
               for row in totals_by_period}
    periods.setdefault(month, {"period": month, "title": month_title(month),
                               "charged": 0, "due": 0.0, "paid": 0.0})
    due_total = sum(item["amount_due"] for item in data)
    paid_total = sum(item["amount_paid"] for item in data)
    return {"ok": True, "period": month, "period_title": month_title(month),
            "totals": {"people": len(data),
                       "charged": sum(1 for item in data if item["charged"]),
                       "due": due_total, "paid": paid_total},
            "periods": [periods[key] for key in sorted(periods, reverse=True)],
            "data": data}



class DuesAccrueWebIn(core.ApiIn):
    period: str = Field(pattern=_PERIOD, description="месяц начисления, YYYY-MM")


@router.post("/webapp/admin/dues/accrue")
def webapp_admin_dues_accrue(body: DuesAccrueWebIn,
                             who: dict = Depends(core.webapp_actor)) -> dict:
    """Кнопка «Начислить за месяц» из Mini App: тот же обработчик, что и у пути-канона."""
    viewer = core._webapp_require(who, "budget.write")
    return dues_accrue(DuesAccrueIn(period=body.period), who=viewer)



class GranularityIn(core.ApiIn):
    section: Literal["dues", "expense", "show_income", "show_cost", "sublease"]
    mode: Literal["per_operation", "per_source", "per_month"]
    note: str | None = Field(default=None, max_length=300)



@router.get("/tools/finance/granularity")
def finance_granularity(who: dict = Depends(core.require("budget.read"))) -> dict:
    """Как писать операции в человеческий лист: по операции, по источнику или за месяц."""
    with core.pool.connection() as conn:
        rows = conn.execute("select section, mode, note from finance_granularity "
                            "order by section").fetchall()
    return {"ok": True, "data": [dict(row) for row in rows]}



@router.post("/tools/finance/granularity")
def finance_granularity_set(body: GranularityIn,
                            who: dict = Depends(core.require("budget.import"))) -> dict:
    with core.pool.connection() as conn:
        conn.execute("""insert into finance_granularity (section, mode, note)
                        values (%s, %s, %s)
                        on conflict (section) do update
                          set mode = excluded.mode, note = excluded.note""",
                     (body.section, body.mode, body.note))
        core.audit(conn, "human", who["id"], "finance.granularity", "finance_granularity",
              body.section, after={"mode": body.mode})
    return {"ok": True, "section": body.section, "mode": body.mode}



class SourceMapIn(core.ApiIn):
    section: Literal["dues", "expense", "show_income", "show_cost", "sublease"]
    label: str = Field(min_length=1, max_length=120)
    category_id: int | None = None
    project_id: int | None = None
    note: str | None = Field(default=None, max_length=300)



@router.get("/tools/finance/sources")
def finance_sources(section: str | None = None,
                    who: dict = Depends(core.require("budget.read"))) -> dict:
    """Карта «как написано в листе → статья и проект в базе»."""
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select m.id, m.section, m.label, m.category_id, m.project_id, m.note,
                   c.name as category, pr.name as project
            from finance_source_map m
            left join category c on c.id = m.category_id
            left join project pr on pr.id = m.project_id
            where (%s::text is null or m.section = %s::text)
            order by m.section, m.label""", (section, section)).fetchall()
    return {"ok": True, "data": [dict(row) for row in rows]}



@router.post("/tools/finance/sources")
def finance_source_add(body: SourceMapIn,
                       who: dict = Depends(core.require("budget.import"))) -> dict:
    with core.pool.connection() as conn:
        row = conn.execute("""
            insert into finance_source_map (section, label, category_id, project_id, note)
            values (%s, %s, %s, %s, %s)
            on conflict (section, lower(label)) do update
              set category_id = excluded.category_id, project_id = excluded.project_id,
                  note = excluded.note
            returning id""",
            (body.section, body.label.strip(), body.category_id, body.project_id,
             body.note)).fetchone()
        core.audit(conn, "human", who["id"], "finance.source_map", "finance_source_map",
              str(row["id"]), after={"section": body.section, "label": body.label})
    return {"ok": True, "id": row["id"]}



@router.delete("/tools/finance/sources/{source_id}")
def finance_source_delete(source_id: int,
                          who: dict = Depends(core.require("budget.import"))) -> dict:
    with core.pool.connection() as conn:
        conn.execute("delete from finance_source_map where id = %s", (source_id,))
        core.audit(conn, "human", who["id"], "finance.source_unmap", "finance_source_map",
              str(source_id))
    return {"ok": True}

