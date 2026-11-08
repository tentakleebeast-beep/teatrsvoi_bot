"""LLM-маршрутизатор свободного текста: сценарий + ответ (бывший WF-01 в n8n).

Контракт и промпт перенесены из WF-01 один в один (`docs/infrastructure/29` §3):
модель отвечает первой строкой словом BUDGET / SCHEDULE / DUES / ASSISTANT, для
BUDGET второй строкой — `CATEGORY: <название из закрытого списка>`, дальше текст
ответа человеку. Причина переноса — V-026 (01.11.2026): n8n выведен из контура,
а вебхук роутера был открыт в интернет без проверки подлинности, то есть чужой
POST прогонял модель за наши деньги.

Правила docs/21, которые держит модуль:

* модель не придумывает категории — ответ вне переданного списка отбрасывается;
* «Прочее» и `none` — это «не знаю», а не категория;
* уверенность ниже порога `CONFIDENCE_THRESHOLD` (0.7) — категория не принимается,
  решение уходит человеку (кнопки в gateway). Если модель уверенность не назвала,
  порог не применяется: поведение остаётся тем же, что у WF-01.

Модуль не ходит в базу и не пишет журнал: это делает маршрут `POST /tools/route`.
Нет ключа, квота или сбой провайдера — вызывающий код получает `LlmError` и уводит
человека в кнопки, а не показывает «сервис недоступен» (так же вёл себя WF-01).
"""

from __future__ import annotations

import json
import re

import httpx

from . import llm
from .rules import CONFIDENCE_THRESHOLD, FALLBACK_CATEGORY

INTENTS = ("budget", "schedule", "dues", "assistant")

# Первая строка ответа модели → сценарий gateway.
TOKEN_MAP = {"BUDGET": "budget", "SCHEDULE": "schedule",
             "DUES": "dues", "ASSISTANT": "assistant"}

DEFAULT_ANSWER = "Не расслышал вопрос. Переформулируйте, пожалуйста."

_CATEGORY_RE = re.compile(r"CATEGORY:\s*([^\n]+)", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"CONFIDENCE:\s*([0-9]+(?:[.,][0-9]+)?)", re.IGNORECASE)


class LlmError(Exception):
    """Сбой модели: код для gateway (`llm_unavailable`, `llm_error`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def system_prompt(categories: list[str] | None = None,
                  user: dict | None = None) -> str:
    """Промпт WF-01 дословно: те же формулировки, тот же закрытый список.

    Менять текст — значит менять поведение классификации. Поэтому он собран здесь,
    а не в роуте: `tests/unit/test_router_llm.py` сверяет ключевые требования
    («модель не называет себя маршрутизатором», закрытый список, 600 знаков).
    """
    cats = "; ".join(categories or []) or "список не передан"
    data = user or {}
    name = str(data.get("name") or "участник студии")
    roles = ", ".join(data.get("roles") or []) or "неизвестно"
    return "\n".join([
        "Ты — ассистент театра-студии «СВОИ» в Telegram. Ты не человек.",
        "Ты не имеешь доступа к базе, таблицам и календарю: знаешь только имя и роли пишущего.",
        "",
        "Формат ответа (служебный, его видит только программа):",
        "первая строка — РОВНО одно слово: BUDGET, SCHEDULE, DUES или ASSISTANT.",
        "BUDGET — трата, доход, оплата, покупка за деньги.",
        "SCHEDULE — занятость, свободное время, репетиция, «когда кто может».",
        "DUES — вопрос про свои взносы и долг.",
        "ASSISTANT — всё остальное, в том числе вопросы «как сделать», «где найти», «что умеешь».",
        "Важно: если человек спрашивает КАК записать трату, а не сообщает о трате — это ASSISTANT.",
        "",
        "Если BUDGET — второй строкой обязательно: CATEGORY: <название из списка> или CATEGORY: none.",
        "Выбирай ТОЛЬКО из списка ниже. Выбор вне списка будет отброшен.",
        "Список категорий: " + cats,
        "",
        "Никогда не называй себя маршрутизатором, моделью или ботом-классификатором.",
        "Если ASSISTANT — со второй строки ответ человеку: до 600 знаков, по-русски,",
        "без эмодзи, без markdown, без JSON.",
        "Ты знаешь, кто пишет: " + name + ". Роли: " + roles + ".",
        "На вопрос «кто я» отвечай этими данными, не проси представиться.",
        "Если нужны данные, которых у тебя нет, подскажи: /participants, /pending, "
        "кнопки Бюджет и Расписание.",
    ])


def _unwrap_json(answer: str) -> tuple[str, str | None, str | None]:
    """Модель иногда отвечает JSON вместо текста — достаём поля, остальное не трогаем."""
    if not answer.startswith("{"):
        return answer, None, None
    try:
        obj = json.loads(answer)
    except (ValueError, TypeError):
        return answer, None, None
    if not isinstance(obj, dict):
        return answer, None, None
    intent = TOKEN_MAP.get(str(obj.get("intent") or "").upper())
    category = obj.get("category")
    text = str(obj.get("answer") or obj.get("text") or "").strip()
    return text or answer, intent, (None if category is None else str(category))


def parse_answer(raw: str, allowed_categories: list[str] | None = None) -> dict:
    """Ответ модели → `{intent, category, answer, confidence}` по контракту WF-01."""
    value = (raw or "").strip()
    value = re.sub(r"^```[a-z]*\s*|\s*```$", "", value, flags=re.IGNORECASE).strip()
    lines = value.splitlines()
    token = re.sub(r"[^A-Z]", "", (lines[0] if lines else "").strip().upper())

    intent = TOKEN_MAP.get(token, "assistant")
    answer = ""
    category: str | None = None
    confidence: float | None = None

    if token in TOKEN_MAP:
        rest = "\n".join(lines[1:]).strip()
        match = _CATEGORY_RE.search(rest)
        if match:
            category = match.group(1).strip()
        conf = _CONFIDENCE_RE.search(rest)
        if conf:
            confidence = float(conf.group(1).replace(",", "."))
        answer = _CONFIDENCE_RE.sub("", _CATEGORY_RE.sub("", rest)).strip()
    else:
        answer = value           # модель ответила без маркера — считаем это текстом

    answer, json_intent, json_category = _unwrap_json(answer)
    if json_intent:
        intent = json_intent
    if json_category:
        category = json_category

    if category and category.strip().lower() == "none":
        category = None
    if category and category.strip() == FALLBACK_CATEGORY:
        # «Прочее» от модели — не ответ, а «не знаю»: идём к человеку (docs/21 §2)
        category = None
    if category and allowed_categories and category not in allowed_categories:
        category = None
    if category and confidence is not None and confidence < CONFIDENCE_THRESHOLD:
        category = None
    if intent == "assistant" and not answer.strip():
        answer = DEFAULT_ANSWER
    return {"intent": intent, "category": category,
            "answer": answer.strip(), "confidence": confidence}


async def ask(text: str, user: dict | None = None, categories: list[str] | None = None,
              *, transport: httpx.AsyncBaseTransport | None = None,
              api_key: str | None = None, model: str | None = None,
              timeout: float | None = None) -> dict:
    """Спросить модель один раз. Параметры-аргументы оставлены для тестов."""
    key = llm.api_key() if api_key is None else api_key
    if not key:
        raise LlmError("llm_unavailable")
    payload = {
        "model": model or llm.model(),
        "messages": [{"role": "system", "content": system_prompt(categories, user)},
                     {"role": "user", "content": text.strip()[:2000]}],
        # 0.2 и 700 — значения WF-01: менять их значит менять классификацию
        "temperature": 0.2,
        "max_tokens": 700,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout or llm.timeout(),
                                     transport=transport) as client:
            r = await client.post(llm.chat_url(),
                                  headers={"Authorization": f"Bearer {key}",
                                           "Content-Type": "application/json"},
                                  json=payload)
        if r.status_code >= 400:
            raise LlmError("llm_error")
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except LlmError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        # адаптер LLM: сеть и нечитаемый ответ провайдера — «не смогла», не поломка кода
        raise LlmError("llm_unavailable") from exc
    result = parse_answer(str(content), categories)
    usage = data.get("usage") or {}
    result.update(ok=True, model=payload["model"],
                  tokens=usage.get("total_tokens", 0),
                  tokens_in=usage.get("prompt_tokens", 0),
                  tokens_out=usage.get("completion_tokens", 0),
                  provider=llm.provider())
    return result
