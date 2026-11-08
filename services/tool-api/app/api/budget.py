"""Бюджет: операции, разбор текста, сводка, лист."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field, field_validator

from .. import core
from ..errors import ApiError
from ..external import DB_ERRORS, GOOGLE_API_ERRORS
from ..participant_names import category_hints
from ..rules import parse_budget_text

router = APIRouter()

class BudgetCommitIn(core.ApiIn):
    is_plan: bool = False      # план: входит в бюджет, но не в баланс (миграция 045)
    chat_id: int = 0           # в Mini App берём из сессии, а не из формы
    idempotency_key: str | None = None
    tx_type: str = "expense"
    amount: float

    category: str = "Прочее"
    tx_date: str | None = None            # YYYY-MM-DD
    purpose: str = ""
    project: str | None = None
    policy: str | None = None                  # rental | show | rehearsal | training
    counterparty: str | None = None
    source_text: str | None = None
    correlation_id: str | None = None


    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: object) -> object:
        """«1 000», «500,50», «500 ₽» — это числа, а не повод для 422.

        Сумму вводят руками в Mini App и в чате: браузер на iOS отдаёт запятую,
        люди ставят пробелы между разрядами. Запись при этом молча не уходила.
        """
        if isinstance(value, str):
            cleaned = re.sub(r"[\s\u00a0\u202f]", "", value).replace(",", ".")
            cleaned = cleaned.replace("₽", "").replace("руб", "").strip()
            return cleaned or value
        return value

    @field_validator("category", mode="before")
    @classmethod
    def _default_category(cls, value: object) -> object:
        """Пустая категория из формы — это «Прочее», а не операция без категории."""
        if value is None:
            return "Прочее"
        if isinstance(value, str) and not value.strip():
            return "Прочее"
        return value

    @field_validator("purpose", mode="before")
    @classmethod
    def _empty_purpose(cls, value: object) -> object:
        """Статья не обязательна: страница присылает null, если поле пустое."""
        return "" if value is None else value



def _ensure_category(conn, name: str, tx_type: str) -> int:
    """Статья должна соответствовать направлению операции.

    Раньше статья искалась только по имени: «Аренда помещения» молча принималась
    для дохода, и бюджет получал доход в расходной статье (нашёл UX-прогон 30.10.2026).
    """
    want = "income" if tx_type == "income" else "expense"
    row = conn.execute("select id, direction from category where name = %s", (name,)).fetchone()
    if row:
        if row["direction"] != want:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"category_direction: статья «{name}» относится к направлению "
                f"{'доход' if row['direction'] == 'income' else 'расход'}")
        return row["id"]
    return conn.execute(
        "insert into category (name, direction) values (%s,%s) returning id",
        (name, want)).fetchone()["id"]



def _ensure_project(conn, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("select id from project where name = %s", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("insert into project (name) values (%s) returning id",
                        (name,)).fetchone()["id"]



def _write_tx(conn, body: BudgetCommitIn, who: dict, key: str | None) -> dict | None:
    """Единственное место вставки операции бюджета: используется и при прямой записи,
    и при досылке формы. Второго подтверждения нет — крупная сумма идёт тем же путём.
    Возвращает None, если ключ уже использован."""
    tx_date = (datetime.strptime(body.tx_date, "%Y-%m-%d").date()
               if body.tx_date else datetime.now(core.TZ).date())
    period = tx_date.strftime("%Y-%m")
    block = {"expense": "expense", "income": "show_income"}.get(body.tx_type, "expense")

    cat_id = _ensure_category(conn, body.category, body.tx_type)
    proj_id = _ensure_project(conn, body.project)

    conn.execute("""insert into budget_period (period, opened_at)
                    values (%s, %s) on conflict (period) do nothing""",
                 (period, f"{period}-01"))

    created = conn.execute(
        """insert into budget_tx (period, block, tx_date, tx_type, amount, category_id,
               project_id, counterparty, participant_id, purpose, article_raw, status, origin,
               entered_by, source_chat_id, confirmed_by, confirmed_at, idempotency_key, is_plan)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed','bot',%s,%s,%s,now(),%s,%s)
           on conflict (idempotency_key) do nothing
           returning *""",
        (period, block, tx_date, body.tx_type, body.amount, cat_id, proj_id,
         body.counterparty, who.get("id") or None, body.purpose or body.source_text or "",
         body.source_text, who.get("id") or None, body.chat_id,
         who.get("id") or None, key or None, bool(body.is_plan))).fetchone()

    if created is None:
        return None

    core.audit(conn, "human", who.get("id"), "budget.tx.create", "budget_tx", str(created["id"]),
          after={"amount": float(body.amount), "category": body.category,
                 "type": body.tx_type, "period": period},
          correlation_id=body.correlation_id,
          source={"chat_id": body.chat_id, "text": body.source_text})
    return created



def _mark_sheet_state(tx_id: int, error: str | None = None) -> None:
    """Признак «лист подтвердил / не подтвердил» для сверки (C-3, db/init/033)."""
    try:
        with core.pool.connection() as conn:
            if error is None:
                conn.execute("update budget_tx set sheet_written_at = now(), "
                             "sheet_error = null where id = %s", (tx_id,))
            else:
                conn.execute("update budget_tx set sheet_written_at = null, "
                             "sheet_error = %s where id = %s", (error[:200], tx_id))
    except DB_ERRORS as exc:
        # признак вспомогательный: сбой отметки не должен отменять запись операции,
        # но и молчать нельзя — иначе сверка «файл → БД» тихо теряет признак
        core.log.warning("не удалось отметить sheet-состояние операции: %s", exc)



def _sheet_row(tx: dict) -> list:
    """Строка листа «Транзакции» — один формат и для первой записи, и для повтора (C-3).

    `tx` — словарь с id, tx_date, tx_type, block, amount; остальные поля необязательны.
    """
    tx_date = tx["tx_date"]
    if isinstance(tx_date, str):
        tx_date = datetime.strptime(tx_date, "%Y-%m-%d").date()
    block = tx.get("block") or {"expense": "expense", "income": "show_income"}.get(
        tx.get("tx_type"), "expense")
    return [
        str(tx["id"]), tx_date.strftime("%d.%m.%Y"),
        "доход" if tx.get("tx_type") == "income" else "расход",
        {"expense": "Расходы студии", "show_income": "Показы — доход"}.get(block, block),
        float(tx["amount"]), tx.get("category") or "", tx.get("project") or "",
        tx.get("counterparty") or "", tx.get("purpose") or tx.get("source_text") or "",
        "", "бот", f"telegram · chat {tx.get('chat_id')}", 1, "подтверждено",
    ]



def _append_sheet_row(row: list) -> str | None:
    """Дописать готовую строку в лист. Возвращает текст ошибки или None."""
    try:
        core._sheets().spreadsheets().values().append(
            spreadsheetId=core.BUDGET_SPREADSHEET_ID, range="Транзакции!A1",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": [row]}).execute()
    except GOOGLE_API_ERRORS as e:
        # адаптер листа: текст отказа уходит в budget_tx.sheet_error, запись в БД живёт
        return str(e)[:200]
    return None



def _sheet_ids() -> set[int]:
    """ID операций, уже стоящие в листе. Нужны, чтобы повтор не задвоил строку.

    Append мог пройти, а ответ — потеряться (таймаут): тогда в БД останется
    `sheet_error`, хотя строка в листе есть.
    """
    values = core._sheets().spreadsheets().values().get(
        spreadsheetId=core.BUDGET_SPREADSHEET_ID,
        range="Транзакции!A2:A5000").execute().get("values", [])
    ids: set[int] = set()
    for row in values:
        if row and str(row[0]).strip().isdigit():
            ids.add(int(str(row[0]).strip()))
    return ids



def _append_tx_sheet(body: BudgetCommitIn, created: dict) -> str | None:
    """Строка в лист «Транзакции». Best effort: сбой листа не отменяет запись в БД.

    Исход попытки фиксируется в budget_tx (sheet_written_at / sheet_error), чтобы
    сверка «файл → БД» отличала «человек удалил строку» от «строка не дошла до листа».
    """
    if not core.BUDGET_SPREADSHEET_ID:
        return None
    if not core.SHEET_WRITE_ENABLED:
        # dev не пишет в человеческий лист: строка остаётся с пометкой sheet_error,
        # экран сверки покажет её в «лист не подтвердил»
        _mark_sheet_state(created["id"], error=core.DEV_SHEET_ERROR)
        return core.DEV_SHEET_ERROR
    tx_date = (datetime.strptime(body.tx_date, "%Y-%m-%d").date()
               if body.tx_date else datetime.now(core.TZ).date())
    block = {"expense": "expense", "income": "show_income"}.get(body.tx_type, "expense")
    row = _sheet_row({
        "id": created["id"], "tx_date": tx_date, "tx_type": body.tx_type, "block": block,
        "amount": body.amount, "category": body.category, "project": body.project,
        "counterparty": body.counterparty, "purpose": body.purpose,
        "source_text": body.source_text, "chat_id": body.chat_id,
    })
    error = _append_sheet_row(row)
    _mark_sheet_state(created["id"], error=error)
    return error



@router.post("/tools/budget/commit")
def budget_commit(body: BudgetCommitIn, who: dict = Depends(core.require("budget.write"))) -> dict:
    """
    Запись операции. Крупная операция пишется сразу: подтверждение из проекта убрано
    решением 30.10.2026 (см. комментарий ниже), след остаётся в аудите.
    """
    if body.amount <= 0:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Сумма должна быть больше нуля",
                       code="amount_not_positive")

    # без ключа повтор того же запроса создавал вторую операцию (smoke docs/59, C-10);
    # бот всегда шлёт tg:budget:<chat>:<message>, остальным клиентам ключ тоже нужен
    key = (body.idempotency_key or "").strip()
    if not key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "idempotency_key_required: передайте ключ операции")

    if key:
        with core.pool.connection() as conn:
            dup = conn.execute("select * from budget_tx where idempotency_key = %s",
                               (key,)).fetchone()
        if dup:
            return {"ok": True, "deduped": True,
                    "tx": {"id": dup["id"], "period": dup["period"],
                           "amount": float(dup["amount"]), "category": body.category},
                    "sheet": "skipped"}

    # Решение 30.10.2026: подтверждение крупных сумм из проекта убрано.
    # Расходы и доходы вносят только организаторы, второй подтверждающий никого
    # не защищает, а людей пугает. Пишем сразу, в одном месте и с аудитом.

    with core.pool.connection() as conn:
        created = _write_tx(conn, body, who, key)
        if created is None:                       # параллельный повтор с тем же ключом
            created = conn.execute("select * from budget_tx where idempotency_key = %s",
                                   (key,)).fetchone()
            if created is not None:
                return {"ok": True, "deduped": True,
                        "tx": {"id": created["id"], "period": created["period"],
                               "amount": float(created["amount"]), "category": body.category},
                        "sheet": "skipped"}

    sheet_error = _append_tx_sheet(body, created)
    return {"ok": True, "tx": {"id": created["id"], "period": created["period"],
                               "amount": float(body.amount), "category": body.category},
            "sheet": "ok" if not sheet_error else "error", "sheet_error": sheet_error}



class SheetRetryIn(core.ApiIn):
    limit: int = Field(default=50, ge=1, le=200)



@router.post("/tools/budget/sheet-retry")
def budget_sheet_retry(body: SheetRetryIn | None = None,
                       who: dict = Depends(core.require("budget.write"))) -> dict:
    """Повтор записи строк, до листа не дошедших (C-3, Ш-26).

    Сверка умеет только показать `ids_лист_не_подтвердил`; человеку нужен способ
    дописать их. Берём операции с `sheet_error` и пустым `sheet_written_at`:
    успех — заполняем `sheet_written_at`, повторный сбой — обновляем `sheet_error`.
    ID, уже стоящие в листе (append прошёл, ответ потерялся), не дублируем, а
    просто подтверждаем.
    """
    limit = body.limit if body else 50
    empty = {"ok": True, "attempted": 0, "written": [], "already": [], "failed": [],
             "remaining": 0}
    if not core.BUDGET_SPREADSHEET_ID:
        return {**empty, "ok": False, "error": "no_spreadsheet"}
    if not core.SHEET_WRITE_ENABLED:
        return {**empty, "ok": False, "error": core.DEV_SHEET_ERROR}

    with core.pool.connection() as conn:
        rows = conn.execute("""
            select t.id, t.tx_date, t.tx_type, t.block, t.amount, t.counterparty,
                   t.purpose, t.source_chat_id as chat_id,
                   coalesce(c.name, '') as category, coalesce(p.name, '') as project
            from budget_tx t
            left join category c on c.id = t.category_id
            left join project p on p.id = t.project_id
            where t.sheet_written_at is null and t.sheet_error is not null
              and t.status = 'confirmed'
            order by t.id
            limit %s""", (limit,)).fetchall()

    try:
        in_sheet = _sheet_ids()
    except GOOGLE_API_ERRORS:
        # чтение листа не удалось — не мешаем главному: пробуем дописать
        in_sheet = set()

    written: list[int] = []
    already: list[int] = []
    failed: list[dict] = []
    for row in rows:
        tx = dict(row)
        if tx["id"] in in_sheet:
            _mark_sheet_state(tx["id"])
            already.append(tx["id"])
            continue
        error = _append_sheet_row(_sheet_row(tx))
        _mark_sheet_state(tx["id"], error=error)
        if error is None:
            written.append(tx["id"])
        else:
            failed.append({"id": tx["id"], "error": error})

    with core.pool.connection() as conn:
        # «Осталось» — это очередь целиком, а не только текущая порция: при limit=50
        # из 120 строк человек должен видеть 120, а не 50 оставшихся.
        remaining = conn.execute("""
            select count(*) as n from budget_tx
            where sheet_written_at is null and sheet_error is not null
              and status = 'confirmed'
        """).fetchone()["n"]
        core.audit(conn, "human", who.get("id"), "budget.sheet.retry", "budget_tx", None,
              after={"found": len(rows), "written": len(written),
                     "already": len(already), "failed": len(failed), "ids": written},
              source={"kind": "sheet_retry"})

    return {"ok": True, "attempted": len(rows), "written": written, "already": already,
            "failed": failed, "remaining": remaining}



def period_title(period: str) -> str:
    """`2026-10` → «октябрь 2026» — подпись периода в панели и отчётах."""
    try:
        year, month = (int(x) for x in period.split("-", 1))
    except (TypeError, ValueError):
        return period
    if not 1 <= month <= 12:
        return period
    return f"{core.MONTHS_RU[month - 1]} {year}"



@router.get("/tools/budget/recent")
def budget_recent(limit: int = Query(default=5, ge=1, le=50),
                  period: str | None = Query(default=None),
                  who: dict = Depends(core.require("budget.read"))) -> dict:
    """Последние операции месяца.

    Раньше здесь были «последние по всему журналу»: сортировка по tx_date поднимала
    наверх авансы с датой услуги в декабре, а операции текущего месяца вытеснялись
    (обращение №15). Теперь по умолчанию показывается текущий месяц по period
    (месяц блока листа), другой месяц запрашивается явно.

    Границы листания (Ш-43, V-003): нижняя — первый период в budget_tx, верхняя —
    текущий месяц. Запрос вне границ приводится к ближайшей границе, чтобы старые
    кнопки в чате не открывали пустой 2025 год.
    """
    current = datetime.now(core.TZ).strftime("%Y-%m")
    wanted = (period or current).strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", wanted):
        raise HTTPException(422, "bad_period: ожидается YYYY-MM")
    with core.pool.connection() as conn:
        bounds = conn.execute(
            "select min(period) as first_period from budget_tx").fetchone()
        first_period = (bounds or {}).get("first_period") or current
        if wanted < first_period:
            wanted = first_period
        if wanted > current:
            wanted = current
        rows = conn.execute("""
            select t.id, t.period, t.tx_date, t.block, t.tx_type, t.amount,
                   coalesce(c.name, $$Прочее$$) category, t.purpose
            from budget_tx t left join category c on c.id = t.category_id
            where t.status = $$confirmed$$ and t.period = %s
            order by t.tx_date desc, t.id desc limit %s""",
            (wanted, limit)).fetchall()
    return {"ok": True, "period": wanted, "period_title": period_title(wanted),
            "current": wanted == current, "first_period": first_period,
            "current_period": current, "data": rows}



@router.get("/tools/categories")
def categories_list(who: dict = Depends(core.current)) -> dict:
    with core.pool.connection() as conn:
        rows = conn.execute("select name, direction from category order by direction, name").fetchall()
    return {"ok": True, "data": rows}



@router.get("/webapp/categories")
def webapp_categories(who: dict = Depends(core.webapp_actor)) -> dict:
    """Категории бюджета для формы: по направлениям, а не общим списком.

    Общий список позволял записать доход в расходную категорию («Коммунальные
    платежи») — инвариант I3 такое ловит, но человек уже нажал «Записать».
    """
    with core.pool.connection() as conn:
        rows = conn.execute(
            "select name, direction from category where coalesce(name, '') <> '' "
            "order by direction, name").fetchall()
    return {"ok": True,
            "categories": [row["name"] for row in rows],   # прежний вид, для старых страниц
            "by_direction": group_categories(rows)}



def group_categories(rows) -> dict[str, list[str]]:
    """Раскладывает категории по направлению: форма показывает только свои."""
    by_direction: dict[str, list[str]] = {"income": [], "expense": []}
    for row in rows:
        direction = row["direction"] if row["direction"] in by_direction else "expense"
        name = row["name"]
        if name:
            by_direction[direction].append(name)
    return by_direction



@router.post("/webapp/tx/save")
def webapp_tx_save(body: BudgetCommitIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """Запись операции из формы. Идемпотентность — ключ от клиента."""
    return budget_commit(body, who=core._webapp_require(who, "budget.write"))



class BudgetParseIn(core.ApiIn):
    text: str



@router.post("/tools/budget/parse")
def budget_parse(body: BudgetParseIn, who: dict = Depends(core.current)) -> dict:
    """Детерминированный разбор траты (правила в app/rules.py, покрыты unit-тестами).

    Подсказки по именам людей («5000 Соколову» → гонорары) приходят из данных, а не из кода.
    """
    return parse_budget_text(body.text, today=datetime.now(core.TZ).date(),
                             extra_hints=category_hints())



@router.get("/tools/budget/summary")
def budget_summary(project: str | None = None,
                   who: dict = Depends(core.require("budget.read"))) -> dict:
    """Итоги по периодам и баланс студии — для ответов «сколько у нас денег»."""
    with core.pool.connection() as conn:
        rows = conn.execute("""
            select t.period,
                   sum(case when t.tx_type='income' and t.block <> 'dues' and not coalesce(t.is_plan,false) then t.amount else 0 end) as income,
                   sum(case when t.tx_type='expense' and not coalesce(t.is_plan,false) then t.amount else 0 end) as expense,
                   count(*) as ops
            from budget_tx t left join project pr on pr.id = t.project_id
            where t.status = 'confirmed'
              and (%s::text is null or pr.name ilike %s::text)
            group by t.period order by t.period""", (project, f"%{project}%" if project else None)).fetchall()
        dues = {r["period"]: float(r["s"]) for r in conn.execute(
            "select period, sum(amount_paid) s from dues_charge group by period")}

    data, total_in, total_out = [], 0.0, 0.0
    for r in rows:
        inc = float(r["income"]) + dues.get(r["period"], 0.0)
        exp = float(r["expense"])
        total_in += inc
        total_out += exp
        data.append({"period": r["period"], "income": round(inc, 2),
                     "expense": round(exp, 2), "result": round(inc - exp, 2),
                     "ops": r["ops"]})
    return {"ok": True, "project": project, "periods": data,
            "balance": round(total_in - total_out, 2),
            "income_total": round(total_in, 2), "expense_total": round(total_out, 2)}

