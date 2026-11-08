#!/usr/bin/env python3
"""Замер A-0: набор из 30 вопросов, метрика ответов, сравнение моделей-кандидатов.

Зачем. «Лучше / хуже» агентного режима решалось на глаз: одиночный вызов модели не
видит дату, расписание и бюджет, агентный цикл видит. Без общей линейки нельзя ни
выбрать модель, ни заметить, что стало хуже. Здесь линейка:

  * набор — `scripts/agent_eval_set.json` (30 реальных вопросов, у каждого класс и
    эталонные факты);
  * кандидаты — режим `single` (один вызов модели, как сейчас в `/tools/route`) и
    режим `agent` (цикл «модель → инструмент → модель», как в карте A-1);
  * метрика — доля адекватных ответов (факты совпали), доля честных отказов, доля
    неверных ответов, латентность p50/p95, число шагов и токенов.

Сверка идёт по ключевым фактам (дата, сумма, имя, фраза), а не по полному тексту:
два верных ответа разными словами должны считаться одинаково верными. Дата и день
недели у Q-01/Q-12 считаются от даты прогона (`{today_date}` / `{today_weekday}`,
`resolve_today()`), а при `--rescore` — от даты сохранённого прогона (`ran_at`).

Реестр инструментов агента живёт в `agently/registry.py`; сценарий стенда читает
только (`GET /tools/*`, `GET /folders`, `GET /files`).

Запуск:
    env -u PYTHONPATH .venv/bin/python scripts/agent_eval.py --check-set
    env -u PYTHONPATH .venv/bin/python scripts/agent_eval.py \
        --models openai/gpt-4.1-nano,openai/gpt-4o-mini --mode both
    env -u PYTHONPATH .venv/bin/python scripts/agent_eval.py --models ... --json out.json

Ключ провайдера берётся из `LLM_API_KEY` (`.env` или окружение) и нигде не печатается.
Коды выхода: 0 — замер выполнен, 1 — набор негоден, 2 — провайдер или tool-api недоступны.
Тест: `tests/agent/test_agent_eval_set.py`, `tests/agent/test_agent_eval.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from _cli import apply_env, env, parser  # noqa: E402  (путь к scripts добавляет сам запуск)

apply_env()

SET_PATH = Path(__file__).resolve().parent / "agent_eval_set.json"

# Цены OpenRouter, $ за 1M токенов (вход, выход), проверены 01.11.2026.
# Нужны только для колонки «сколько стоит 1000 сообщений»; метрики качества от них не зависят.
PRICES = {
    "openai/gpt-4.1-nano": (0.10, 0.40),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "openai/gpt-4o": (2.50, 10.00),
    "google/gemini-2.5-flash": (0.30, 2.50),
    "deepseek/deepseek-chat": (0.26, 1.03),
}

WEB_SEARCH_ENABLED = False   # флаг конфига агента (A-1 п.2): по умолчанию выключен
MAX_TOOL_RESULT = 4000       # сколько символов результата инструмента отдаём модели
DEFAULT_MAX_STEPS = 4        # лимит шагов из карты A-1
REFUSAL = re.compile(
    r"(не могу|не имею доступа|нет доступа|не вижу|нет данных|не знаю|не располагаю"
    r"|у меня нет|не в состоянии|не хватает данных|неизвестно|нет информации"
    r"|не получится посмотреть|не удаётся посмотреть|не доступ)",
    re.IGNORECASE)
CLARIFY = re.compile(r"(\?|уточн|какую категор|какая категор|выбер)", re.IGNORECASE)

SYSTEM_BASE = (
    "Ты — ассистент театра-студии «СВОИ» в Telegram. Отвечай по-русски, коротко и по делу, "
    "без служебных пометок и без Markdown-разметки."
)
SYSTEM_SINGLE = SYSTEM_BASE + (
    " У тебя нет инструментов: ты не видишь базу, календарь, бюджет, взносы и файлы. "
    "Не выдумывай даты, суммы, имена и события: если ответ требует данных, скажи прямо, "
    "что не можешь посмотреть."
)
SYSTEM_AGENT = SYSTEM_BASE + (
    " У тебя есть инструменты. Прежде чем назвать факт (дата, время, сумма, имя, событие, "
    "файл) — вызови подходящий инструмент и отвечай только по его результату. "
    "Если нужных данных нет в результатах инструментов, так и скажи: «этого я не вижу». "
    "Ничего не придумывай и не подставляй цифры из памяти."
)


# ------------------------------------------------------------------ набор
@dataclass(frozen=True)
class Question:
    id: str
    text: str
    klass: str
    source: str
    why: str
    expect: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    clarify: bool = False


CLASSES = ("deterministic", "tool", "web")
EXPECTS = ("answer", "refuse")

# Подстановки даты прогона. «Какое сегодня число» нельзя мерить константой: на
# следующий день честный ответ «2 ноября 2026» получал бы приговор wrong, и линейка
# занижала бы ровно тот класс, ради которого сделана.

# Стемы дней недели для сверки: «воскресенье», «в воскресенье», «воскресенья» — один факт.
WEEKDAY_STEMS = {
    "понедельник": "понедельник", "вторник": "вторник", "среда": "сред",
    "четверг": "четверг", "пятница": "пятниц", "суббота": "суббот",
    "воскресенье": "воскресен",
}


def _today_values(day: date) -> dict[str, str]:
    """Значения подстановок для конкретного дня."""
    return {"{today_date}": day.isoformat(),
            "{today_dmy}": day.strftime("%d.%m.%Y"),
            "{today_weekday}": WEEKDAYS[day.weekday()]}


def _substitute(value: Any, keys: dict[str, str]) -> Any:
    """Рекурсивно подставить дату во все строки набора (текст, why, факты)."""
    if isinstance(value, str):
        for key, text in keys.items():
            value = value.replace(key, text)
        return value
    if isinstance(value, list):
        return [_substitute(item, keys) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, keys) for key, item in value.items()}
    return value


def resolve_today(data: dict[str, Any], day: date | None = None) -> dict[str, Any]:
    """Набор с подставленной датой прогона вместо `{today_date}` / `{today_weekday}`.

    При обычном прогоне подставляется сегодняшний день, при `--rescore` — дата
    сохранённого прогона (`ran_at`): иначе старые приговоры пересчитывались бы по
    новой дате и переставали сходиться с отчётом.
    """
    return _substitute(data, _today_values(day or _now().date()))


def load_set(path: Path = SET_PATH) -> dict[str, Any]:
    """Набор как есть: словарь из JSON (проверку формы делает `problems()`)."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def problems(data: dict[str, Any]) -> list[str]:
    """Всё, что делает набор негодным: пустые поля, чужие классы, дубли, нет фактов."""
    errors: list[str] = []
    questions = data.get("questions") or []
    if len(questions) != 30:
        errors.append(f"вопросов {len(questions)}, нужно ровно 30")
    seen_ids: set[str] = set()
    seen_text: dict[str, str] = {}
    for item in questions:
        qid = str(item.get("id") or "")
        if not qid:
            errors.append("вопрос без id")
            continue
        if qid in seen_ids:
            errors.append(f"{qid}: повтор id")
        seen_ids.add(qid)
        text = " ".join(str(item.get("text") or "").lower().split())
        if not text:
            errors.append(f"{qid}: пустой текст")
        elif text in seen_text:
            errors.append(f"{qid}: повтор вопроса (совпадает с {seen_text[text]})")
        else:
            seen_text[text] = qid
        if item.get("class") not in CLASSES:
            errors.append(f"{qid}: класс {item.get('class')!r} вне {CLASSES}")
        if item.get("expect") not in EXPECTS:
            errors.append(f"{qid}: expect {item.get('expect')!r} вне {EXPECTS}")
        if not str(item.get("source") or "").strip():
            errors.append(f"{qid}: не указан источник")
        if not str(item.get("why") or "").strip():
            errors.append(f"{qid}: не указано, зачем вопрос в наборе")
        facts = item.get("facts")
        if not isinstance(facts, list):
            errors.append(f"{qid}: facts не список")
            facts = []
        for fact in facts:
            kind = fact.get("kind") if isinstance(fact, dict) else None
            if kind not in ("date", "time", "money", "phrase", "any", "weekday"):
                errors.append(f"{qid}: вид факта {kind!r} неизвестен")
            elif fact.get("value") in (None, "", []):
                errors.append(f"{qid}: пустое значение факта {kind}")
            elif "{today" in str(fact.get("value")):
                errors.append(f"{qid}: подстановка даты не сработала в факте {fact.get('value')!r}")
        # У вопросов класса «нужен инструмент» факты обязательны: иначе сверять нечего.
        if item.get("expect") == "answer" and not facts:
            errors.append(f"{qid}: нет эталонных фактов")
        if item.get("expect") == "refuse" and facts:
            errors.append(f"{qid}: у отказа не должно быть эталонных фактов")
    return errors


# ------------------------------------------------------------------ сверка фактов
def normalize(text: str) -> str:
    """Текст для сверки: нижний регистр, ё→е, единые пробелы, без неразрывных."""
    flat = (text or "").lower().replace("ё", "е").replace("\u00a0", " ")
    return " ".join(flat.split())


MONTHS = {
    1: ("январ",), 2: ("феврал",), 3: ("март",), 4: ("апрел",),
    5: ("ма",), 6: ("июн",), 7: ("июл",), 8: ("август",),
    9: ("сентябр",), 10: ("октябр",), 11: ("ноябр",), 12: ("декабр",),
}


def numbers_in(text: str) -> set[int]:
    """Все числа текста как целые: «210 998», «210998,00», «45 000 ₽» → 210998, 45000.

    Пробелы и запятые внутри числа считаются разделителями тысяч, только если после
    них ровно три цифры. Иначе «21.11» склеилось бы в 2111.
    """
    found: set[int] = set()
    for raw in re.findall(r"\d[\d\s.,\u00a0]*", normalize(text)):
        digits = raw.strip()
        for sep in (",", ".", "\u00a0", " "):
            # 45 000 → 45000 (ровно три знака после разделителя), 45 000,50 → 45000
            digits = re.sub(rf"(?<=\d){re.escape(sep)}(?=\d{{3}}(?!\d))", "", digits)
        digits = digits.rstrip(".,\u00a0 ")          # точка в конце предложения — не разделитель
        digits = re.sub(r"([.,]\d+)$", "", digits)
        digits = re.sub(r"\D", "", digits)
        if digits:
            found.add(int(digits))
    return found


def date_forms(iso: str) -> list[re.Pattern[str]]:
    """Регулярки верных записей даты `2026-11-01`: 01.11.2026, 1 ноября 2026, 01.11.26.

    Месяц берётся по стему («ноябр»), чтобы «1 ноября 2026» и «1 ноябрь 2026» считались
    одной датой: сверяем факт, а не падеж.
    """
    day = datetime.strptime(iso, "%Y-%m-%d")
    stem = MONTHS[day.month][0]
    return [
        re.compile(rf"\b0?{day.day}\.0?{day.month}\.{day.year}\b"),
        re.compile(rf"\b0?{day.day}\.0?{day.month}\.{str(day.year)[2:]}\b"),
        re.compile(rf"\b{day.day}\s+{stem}\w*\s+{day.year}\b"),
        # Год часто опускают: «28 октября занят» — это та же дата, если месяц совпал.
        re.compile(rf"\b{day.day}\s+{stem}\w*\b"),
    ]


def time_forms(value: str) -> list[re.Pattern[str]]:
    """Регулярки верных записей времени: `15:00` → «15:00», «15.00», «15 часов», «с 15».

    Последняя форма нужна, потому что ответ часто повторяет вопрос словами
    («28 октября с 10 до 14 свободен») и это тот же факт, а не ошибка.
    """
    hour, _minute = value.split(":")
    bare = int(hour)
    return [
        re.compile(rf"\b0?{bare}[:.]00\b"),
        re.compile(rf"\b0?{bare}\s*час\w*"),
        re.compile(rf"\b(?:с|до|в|к|после)\s+0?{bare}\b"),
    ]


def fact_matches(fact: dict[str, Any], text: str) -> bool:
    """Совпал ли один эталонный факт с текстом ответа."""
    kind = fact.get("kind")
    value = fact.get("value")
    flat = normalize(text)
    if kind == "money":
        try:
            target = int(str(value))
        except (TypeError, ValueError):
            return False
        return target in numbers_in(text)
    if kind == "date":
        if any(form.search(flat) for form in date_forms(str(value))):
            return True
        day = datetime.strptime(str(value), "%Y-%m-%d")
        return f"{day.day:02d}.{day.month:02d}" in flat
    if kind == "time":
        return any(form.search(flat) for form in time_forms(str(value)))
    if kind == "phrase":
        return normalize(str(value)) in flat
    if kind == "weekday":
        stem = WEEKDAY_STEMS.get(normalize(str(value)), "")
        return bool(stem) and stem in flat
    if kind == "any":
        return any(normalize(str(alt)) in flat for alt in value or [])
    return False


def weekday_conflict(question: Question, flat: str) -> str:
    """Другой день недели в ответе: «сегодня понедельник» вместо верного дня.

    Списком запрещённых значений это не выразить: день недели меняется каждый день,
    и статический список назавтра запретил бы верный ответ.
    """
    expected = {WEEKDAY_STEMS.get(normalize(str(fact.get("value"))), "")
                for fact in question.facts if fact.get("kind") == "weekday"}
    if not expected:
        return ""
    for name, stem in WEEKDAY_STEMS.items():
        if stem in flat and stem not in expected:
            return name
    return ""


def score(question: Question, answer: str) -> tuple[str, list[str]]:
    """Приговор ответу: `adequate`, `refused` или `wrong` (плюс пояснения).

    * adequate — все эталонные факты на месте (для вопроса-отказа — отказ на месте);
    * refused — фактов нет, но и выдумки нет: модель сказала, что не видит данных;
    * wrong — ответ содержит неверный факт, запрещённое значение или выдумку вместо отказа.
    """
    notes: list[str] = []
    flat = normalize(answer)
    other_day = weekday_conflict(question, flat)
    if other_day:
        return "wrong", [f"назван другой день недели: {other_day}"]
    missing = [fact.get("value") for fact in question.facts if not fact_matches(fact, answer)]
    if question.expect == "refuse":
        if REFUSAL.search(answer):
            return "adequate", ["отказ — эталон для класса «нужен веб»"]
        return "wrong", ["инструмента нет, а отказа в ответе нет: похоже на выдумку"]
    if not missing:
        if question.clarify and not CLARIFY.search(answer):
            return "wrong", ["сумма есть, но уточнения категории нет"]
        for word in question.forbidden:
            if normalize(word) in flat:
                return "wrong", [f"запрещённое значение в ответе: {word}"]
        return "adequate", notes
    refused = bool(REFUSAL.search(answer))
    if refused:
        return "refused", [f"нет фактов {missing}"]
    return "wrong", [f"нет фактов {missing} и нет отказа"]


# ------------------------------------------------------------------ инструменты (A-2)
def _api(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET в tool-api от имени админа: только чтение, с сервисным токеном."""
    base = (env("TOOL_API_URL") or "http://127.0.0.1:8000").rstrip("/")
    headers = {"Authorization": "Bearer " + env("TOOL_API_TOKEN"),
               "X-Actor-Chat-Id": env("ADMIN_CHAT_ID")}
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(base + path, params=params or {}, headers=headers)
    if resp.status_code != 200:
        return {"ok": False, "error": f"http_{resp.status_code}", "path": path}
    return resp.json()


def _now() -> datetime:
    """Время студии: смещение из настроек, как у сервиса (`TZ_OFFSET_HOURS`)."""
    try:
        offset = float(env("TZ_OFFSET_HOURS", "4") or 4)
    except ValueError:
        offset = 4.0
    return datetime.now(timezone(timedelta(hours=offset)))


WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def tool_now(_args: dict[str, Any]) -> dict[str, Any]:
    today = _now()
    offset = (today.utcoffset() or timedelta(0)).total_seconds() / 3600
    return {"ok": True, "date": today.strftime("%Y-%m-%d"),
            "weekday": WEEKDAYS[today.weekday()], "time": today.strftime("%H:%M"),
            "tz": f"UTC{offset:+g}"}


def tool_read_schedule(args: dict[str, Any]) -> dict[str, Any]:
    today = _now().date()
    start = str(args.get("from") or today.isoformat())
    end = str(args.get("to") or (today + timedelta(days=7)).isoformat())
    data = _api("/tools/calendar/events", {"from": start, "to": end})
    if isinstance(data, dict) and data.get("ok"):
        events = [{"title": item.get("title"), "start": item.get("start"),
                   "end": item.get("end"), "status": item.get("status")}
                  for item in data.get("data") or []]
        return {"ok": True, "from": start, "to": end, "count": len(events), "events": events}
    return {"ok": False, "error": "calendar_unavailable", "detail": str(data)[:200]}


def tool_read_availability(_args: dict[str, Any]) -> dict[str, Any]:
    data = _api("/tools/availability/week")
    if isinstance(data, dict) and data.get("ok"):
        days = [{"day": item.get("day"), "status": item.get("status"),
                 "from": item.get("from"), "to": item.get("to")}
                for item in data.get("days") or []]
        return {"ok": True, "start": data.get("start"), "title": data.get("title"), "days": days}
    return {"ok": False, "error": "availability_unavailable", "detail": str(data)[:200]}


def tool_read_budget(args: dict[str, Any]) -> dict[str, Any]:
    data = _api("/tools/budget/summary")
    if isinstance(data, dict) and data.get("ok"):
        periods = data.get("periods") or []
        period = str(args.get("period") or "")
        if period:
            periods = [item for item in periods if item.get("period") == period]
        return {"ok": True, "periods": periods, "income_total": data.get("income_total"),
                "expense_total": data.get("expense_total"), "balance": data.get("balance")}
    return {"ok": False, "error": "budget_unavailable", "detail": str(data)[:200]}


def tool_read_dues(_args: dict[str, Any]) -> dict[str, Any]:
    data = _api("/tools/dues/me")
    if isinstance(data, dict) and data.get("ok"):
        return {"ok": True, "charges": data.get("data") or [], "current": data.get("current"),
                "debt": data.get("debt"), "tariff": data.get("tariff")}
    return {"ok": False, "error": "dues_unavailable", "detail": str(data)[:200]}


def tool_list_files(args: dict[str, Any]) -> dict[str, Any]:
    folders = _api("/folders")
    files = _api("/files")
    if not (isinstance(folders, dict) and folders.get("ok")):
        return {"ok": False, "error": "files_unavailable", "detail": str(folders)[:200]}
    tree = [item.get("path") for item in folders.get("data") or []]
    query = normalize(str(args.get("query") or ""))
    if query:
        tree = [path for path in tree if query in normalize(str(path))]
    names = [item.get("name") for item in (files.get("data") or [])] \
        if isinstance(files, dict) else []
    if query:
        names = [name for name in names if query in normalize(str(name))]
    return {"ok": True, "folders": tree[:60], "files": names[:60],
            "files_count": len(names)}


def tool_web_search(args: dict[str, Any]) -> dict[str, Any]:
    if not WEB_SEARCH_ENABLED:
        return {"ok": False, "error": "web_search_disabled",
                "message": "веб-поиск выключен в конфиге агента: данных из интернета нет"}
    return {"ok": False, "error": "web_search_not_implemented",
            "message": f"поиск «{args.get('query')}» недоступен в замере"}


TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "now",
        "description": "Текущая дата, день недели, время и часовой пояс студии (Самара).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_schedule",
        "description": "События календаря студии за интервал: репетиции, аренда, показы.",
        "parameters": {"type": "object", "properties": {
            "from": {"type": "string", "description": "начало, YYYY-MM-DD"},
            "to": {"type": "string", "description": "конец, YYYY-MM-DD"}}}}},
    {"type": "function", "function": {
        "name": "read_availability",
        "description": "Занятость спрашивающего на текущую неделю: день, статус, часы.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_budget",
        "description": "Доходы, расходы и итог бюджета по месяцам (YYYY-MM).",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "description": "месяц YYYY-MM, необязательно"}}}}},
    {"type": "function", "function": {
        "name": "read_dues",
        "description": "Взносы спрашивающего: начисления, оплаты, текущий месяц, тариф.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "Папки и файлы хранилища студии (только имена и пути).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "слово для фильтра, необязательно"}}}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Поиск в интернете: курсы, погода, внешние цены и новости.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
]

IMPLEMENTATIONS = {
    "now": tool_now,
    "read_schedule": tool_read_schedule,
    "read_availability": tool_read_availability,
    "read_budget": tool_read_budget,
    "read_dues": tool_read_dues,
    "list_files": tool_list_files,
    "web_search": tool_web_search,
}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Выполнить инструмент по имени; неизвестное имя — ошибка, а не падение замера."""
    impl = IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"ok": False, "error": "unknown_tool", "tool": name}
    try:
        return impl(args or {})
    except Exception as exc:  # noqa: BLE001 — сбой инструмента не должен ронять весь прогон
        return {"ok": False, "error": "tool_failed", "tool": name, "detail": str(exc)[:200]}


# ------------------------------------------------------------------ провайдер
class ModelError(RuntimeError):
    """Модель недоступна: нет ключа, квота, сеть или не-200 от провайдера."""


@dataclass
class Completion:
    message: dict[str, Any]
    tokens_in: int
    tokens_out: int


def chat(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
         timeout: float | None = None) -> Completion:
    """Один вызов OpenAI-совместимого провайдера (OpenRouter, ProxyAPI, vsegpt)."""
    key = env("LLM_API_KEY") or env("OPENROUTER_API_KEY")
    if not key:
        raise ModelError("LLM_API_KEY не задан: замер без ключа провайдера невозможен")
    base = (env("LLM_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0,
                            "max_tokens": 700}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    limit = timeout if timeout is not None else float(env("LLM_TIMEOUT_SECONDS", "60") or 60)
    try:
        with httpx.Client(timeout=limit) as client:
            resp = client.post(url, json=body, headers={
                "Authorization": "Bearer " + key,
                "HTTP-Referer": "https://github.com/teatrsvoi",
                "X-Title": "teatrsvoi agent eval"})
    except httpx.HTTPError as exc:
        raise ModelError(f"провайдер недоступен: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise ModelError(f"провайдер ответил {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise ModelError(f"ответ провайдера без choices: {str(data)[:200]}") from exc
    usage = data.get("usage") or {}
    return Completion(message=message, tokens_in=int(usage.get("prompt_tokens") or 0),
                      tokens_out=int(usage.get("completion_tokens") or 0))


# ------------------------------------------------------------------ прогон
@dataclass
class Answer:
    question: str
    verdict: str
    notes: list[str]
    text: str
    latency: float
    steps: int
    tokens_in: int
    tokens_out: int
    tools_used: list[str] = field(default_factory=list)
    why: str = ""          # зачем вопрос в наборе: печатается рядом с провалом


def run_single(model: str, question: Question) -> Answer:
    """Кандидат «один вызов модели», как сегодня: без инструментов и без данных."""
    started = datetime.now()
    messages = [{"role": "system", "content": SYSTEM_SINGLE},
                {"role": "user", "content": question.text}]
    completion = chat(model, messages)
    text = str(completion.message.get("content") or "")
    verdict, notes = score(question, text)
    return Answer(question=question.id, verdict=verdict, notes=notes, text=text,
                  latency=(datetime.now() - started).total_seconds(), steps=1,
                  tokens_in=completion.tokens_in, tokens_out=completion.tokens_out,
                  why=question.why)


def run_agent(model: str, question: Question, max_steps: int = DEFAULT_MAX_STEPS) -> Answer:
    """Кандидат «агентный цикл»: модель → инструмент → модель, не больше `max_steps`."""
    started = datetime.now()
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_AGENT},
                                      {"role": "user", "content": question.text}]
    steps = 0
    tokens_in = tokens_out = 0
    used: list[str] = []
    text = ""
    while steps < max_steps:
        steps += 1
        completion = chat(model, messages, tools=TOOLS)
        tokens_in += completion.tokens_in
        tokens_out += completion.tokens_out
        message = completion.message
        calls = message.get("tool_calls") or []
        if not calls:
            text = str(message.get("content") or "")
            break
        messages.append({"role": "assistant",
                         "content": message.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            function = (call.get("function") or {})
            name = str(function.get("name") or "")
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            used.append(name)
            result = call_tool(name, args)
            payload = json.dumps(result, ensure_ascii=False)[:MAX_TOOL_RESULT]
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": payload})
    if not text:
        text = "не разобрался: лимит шагов исчерпан"
    verdict, notes = score(question, text)
    return Answer(question=question.id, verdict=verdict, notes=notes, text=text,
                  latency=(datetime.now() - started).total_seconds(), steps=steps,
                  tokens_in=tokens_in, tokens_out=tokens_out, tools_used=used,
                  why=question.why)


def percentile(values: list[float], share: float) -> float:
    """p50/p95 без numpy: ближайший ранг по отсортированному списку."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


@dataclass
class Summary:
    model: str
    mode: str
    answers: list[Answer]

    @property
    def count(self) -> int:
        return len(self.answers)

    def share(self, verdict: str) -> float:
        if not self.count:
            return 0.0
        return sum(1 for item in self.answers if item.verdict == verdict) / self.count

    @property
    def steps(self) -> float:
        return statistics.fmean(item.steps for item in self.answers) if self.answers else 0.0

    @property
    def latency(self) -> list[float]:
        return [item.latency for item in self.answers]

    @property
    def tokens(self) -> float:
        if not self.count:
            return 0.0
        return statistics.fmean(item.tokens_in + item.tokens_out for item in self.answers)

    @property
    def cost_per_1000(self) -> float | None:
        """Сколько долларов стоит 1000 таких сообщений — по среднему расходу токенов."""
        price = PRICES.get(self.model)
        if price is None or not self.answers:
            return None
        tokens_in = statistics.fmean(item.tokens_in for item in self.answers)
        tokens_out = statistics.fmean(item.tokens_out for item in self.answers)
        return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000 * 1000


def evaluate(model: str, mode: str, questions: list[Question],
             max_steps: int = DEFAULT_MAX_STEPS) -> Summary:
    """Прогнать весь набор одной моделью в одном режиме."""
    answers: list[Answer] = []
    for question in questions:
        if mode == "single":
            answers.append(run_single(model, question))
        else:
            answers.append(run_agent(model, question, max_steps))
    return Summary(model=model, mode=mode, answers=answers)


# ------------------------------------------------------------------ вывод
HEADER = ("| модель | режим | шагов | адекватно | отказ | неверно | p50, с | p95, с "
          "| токенов | $/1000 |")
ALIGN = "|---|---|---|---|---|---|---|---|---|---|"


def table(rows: list[Summary]) -> str:
    """Таблица «модель × метрика» — то, по чему выбирают модель и число шагов."""
    lines = [HEADER, ALIGN]
    for row in rows:
        cost = row.cost_per_1000
        lines.append(
            f"| {row.model} | {row.mode} | {row.steps:.1f} "
            f"| {row.share('adequate') * 100:.0f}% | {row.share('refused') * 100:.0f}% "
            f"| {row.share('wrong') * 100:.0f}% "
            f"| {percentile(row.latency, 0.5):.1f} | {percentile(row.latency, 0.95):.1f} "
            f"| {row.tokens:.0f} | {('—' if cost is None else f'{cost:.3f}')} |")
    return "\n".join(lines)


def by_class(rows: list[Summary], data: dict[str, Any]) -> str:
    """Разрез по классам вопросов: где именно кандидат проваливается."""
    klass = {item["id"]: item["class"] for item in data["questions"]}
    lines = ["| модель | режим | класс | адекватно |", "|---|---|---|---|"]
    for row in rows:
        for name in CLASSES:
            subset = [item for item in row.answers if klass.get(item.question) == name]
            if not subset:
                continue
            good = sum(1 for item in subset if item.verdict == "adequate")
            lines.append(f"| {row.model} | {row.mode} | {name} | "
                         f"{good}/{len(subset)} ({good / len(subset) * 100:.0f}%) |")
    return "\n".join(lines)


def failures(row: Summary, limit: int = 6) -> str:
    """Что именно пошло не так — по одному примеру на проблему, без полного лога."""
    lines = []
    for answer in row.answers:
        if answer.verdict == "adequate":
            continue
        if len(lines) >= limit:
            break
        text = " ".join(answer.text.split())[:160]
        goal = f" [{answer.why}]" if answer.why else ""
        lines.append(f"- {answer.question}{goal}: {answer.verdict} — {text}")
    return "\n".join(lines) or "- нет"


def as_json(rows: list[Summary], ran_at: str = "") -> dict[str, Any]:
    """Подробный результат: ответы по каждому вопросу, чтобы разбор шёл по фактам.

    `ran_at` — дата прогона: по ней `--rescore` подставляет `{today_*}` в набор,
    иначе пересчёт по старому файлу считался бы по сегодняшней дате.
    """
    return {"ran_at": ran_at or _now().date().isoformat(), "runs": [
        {"model": row.model, "mode": row.mode,
         "metrics": {"count": row.count, "adequate": row.share("adequate"),
                     "refused": row.share("refused"), "wrong": row.share("wrong"),
                     "steps_avg": row.steps, "p50": percentile(row.latency, 0.5),
                     "p95": percentile(row.latency, 0.95), "tokens_avg": row.tokens},
         "answers": [{"question": item.question, "verdict": item.verdict,
                      "notes": item.notes, "text": item.text,
                      "latency": round(item.latency, 2), "steps": item.steps,
                      "tokens_in": item.tokens_in, "tokens_out": item.tokens_out,
                      "tools": item.tools_used} for item in row.answers]}
        for row in rows]}


def from_json(path: Path, data: dict[str, Any]) -> list[Summary]:
    """Пересчитать приговоры по сохранённым ответам: правка сверки не стоит новых денег.

    Для этого и хранится полный текст ответов: если правило сверки уточнили (например,
    стали принимать дату без года), метрики пересчитываются по тем же ответам, а не
    получаются новым прогоном — иначе сравнивать «до/после» правки нельзя.
    """
    known = {item["id"]: item for item in data["questions"]}
    rows: list[Summary] = []
    for run in json.loads(path.read_text(encoding="utf-8"))["runs"]:
        answers: list[Answer] = []
        for stored in run["answers"]:
            source = known.get(stored["question"])
            if source is None:
                continue
            question = Question(id=source["id"], text=source["text"], klass=source["class"],
                                source=source.get("source", ""), why=source.get("why", ""),
                                expect=source.get("expect", "answer"),
                                facts=source.get("facts") or [],
                                forbidden=source.get("forbidden") or [],
                                clarify=bool(source.get("clarify")))
            verdict, notes = score(question, stored["text"])
            answers.append(Answer(question=question.id, verdict=verdict, notes=notes,
                                  text=stored["text"], latency=float(stored["latency"]),
                                  steps=int(stored["steps"]),
                                  tokens_in=int(stored.get("tokens_in") or 0),
                                  tokens_out=int(stored.get("tokens_out") or 0),
                                  tools_used=stored.get("tools") or [],
                                  why=question.why))
        rows.append(Summary(model=run["model"], mode=run["mode"], answers=answers))
    return rows


def report(rows: list[Summary], data: dict[str, Any], print_answers: bool = False) -> None:
    """Печать результата: таблица «модель × метрика», разрез по классам, примеры провалов."""
    print(table(rows))
    print("\nРазрез по классам вопросов:\n")
    print(by_class(rows, data))
    for row in rows:
        bad = failures(row)
        if bad != "- нет":
            print(f"\nГде не совпало — {row.model} / {row.mode}:\n{bad}")
    if print_answers:
        for row in rows:
            print(f"\n## {row.model} / {row.mode}")
            for answer in row.answers:
                print(f"- {answer.question} [{answer.verdict}]: "
                      f"{' '.join(answer.text.split())[:300]}")


# ------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    ap = parser(__doc__ or "")
    ap.add_argument("--set", default=str(SET_PATH), help="файл набора (JSON)")
    ap.add_argument("--check-set", action="store_true",
                    help="только проверить набор и выйти (без сети и без ключа)")
    ap.add_argument("--models", default="openai/gpt-4.1-nano,openai/gpt-4o-mini",
                    help="модели через запятую (минимум две, одна дешёвая)")
    ap.add_argument("--mode", choices=("single", "agent", "both"), default="both",
                    help="кандидат: один вызов модели, агентный цикл или оба")
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                    help=f"лимит шагов агентного цикла (по умолчанию {DEFAULT_MAX_STEPS})")
    ap.add_argument("--ids", default="", help="прогнать только эти id через запятую")
    ap.add_argument("--json", dest="json_path", default="",
                    help="куда сохранить подробный результат (JSON)")
    ap.add_argument("--rescore", default="",
                    help="пересчитать метрики по сохранённому JSON (без вызовов модели)")
    ap.add_argument("--print-answers", action="store_true",
                    help="печатать ответы по каждому вопросу (для разбора)")
    return ap


def selected_questions(data: dict[str, Any], ids: str) -> list[Question]:
    """Вопросы набора как объекты; `--ids` сужает прогон, не меняя самого набора."""
    wanted = {item.strip() for item in ids.split(",") if item.strip()}
    result = []
    for item in data["questions"]:
        if wanted and item["id"] not in wanted:
            continue
        result.append(Question(id=item["id"], text=item["text"], klass=item["class"],
                               source=item.get("source", ""), why=item.get("why", ""),
                               expect=item.get("expect", "answer"),
                               facts=item.get("facts") or [],
                               forbidden=item.get("forbidden") or [],
                               clarify=bool(item.get("clarify"))))
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stored: dict[str, Any] = {}
    ran_at = ""
    if args.rescore:
        # Дата прогона берётся из самого файла: пересчёт обязан судить по тем же фактам,
        # что и в день замера, иначе «до/после» правки сверки не сравнимо.
        stored = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        ran_at = str(stored.get("ran_at") or "")
    day = date.fromisoformat(ran_at) if ran_at else None
    data = resolve_today(load_set(Path(args.set)), day)
    errors = problems(data)
    if errors:
        print("Набор негоден:", file=sys.stderr)
        for error in errors:
            print("  -", error, file=sys.stderr)
        return 1
    questions = selected_questions(data, args.ids)
    if args.check_set:
        applied = _today_values(day or _now().date())["{today_date}"]
        print(f"Набор годен: {len(questions)} вопросов, "
              f"{len({item.klass for item in questions})} класса, "
              f"фактов {sum(len(item.facts) for item in questions)}, "
              f"дата подставлена {applied}.")
        return 0
    if args.rescore:
        rows = from_json(Path(args.rescore), data)
        print(f"Пересчёт по сохранённым ответам: {args.rescore}\n")
        report(rows, data, args.print_answers)
        if args.json_path:
            path = Path(args.json_path)
            path.write_text(json.dumps(as_json(rows, ran_at), ensure_ascii=False, indent=1),
                            encoding="utf-8")
            print(f"\nПриговоры перезаписаны: {path}")
        return 0
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models:
        print("Нужна хотя бы одна модель", file=sys.stderr)
        return 2
    modes = ("single", "agent") if args.mode == "both" else (args.mode,)
    rows: list[Summary] = []
    started = datetime.now()
    for model in models:
        for mode in modes:
            print(f"… {model} / {mode}", file=sys.stderr)
            try:
                rows.append(evaluate(model, mode, questions, args.max_steps))
            except ModelError as exc:
                print(f"Замер остановлен: {exc}", file=sys.stderr)
                return 2
    print(f"\nЗамер A-0: {len(questions)} вопросов × {len(rows)} прогонов, "
          f"{(datetime.now() - started).total_seconds():.0f} с\n")
    report(rows, data, args.print_answers)
    if args.json_path:
        path = Path(args.json_path)
        path.write_text(json.dumps(as_json(rows), ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"\nПодробный результат: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
