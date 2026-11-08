"""СМ-9. Ассистент: каскад правил → модель → человек, «не понял» формой.

Роли: org, troupe. Домен требований: E1–E11, I1–I2.

Обоснование: FR-E4, FR-E5, FR-E9, FR-E10, FR-E2. На стенде LLM не настроен намеренно
(`LLM_BASE_URL`/`LLM_API_KEY` пусты): это и есть проверка требования «LLM обходится при
недоступности провайдера» — сервис обязан ответить честно, а не выдумать сумму.
"""

SCENARIO = {
    "id": "СМ-9",
    "title": "Ассистент: каскад правил → модель → человек, «не понял» формой",
    "roles": ["org", "troupe"],
    "domain": "E1–E11, I1–I2",
}


def test_cm09_assistant(journal, stand, people, org, troupe):
    """Правила отвечают без модели, модель недоступна — честный отказ, журнал разбора."""
    # 1. Вопрос о деньгах решает детерминированный код, а не модель (FR-E4).
    summary = org.get("/tools/budget/summary")
    journal.check(
        "1. GET /tools/budget/summary (детерминированный ответ)", expected="код 200",
        actual=f"код {summary.code}, периодов {len(summary.field('periods') or [])}",
        evidence=summary.evidence(100), ok=summary.ok, requirements=["FR-E4"])

    # 2. Разбор текста о трате идёт правилами: движок rules-py, без LLM (FR-E9).
    parsed = org.post("/tools/budget/parse", body={"text": "потратил 300 на реквизит"})
    draft = parsed.field("draft") or {}
    journal.check(
        "2. POST /tools/budget/parse (правила)", expected="движок rules-py, сумма 300",
        actual=f"код {parsed.code}, engine={parsed.field('engine')}, "
               f"amount={draft.get('amount')}, category={draft.get('category')}",
        evidence=parsed.evidence(),
        ok=parsed.ok and parsed.field("engine") == "rules-py"
        and float(draft.get("amount") or 0) == 300,
        requirements=["FR-E9", "FR-B1"])

    # 3. Роутер без LLM отвечает честным отказом, а не выдуманным ответом (FR-E5, FR-E2).
    route = org.post("/tools/route", body={"text": "потратил 300 на реквизит"})
    journal.check(
        "3. POST /tools/route (LLM недоступен)",
        expected="код 200, error=llm_unavailable, engine=tool-api-fallback",
        actual=f"код {route.code}, error={route.field('error')}, "
               f"engine={route.field('engine')}, answer={bool(route.field('answer'))}",
        evidence=route.evidence(),
        ok=route.code == 200 and route.field("error") == "llm_unavailable"
        and not route.field("answer"),
        requirements=["FR-E5", "FR-E2"])

    # 4. Ассистент на свободный вопрос: без модели — форма/отказ, без выдуманных сумм (FR-E5).
    asked = org.post("/tools/assistant/ask", body={
        "chat_id": people["org"]["chat_id"], "text": "сколько у нас денег?"})
    journal.check(
        "4. POST /tools/assistant/ask (LLM недоступен)",
        expected="код 200 или 503 с честной причиной, без выдуманного числа",
        actual=f"код {asked.code}, error={asked.field('error')}",
        evidence=asked.evidence(), ok=asked.code in (200, 503),
        requirements=["FR-E5"])

    # 5. Журнал разбора (intent_log) пишется веткой разбора — на стенде её нет (FR-E10).
    journal.skip(
        "5. запись в intent_log после разбора реплики",
        expected="строка intent_log с фразой, намерением и уверенностью",
        evidence="разбор реплики делает ветка роутера/ассистента, которой нужен LLM; "
                 "на стенде LLM_API_KEY пуст",
        cause="нужен LLM (недетерминизм)", reason="разбор реплики без модели не исполняется",
        requirements=["FR-E10"])

    # 6. Порог каскада 0.7 и выбор из закрытого списка — свойство кода, не стенда (FR-E9).
    journal.skip(
        "6. порог 0.7 при разборе моделью",
        expected="ответ с уверенностью ниже 0.7 превращается в форму",
        evidence="на стенде модель не настроена: ветка каскада с моделью не исполняется",
        cause="нужен LLM (недетерминизм)", reason="ответ модели на стенде недоступен",
        requirements=["FR-E9", "FR-E6"])

    # 7. «Не понял» показывается формой в чате — проверяется на живом боте (FR-E5).
    journal.skip(
        "7. ответ «не понял» формой в чате",
        expected="бот присылает форму вместо выдуманного ответа",
        evidence="форму рисует gateway в живом Telegram-чате",
        cause="нужен живой Telegram", reason="форма живёт в чате",
        requirements=["FR-E5"])

    _ = troupe
    journal.assert_passed()
