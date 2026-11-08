"""Ограниченный цикл ReAct для свободного текста (docs/development/108, A-1).

Почему цикл здесь, а не «один вызов модели»: одиночный вызов отвечает из памяти —
он не знает сегодняшнюю дату, не видит расписание и бюджет и придумывает цифры.
Агентный шаг обязан сначала получить факты инструментом, потом отвечать.

Пределы заданы кодом, а не просьбой в промпте:
  * не больше `config.max_steps()` шагов (по умолчанию 4);
  * общий таймаут `config.timeout()` секунд (по умолчанию 15);
  * бюджет токенов `config.token_budget()` на запрос;
  * список инструментов — реестр `registry`, отфильтрованный по правам человека;
  * запись не выполняется: `propose_budget_tx` возвращает только намерение.

Недоступность провайдера, таймаут или исчерпанный бюджет — это не «Думаю…»
навсегда: вызывающий получает `ModelUnavailable` и уходит в правила (docs/21).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from .. import core, llm, rules
from ..participant_names import category_hints
from . import citations, config, features, proposals, registry, tasks

MAX_TOOL_RESULT = registry.MAX_RESULT_CHARS
MAX_ANSWER_CHARS = 900
WEEKDAYS_RU = registry.WEEKDAYS_RU

#: Как называть шаг в строке «календарь → занятость → окна». Подпись — про данные,
#: а не про имя функции: человеку не нужно знать `read_availability`.
TOOL_LABELS = {
    "now": "дата",
    "read_schedule": "календарь",
    "read_availability": "занятость",
    "find_slots": "окна",
    "read_budget": "бюджет",
    "read_dues": "взносы",
    "list_files": "файлы",
    "read_file": "файл",
    "web_search": "поиск",
    "propose_budget_tx": "операция",
    "propose_event": "событие",
    "propose_note": "заметка",
    "propose_move": "перемещение",
}

#: Вид данных для «входа в дом», если инструмент вернул `needs_input` без своего вида.
NEEDS_KIND = {"read_file": "files", "list_files": "files", "find_slots": "slots",
              "propose_event": "event", "propose_note": "note",
              "propose_budget_tx": "budget"}

#: Что добавляется к системной подсказке, когда человек поставил задачу (`task: true`).
TASK_HINT = "\n".join([
    "Это задача, а не вопрос: её видно по просьбе сделать несколько вещей.",
    "1. Сначала составь план в уме и выполняй его инструментами по шагам.",
    "2. Для записи вызывай propose_* и ничего не выполняй сам: человек подтвердит.",
    "   Несколько предложений подряд — это план, он покажется одной карточкой.",
    "3. Если для действия не хватает даты, времени, участников или суммы —",
    "   не придумывай их: вызови propose_* без этих полей. Человек получит вход",
    "   в приложение, а не догадку.",
    "4. В конце ответь итогом: что сделал и что не смог. Выполненного не выдумывай:",
    "   если шаг не удался, так и скажи.",
])

SCENARIO_HINT = {
    "budget": "Скорее всего человек спрашивает про деньги. Начни с read_budget или read_dues, "
              "если речь про бюджет студии или свои взносы.",
    "schedule": "Скорее всего человек спрашивает про время. Начни с read_schedule и/или "
                "read_availability.",
    "dues": "Скорее всего человек спрашивает про свои взносы. Начни с read_dues.",
    "assistant": "Это общий вопрос. Если нужны факты студии — возьми их инструментом, "
                 "иначе ответь коротко.",
}


class ModelUnavailable(RuntimeError):
    """Модель недоступна: таймаут, сеть или ошибка провайдера. Не поломка кода."""

    def __init__(self, code: str = "provider_unavailable") -> None:
        super().__init__(code)
        self.code = code


def system_prompt(who: dict | None = None, hint: str | None = None,
                  categories: str | None = None,
                  permitted: str | None = None, task: bool = False,
                  extra: str | None = None) -> str:
    """Промпт агентного шага.

    Дата в промпте — серверная (`core.now()`), чтобы модель не достраивала год из
    памяти: дефект «27 октября 2023 года» рождался именно так. Факты по студии всё
    равно только из инструментов, и вопрос про дату проверяется вызовом `now`.
    """
    data = who or {}
    name = str(data.get("display_name") or data.get("name") or "участник студии")
    roles = ", ".join(data.get("roles") or []) or "неизвестно"
    moment = core.now()
    lines = [
        "Ты — ассистент театра-студии «СВОИ» в Telegram. Ты не человек.",
        "Сегодня по Самаре: " + moment.strftime("%d.%m.%Y") + ", "
        + WEEKDAYS_RU[moment.weekday()] + ", год " + str(moment.year) + ".",
        "Правила, которые нельзя нарушать:",
        "1. Дату, время и день недели не бери из памяти — вызывай инструмент now.",
        "   Называй дату в формате ДД.ММ.ГГГГ ровно так, как её вернул инструмент.",
        "2. Расписание, занятость, бюджет, взносы и файлы — только из инструментов.",
        "   Если инструмент не дал данных, так и скажи. Числа не придумывай.",
        "   Год и месяц называй только те, что есть в ответе инструмента: если период",
        "   не назван, не добавляй его от себя. Период бери из поля period_human.",
        "3. Ты ничего не записываешь. Просьбу записать расход или доход оформляй",
        "   инструментом propose_budget_tx: запись подтвердит человек кнопкой.",
        "   Категорию бери ровно из закрытого списка ниже; если подходящей нет —",
        "   «Прочее» для расхода, а не выдуманная категория.",
        "3а. Куда идти за фактом: «сколько пришло, поступило, доход, баланс, траты "
        "студии» — read_budget; «мои взносы, тариф, оплачено ли, долг» — read_dues.",
        "3б. Если человек назвал месяц без года, это текущий год: передай period "
        "в виде YYYY-MM. Если данных за названный период нет, так и скажи.",
        "4. Если вопрос не про студию и инструменты не нужны — ответь сам коротко.",
        "5. Ссылайся на факты инструментов как есть: даты, суммы, имена.",
        "6. Отвечай по-русски, до 600 знаков, без эмодзи, без markdown, без JSON.",
        "7. Не называй себя моделью, ботом, маршрутизатором или ИИ.",
        "8. Один и тот же инструмент с теми же аргументами дважды не вызывай:",
        "   результат уже есть в истории. Разные аргументы — можно.",
        "9. Если по данным ответить нельзя — честно скажи «Пока не умею» и перечисли,",
        "   что доступно из списка ниже. Экраны, кнопки и действия не придумывай.",
        "10. Файлы: содержимое — только из read_file. Ссылайся на имя файла и цитируй",
        "    фрагмент дословно; если нужного в файле нет — скажи «в файле этого нет».",
        "Ты знаешь, кто пишет: " + name + ". Роли: " + roles + ".",
    ]
    if task:
        # Рамка «Озадачить» (решение 05.11.2026): меняется формулировка задачи, а не
        # права. Агент по-прежнему не пишет в базу — он предлагает шаги и действия.
        lines.append(
            "Человек попросил задачу: предложи шаги и конкретные действия, а не только "
            "текст ответа. Действия — из списка ниже и без записи: запись подтверждает "
            "человек кнопкой.")
    if permitted:
        lines.append(permitted)
    if categories:
        lines.append("Закрытый список категорий бюджета: " + categories + ".")
    if hint:
        lines.append(hint)
    if task:
        lines.append(TASK_HINT)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def category_context(conn) -> str | None:
    """Строка с закрытым списком категорий: модель выбирает из него, а не выдумывает."""
    rows = conn.execute(
        "select direction, name from category order by direction, name").fetchall()
    expense = [row["name"] for row in rows if row["direction"] == "expense"]
    income = [row["name"] for row in rows if row["direction"] == "income"]
    if not expense and not income:
        return None
    parts = []
    if expense:
        parts.append("расход — " + ", ".join(expense))
    if income:
        parts.append("доход — " + ", ".join(income))
    return "; ".join(parts)


def _post(payload: dict, transport=None) -> dict:
    """Синхронный запрос к провайдеру; в тестах подменяется транспортом.

    Цикл асинхронный, а HTTP — через `asyncio.to_thread`, чтобы не блокировать
    event loop tool-api на время ответа модели.
    """
    if transport is not None:
        return transport(payload)
    with httpx.Client(timeout=llm.timeout()) as client:
        response = client.post(
            llm.chat_url(),
            headers={"Authorization": f"Bearer {llm.api_key()}",
                     "Content-Type": "application/json"},
            json=payload)
    if response.status_code >= 400:
        raise ModelUnavailable("llm_error")
    try:
        return response.json()
    except ValueError as exc:
        raise ModelUnavailable("llm_unavailable") from exc


async def _call_model(payload: dict, transport=None, timeout: float | None = None) -> dict:
    """Один запрос к модели с таймаутом шага."""
    if not llm.is_configured() and transport is None:
        raise ModelUnavailable("llm_unavailable")
    wait = timeout if timeout is not None else llm.timeout()
    try:
        return await asyncio.wait_for(asyncio.to_thread(_post, payload, transport), timeout=wait)
    except TimeoutError as exc:
        raise ModelUnavailable("provider_timeout") from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise ModelUnavailable("llm_unavailable") from exc


def _parse_tool_calls(choice: dict) -> list[dict]:
    calls = choice.get("tool_calls") or []
    return [call for call in calls if isinstance(call, dict)]


def _tool_arguments(function: dict) -> dict:
    raw = function.get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def classifier_payload(text: str) -> dict:
    """Запрос классификатора как есть: одно слово, без инструментов, temperature 0.

    Вынесен из `classify`, потому что его же отправляет замер стоимости
    (`scripts/agent_cost.py --measure-classifier`): считать цену классификатора
    по другому промпту означало бы мерить не то, что работает в бою.
    """
    return {
        "model": config.classifier_model(),
        "messages": [
            {"role": "system", "content":
                "Определи сценарий сообщения в театре-студии. Ответь РОВНО одним словом: "
                "BUDGET (деньги, траты, бюджет), SCHEDULE (время, занятость, репетиции), "
                "DUES (свои взносы и долг), ASSISTANT (всё остальное)."},
            {"role": "user", "content": text.strip()[:1500]},
        ],
        "temperature": 0,
        "max_tokens": 4,
    }


async def classify(text: str, *, transport=None, timeout: float | None = None) -> str | None:
    """Дешёвый классификатор сценария без инструментов. None — «не знаю», идём дальше."""
    if not config.classifier_enabled():
        return None
    payload = classifier_payload(text)
    try:
        data = await _call_model(payload, transport, timeout)
    except ModelUnavailable:
        return None
    content = ""
    try:
        content = str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return None
    token = "".join(ch for ch in content.upper() if ch.isalpha())
    mapping = {"BUDGET": "budget", "SCHEDULE": "schedule",
               "DUES": "dues", "ASSISTANT": "assistant"}
    return mapping.get(token)


def _usage(data: dict) -> dict:
    """Расход одного вызова модели: вход, выход и всего (AUD-10, FR-E10).

    Провайдер отдаёт три числа (`prompt_tokens`, `completion_tokens`, `total_tokens`).
    Раньше в журнал шла только сумма, причём как «выход», — по такой строке стоимость
    не считается: у входа и выхода разные цены (`config/agent-models.yaml`).
    `total_tokens` провайдера главнее суммы: если он её округлил, верим ему, а вход и
    выход остаются раздельными для расчёта.

    Обоснование: карта AUD-10 `t_4a9705ea`, docs/reports/119 §9 п.5, §10 п.5.
    """
    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or (tokens_in + tokens_out)
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "tokens": total}


def _add_usage(total: dict, extra: dict) -> dict:
    """Сумма расхода за сессию: шаги, исправление года, финальный ответ."""
    return {key: int(total.get(key) or 0) + int(extra.get(key) or 0)
            for key in ("tokens_in", "tokens_out", "tokens")}


ZERO_USAGE = {"tokens_in": 0, "tokens_out": 0, "tokens": 0}



async def run(conn, who: dict, question: str, *, transport=None,
              max_steps: int | None = None, token_budget: int | None = None,
              deadline: float | None = None, task: bool = False,
              cancel=None, prompt_extra: str | None = None,
              features_on: bool = True, chat_id: int | None = None) -> dict:
    """Цикл «модель → инструмент → модель». Возвращает ответ, шаги, расход и план.

    `task` — рамка задачи «Озадачить»: шагов 6–8 вместо 4 и сценарий `task` в
    ответе; модель обязана идти по шагам и вернуть итог «что сделал / что не смог».
    Классификатор при этом не зовётся: намерение уже названо человеком кнопкой,
    второй разбор — лишний расход. `cancel` — проверка отмены между шагами;
    `chat_id` позволяет ей же смотреть флаг `agently.tasks`.
    `features_on=False` используется оболочкой файлового агента: там свои правила
    ответа и не нужен детерминированный отказ «действие не поддерживается».
    """
    started = time.monotonic()
    limit_steps = max_steps or (config.task_max_steps() if task else config.max_steps())
    budget = token_budget or config.token_budget()
    hard_deadline = deadline or (started + config.timeout())

    def cancelled() -> bool:
        if callable(cancel) and cancel():
            return True
        return tasks.is_cancelled(chat_id) if chat_id is not None else False

    def reply(ok: bool, **extra) -> dict:
        base = {"ok": ok, "steps": steps, "step_count": len(steps), **spent,
                "model": config.agent_model(), "scenario": scenario,
                "step_line": step_line(steps), "plan": plan, "propose": propose}
        base.update(extra)
        return base

    # «Что ты умеешь / что можно записать или посмотреть» — вопрос про реестр функций,
    # а не про данные: отвечаем детерминированно, без модели и без выдумок.
    if features_on and features.answers_capability_question(question):
        return features.capability_answer(who)

    # Просьба о действии, которого нет в реестре (перенос, отмена, удаление, правка):
    # честный отказ, а не «сейчас перенесу» после чтения расписания.
    if features_on and features.asks_unsupported_action(question):
        return features.unsupported_action_answer(who)

    # Рамка «Озадачить»: намерение названо кнопкой, классификатор не нужен.
    # Без рамки классифицируем, если включены фичи.
    scenario: str | None = None
    if task:
        scenario = "task"
    elif features_on:
        scenario = await classify(question, transport=transport,
                                  timeout=max(1.0, min(5.0, hard_deadline - time.monotonic())))
    tools = registry.tool_schemas(who)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt(
            who, SCENARIO_HINT.get(scenario or "", None), category_context(conn),
            features.prompt_block(who), task=task, extra=prompt_extra)},
        {"role": "user", "content": question.strip()[:2000]},
    ]
    steps: list[dict] = []
    seen: dict[str, dict] = {}
    spent = dict(ZERO_USAGE)
    propose: dict | None = None
    plan: dict | None = None
    collected: list[dict] = []
    needs: dict | None = None
    read_texts: list[str] = []

    for index in range(1, limit_steps + 1):
        if cancelled():
            return reply(False, error="cancelled",
                         message="Остановил: задача отменена.")
        remaining = hard_deadline - time.monotonic()
        if remaining <= 0.5:
            return reply(False, error="timeout",
                         message="Не успел за отведённые 15 секунд.")
        payload: dict[str, Any] = {"model": config.agent_model(), "messages": messages,
                                   "temperature": 0.2, "max_tokens": 700}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = await _call_model(payload, transport, min(remaining, llm.timeout()))
        spent = _add_usage(spent, _usage(data))
        choice = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
        calls = _parse_tool_calls(choice)

        if not calls:
            answer = str(choice.get("content") or "").strip()[:MAX_ANSWER_CHARS]
            if not answer and propose:
                answer = str(propose.get("human_text") or propose.get("text") or "")
            answer, extra = await _correct_stray_years(answer, messages, transport,
                                                       hard_deadline)
            spent = _add_usage(spent, extra)
            verified = True
            if read_texts and answer:
                answer, verified = citations.verify_citations(answer, read_texts)
            if propose is None:
                # Детерминированный запасной путь: если в тексте есть сумма и направление,
                # карточка подтверждения обязана появиться — это не решает модель.
                fallback = rules_answer(conn, who, question)
                if fallback.get("propose"):
                    propose = fallback["propose"]
                    answer = fallback["answer"]
            propose, plan = _settle(collected, needs, propose, plan)
            return reply(True, answer=answer, verified=verified, engine="agent")

        messages.append({"role": "assistant", "content": choice.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            if cancelled():
                return reply(False, error="cancelled", message="Остановил: задача отменена.")
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            args = _tool_arguments(function)
            key = name + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
            moment = time.monotonic()
            if key in seen:
                # Повторный вызов того же инструмента с теми же аргументами:
                # в базе ничего не меняем, но и шаг не тратим — отдаём прежний результат.
                result = dict(seen[key])
                result["note"] = "повторный вызов с теми же аргументами, результат выше"
            else:
                result = registry.execute(conn, who, name, args)
                seen[key] = result
            if result.get("ok") and result.get("proposal"):
                lifted = result["proposal"]
                collected.append({"tool": lifted["tool"], "args": lifted["args"],
                                  "human_text": lifted.get("human_text") or ""})
                propose = collected[-1]
            if result.get("error") == "needs_input":
                kind = result.get("kind") or NEEDS_KIND.get(name) or ""
                needs = proposals.open_proposal(kind, result.get("message"))
            if name == "read_file" and result.get("ok") and result.get("text"):
                read_texts.append(str(result["text"]))
            steps.append({"step": index, "tool": name, "args": args,
                          "ok": bool(result.get("ok")),
                          "error": result.get("error"),
                          "message": str(result.get("message") or "")[:200] or None,
                          "action_id": result.get("action_id"),
                          "latency_ms": int((time.monotonic() - moment) * 1000)})
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name,
                             "content": json.dumps(result, ensure_ascii=False)[:MAX_TOOL_RESULT]})
        if spent["tokens"] >= budget:
            return reply(False, error="token_budget",
                         message="Израсходован бюджет токенов на запрос.")

    # Шаги кончились. Для вопроса факты уже собраны — просим ответ без инструментов.
    # Для задачи итог «не разобрался» честнее достроенного: 9-й шаг не начинался,
    # и выдумывать, что задача выполнена, нельзя.
    propose, plan = _settle(collected, needs, propose, plan)
    if not task:
        answer = await _final_answer(messages, transport, hard_deadline)
        if answer:
            verified = True
            if read_texts:
                answer, verified = citations.verify_citations(answer, read_texts)
            return reply(True, answer=answer, verified=verified, engine="agent",
                         note="step_limit")
    return reply(False, error="step_limit",
                 message="Не разобрался за отведённые шаги. Уточните вопрос.")


def _settle(collected: list[dict], needs: dict | None, propose: dict | None,
            plan: dict | None) -> tuple[dict | None, dict | None]:
    """Что показать человеку: одну карточку, план или вход в приложение.

    Несколько предложений — это план (одна карточка «Выполнить план»); одно —
    обычная карточка подтверждения. Если инструмент честно сказал «не хватает
    данных», показывается вход в Mini App, а не догадка.
    """
    if len(collected) > 1:
        return None, {"steps": list(collected),
                      "human_text": f"План из {len(collected)} шагов"}
    if len(collected) == 1:
        return collected[0], None
    if needs is not None:
        return needs, None
    return propose, plan


def step_line(steps: list[dict]) -> str:
    """Строка шагов для итога: «календарь → занятость → окна»."""
    labels: list[str] = []
    for step in steps:
        label = TOOL_LABELS.get(str(step.get("tool") or ""), str(step.get("tool") or ""))
        if label and (not labels or labels[-1] != label):
            labels.append(label)
    return " → ".join(labels)


async def _final_answer(messages: list[dict], transport, hard_deadline: float) -> str:
    """Ответ без инструментов по уже собранным фактам (шаги кончились)."""
    remaining = hard_deadline - time.monotonic()
    if remaining <= 0.5:
        return ""
    payload = {"model": config.agent_model(),
               "messages": messages + [{"role": "user", "content":
                   "Шаги закончились. Ответь на вопрос коротко по уже полученным данным, "
                   "инструменты не вызывай; если данных нет — так и скажи."}],
               "temperature": 0.2, "max_tokens": 500}
    try:
        data = await _call_model(payload, transport, min(remaining, llm.timeout()))
    except ModelUnavailable:
        return ""
    try:
        return str((data["choices"][0]["message"] or {}).get("content") or "").strip()[
            :MAX_ANSWER_CHARS]
    except (KeyError, IndexError, TypeError):
        return ""


async def _correct_stray_years(answer: str, messages: list[dict], transport,
                              hard_deadline: float) -> tuple[str, dict]:
    """Год, которого нет в данных инструментов, — это выдумка. Одна попытка исправить.

    Дефект «бот ответил датой 2023 года» ловится здесь фактом, а не надеждой на промпт:
    если модель назвала год, которого нет ни в одном ответе инструмента, просим
    переписать ответ по данным. Возвращает ответ и потраченные токены.
    """
    facts = "\n".join(str(message.get("content") or "") for message in messages
                      if message.get("role") == "tool")
    current = core.now().year
    stray = sorted({year for year in re.findall(r"\b(20\d{2})\b", answer)
                    if int(year) != current and year not in facts})
    if not stray:
        return answer, dict(ZERO_USAGE)
    remaining = hard_deadline - time.monotonic()
    if remaining <= 0.5:
        return answer, dict(ZERO_USAGE)
    payload = {"model": config.agent_model(),
               "messages": messages + [
                   {"role": "assistant", "content": answer},
                   {"role": "user", "content":
                    "В ответе год " + ", ".join(stray) + ", которого нет в данных "
                    "инструментов. Перепиши ответ только по фактам из инструментов: "
                    "период бери из поля period_human, дату — из now. Ничего не выдумывай."}],
               "temperature": 0.1, "max_tokens": 400}
    try:
        data = await _call_model(payload, transport, min(remaining, llm.timeout()))
    except ModelUnavailable:
        return answer, dict(ZERO_USAGE)
    try:
        fixed = str((data["choices"][0]["message"] or {}).get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return answer, dict(ZERO_USAGE)
    return (fixed[:MAX_ANSWER_CHARS] or answer), _usage(data)


# ------------------------------------------------------- деградация к правилам
_DATE_QUESTION = ("какой сегодня", "какое сегодня", "какая сегодня", "какой сейчас день",
                  "какое число", "какая дата", "сегодняшняя дата", "сегодняшнее число",
                  "какой день недели", "который час", "сколько времени")


def asks_date(text: str) -> bool:
    lowered = (text or "").lower().replace("ё", "е")
    return any(phrase in lowered for phrase in _DATE_QUESTION)


def rules_answer(conn, who: dict, text: str) -> dict:
    """Ответ по правилам, когда модель недоступна (каскад docs/21).

    Ничего не выдумывает: дата — из `core.now()`, сумма и категория — из
    детерминированного разбора `rules`. Человек получает причину и путь кнопками.
    """
    moment = core.now()
    if features.answers_capability_question(text):
        return {"answer": features.human_capabilities(who),
                "scenario": "assistant", "propose": None}
    if features.asks_unsupported_action(text):
        return {"answer": features.unsupported_action_answer(who)["answer"],
                "scenario": "assistant", "propose": None}
    if asks_date(text):
        return {"answer": f"Сегодня {moment.strftime('%d.%m.%Y')}, "
                          f"{WEEKDAYS_RU[moment.weekday()]}, {moment.strftime('%H:%M')}.",
                "scenario": "assistant", "propose": None}
    amount = rules.detect_amount(text)
    tx_type = rules.detect_type(text)
    if amount and tx_type:
        category = (rules.detect_category(text, extra_hints=category_hints())
                    or rules.CATEGORY_FALLBACK)
        row = conn.execute("select name from category where name = %s and direction = %s",
                           (category, tx_type)).fetchone() if category else None
        if row is None:
            # «Прочее» бывает только расходным: для дохода берём категорию
            # направления из закрытого справочника, а не выдумываем свою
            row = conn.execute(
                "select name from category where direction = %s order by name limit 1",
                (tx_type,)).fetchone()
        if row is not None:
            draft = {"tx_type": tx_type, "amount": round(float(amount), 2),
                     "category": row["name"], "purpose": row["name"],
                     "tx_date": moment.date().isoformat(), "counterparty": None}
            kind = "доход" if tx_type == "income" else "расход"
            return {"answer": f"Похоже на {kind} {amount:.0f} ₽. Нужна кнопка подтверждения.",
                    "scenario": "budget",
                    "propose": {"tool": "budget.tx", "args": draft,
                                "human_text": registry.proposal_text(
                                    tx_type, float(amount), row["name"],
                                    draft["tx_date"])}}
    return {"answer": "Пока не умею разобрать этот вопрос свободным текстом.\n"
                      + features.human_capabilities(who),
            "scenario": "assistant", "propose": None}
