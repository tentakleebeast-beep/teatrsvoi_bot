"""
Выбор человека по имени: от «кто это?» до однозначного ответа.

Правило студии: человек ищется по псевдониму, полному имени, только имени или
только фамилии — но интерфейс никогда не угадывает. Если под запрос подходят
двое, ответ «выберите», а не первый по алфавиту.

Уверенность считается по совпадению слов, а не по вкусу:
  * точное совпадение целого имени/ключа            — 100;
  * слово целиком («смирнова» в «Смирнова Дарья»)     — 60, плюс бонус за все слова;
  * начало слова («смирн» → «смирнова»)             — 40;
  * кусок внутри слова («мирн» → «смирнова»)        — 20;
  * совпадение по роли («Сэм») — отдельный, низший уровень: человек важнее роли.

«Решено» — только если лучший результат строго выше второго. Равенство — это
двое похожих людей, и решает человек.
"""

from __future__ import annotations

from .assign import name_stem

LEVEL_ROLE = 5           # совпадение только по роли — резерв, не человек
LEVEL_SUBSTRING = 20
LEVEL_STEM = 55          # «Витей» → «вит» = «Витя»: падеж не мешает узнать человека
LEVEL_PREFIX = 40
LEVEL_WORD = 60
LEVEL_FULL = 100

EXACT_BONUS = 40         # все слова запроса совпали целиком — «дарья смирнова»


def normalize(text: str | None) -> str:
    return (text or "").strip().lower().replace("ё", "е")


def words(text: str | None) -> list[str]:
    return [w for w in normalize(text).replace("-", " ").split() if w]


def _best_term(term: str, source: str) -> int:
    """Насколько одно слово запроса совпало с одной строкой-источником."""
    src = normalize(source)
    if not src or not term:
        return 0
    if term == src:
        return LEVEL_FULL
    best = 0
    stem = name_stem(term)
    for word in words(src):
        if word == term:
            best = max(best, LEVEL_WORD)
        elif stem and name_stem(word) == stem and len(stem) >= 3:
            best = max(best, LEVEL_STEM)
        elif word.startswith(term):
            best = max(best, LEVEL_PREFIX)
        elif term in word:
            best = max(best, LEVEL_SUBSTRING)
    return best


def score(query: str, person: dict) -> int:
    """Сумма по словам запроса: каждое слово должно найтись хотя бы в одном источнике."""
    terms = words(query)
    if not terms:
        return 0
    sources = [person.get("display_name"), person.get("person_key")]
    sources += list(person.get("aliases") or [])
    roles = list(person.get("role_aliases") or [])
    total = 0
    for term in terms:
        best = max((_best_term(term, src) for src in sources if src), default=0)
        if not best and roles:
            # роль нашлась, но это не человек: только резерв, когда людей нет
            best = max((_best_term(term, src) for src in roles if src), default=0)
            if best:
                best = LEVEL_ROLE
        if not best:
            return 0
        total += best
    if total >= LEVEL_WORD * len(terms):
        total += EXACT_BONUS
    return total


def rank(people: list[dict], query: str) -> list[dict]:
    """Подходящие люди, лучшие первыми; при равенстве — по имени."""
    scored = []
    for person in people:
        value = score(query, person)
        if value:
            scored.append({**person, "score": value})
    scored.sort(key=lambda p: (-p["score"], normalize(p.get("display_name"))))
    return scored


def resolve(people: list[dict], query: str, limit: int = 5) -> dict:
    """Итог выбора: найден / не найден / нужно выбрать из нескольких."""
    if not words(query):
        return {"status": "empty", "person": None, "candidates": [], "query": query}
    ranked = rank(people, query)
    if not ranked:
        return {"status": "not_found", "person": None, "candidates": [], "query": query}
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    candidates = [{k: v for k, v in p.items() if k != "score"} for p in ranked[:limit]]
    if second is not None and second["score"] == best["score"] and best["score"] > LEVEL_ROLE:
        return {"status": "ambiguous", "person": None, "candidates": candidates,
                "query": query}
    person = {k: v for k, v in best.items() if k != "score"}
    return {"status": "resolved", "person": person, "candidates": candidates,
            "query": query, "score": best["score"]}
