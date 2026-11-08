"""Разбор ответов знакомства моделью: закрытый набор полей (обращение №17, часть 2).

Правила `services/tg-gateway/app/intro.py` понимают «40», «полчаса», «час», но
пасуют перед живой речью: «добираюсь минут двадцать на трамвае», «зовите Витей,
только не Витей Ивановичем». Здесь модель получает ровно три поля профиля
(`alias_add`, `travel_minutes`, `ai_manifest`) и не может записать ничего другого:
ответ вне схемы отбрасывается, значения проверяются повторно на нашей стороне
(та же дисциплина, что в docs/21: модель не придумывает данных).

Порядок каскада обратный привычному: сначала правила (детерминированы и дешевле),
модель — только когда правила не поняли. Нет ключа, квота или сбой провайдера —
бот переспрашивает правилами, знакомство не ломается.
"""

from __future__ import annotations

import json
import re

import httpx

from . import llm

STEPS = ("alias", "travel", "manifest")

# Шаг → единственное поле, которое разрешено вернуть.
FIELD_OF = {"alias": "alias_add", "travel": "travel_minutes", "manifest": "ai_manifest"}

SYSTEM = (
    "Ты разбираешь один ответ человека в сценарии знакомства театра-студии. "
    "Отвечай ТОЛЬКО JSON-объектом без пояснений, без markdown, без ```."
)

INSTRUCTIONS = {
    "alias": (
        "Вопрос был: как человека называть в боте коротко. "
        "Верни {\"alias_add\": \"<одно слово>\"} — короткое имя из ответа, "
        "без служебных слов («меня зовут», «зовите»). Если имени нет — {\"alias_add\": null}."
    ),
    "travel": (
        "Вопрос был: сколько минут занимает дорога до студии. "
        "Верни {\"travel_minutes\": <целое число минут>}. «Полчаса» — 30, «час» — 60, "
        "«полтора часа» — 90. Больше 240 или непонятно — {\"travel_minutes\": null}."
    ),
    "manifest": (
        "Вопрос был: что помощнику важно знать про человека. "
        "Верни {\"ai_manifest\": \"<фраза своими словами, до 600 знаков>\"}, "
        "не добавляя того, чего человек не говорил. Нет содержания — {\"ai_manifest\": null}."
    ),
}

_NONE_WORDS = {"", "null", "none", "нет", "ничего", "-", "—", "не знаю"}


class LlmError(Exception):
    """Сбой LLM-ветки: код для gateway (`llm_unavailable`, `llm_error`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_messages(step: str, text: str) -> list[dict]:
    """Сообщения для модели: инструкция шага + закрытая схема ответа."""
    instruction = INSTRUCTIONS.get(step, "")
    user = (f"{instruction}\n\nОтвет человека: {text.strip()[:1000]}")
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": user}]


def _extract_json(raw: str) -> dict | None:
    value = (raw or "").strip()
    value = re.sub(r"^```[a-z]*\s*|\s*```$", "", value).strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(value[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def parse_reply(step: str, raw: str) -> dict:
    """Ответ модели → тело `POST /tools/profile/me`; всё лишнее отбрасывается."""
    field = FIELD_OF.get(step)
    data = _extract_json(raw)
    if not field or data is None:
        return {}
    value = data.get(field)
    return clean(step, value)


def clean(step: str, value: object) -> dict:
    if step == "alias":
        if not isinstance(value, str):
            return {}
        word = " ".join(value.split()).strip(" .,!?;:—-\"'«»()")
        word = word.split()[0] if word.split() else ""
        if word.lower() in _NONE_WORDS or not 2 <= len(word) <= 40:
            return {}
        return {"alias_add": word}
    if step == "travel":
        if isinstance(value, bool):
            return {}
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if not match:
                return {}
            value = int(match.group(0))
        if not isinstance(value, int) or not 0 <= value <= 240:
            return {}
        return {"travel_minutes": value}
    if step == "manifest":
        if not isinstance(value, str):
            return {}
        note = " ".join(value.split())[:600].rstrip()
        if note.lower() in _NONE_WORDS:
            return {}
        return {"ai_manifest": note} if note else {}
    return {}


async def ask(step: str, text: str, *, transport: httpx.AsyncBaseTransport | None = None,
              api_key: str | None = None, model: str | None = None,
              timeout: float | None = None) -> dict:
    """Спросить модель; пустой словарь — «не поняла», LlmError — «не смогла».

    Адрес, ключ, модель и таймаут берутся из `app/llm.py` (то есть из `.env`):
    провайдер меняется переменной, а не правкой кода. Параметры аргументами
    оставлены для тестов — они подменяют ключ и транспорт.
    """
    key = llm.api_key() if api_key is None else api_key
    if not key:
        raise LlmError("llm_unavailable")
    if timeout is None:
        timeout = llm.timeout(llm.DEFAULT_INTRO_TIMEOUT)
    payload = {
        "model": model or llm.model(),
        "messages": build_messages(step, text),
        "temperature": 0,
        "max_tokens": 200,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
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
    except (httpx.HTTPError, ValueError) as exc:
        # адаптер LLM: сеть и нечитаемый JSON — «не смогла», а не поломка кода
        raise LlmError("llm_unavailable") from exc
    return parse_reply(step, str(content))
