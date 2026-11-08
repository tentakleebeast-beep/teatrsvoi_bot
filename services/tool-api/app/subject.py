"""
Разбор субъекта запроса к расписанию (docs/architecture/41).

Человек пишет: «найди окна для Наташи и Сэма в Жизни насекомых».
Наташа и Сэм — роли в спектакле, за ними стоят Орлова и Новиков.
Ещё бывает «найдём окна для всех» и «когда больше всего свободных».

Задача модуля — превратить фразу в структуру:
    {
      "mode": "subset" | "all" | "max_free",
      "participant_ids": [...],
      "project_ids": [...],
      "unresolved": ["..."],       # что не удалось сопоставить
      "confidence": 0.0..1.0,
    }

Правило то же, что в каскаде категорий (docs/development/21): решают правила, модель
подключается только если правила не уверены, и выбирает из закрытого списка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- ключевые слова ---------------------------------------------------------

MAX_FREE_RE = re.compile(
    r"(больше\s+всех|максимум\s+свободн|как\s+можно\s+больше\s+(людей|участников)|"
    r"побольше\s+людей|для\s+тренинг|тренинг\s+для|сбор\s+всей|всем\s+составом)")
ALL_RE = re.compile(r"(для\s+всех|вся\s+труппа|всей\s+трупп|все\s+участники|"
                    r"кто\s+свободен\s+из\s+всех)")


@dataclass
class Person:
    id: int
    name: str
    aliases: list[tuple[str, str, int | None]] = field(default_factory=list)
    # (alias, alias_kind, project_id)


@dataclass
class Project:
    id: int
    name: str


@dataclass
class Subject:
    mode: str = "all"
    participant_ids: list[int] = field(default_factory=list)
    project_ids: list[int] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    confidence: float = 0.0
    hint: str = ""


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^а-яa-z0-9\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()




def _tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) > 1]


def _close(left: str, right: str, prefix: int = 4) -> bool:
    """
    Совпадение с учётом падежей: «Наташи» ≈ «Наташа», «Сэма» ≈ «Сэм»,
    «Чёрной пурге» ≈ «Чёрная пурга». Сравниваем начало слова: русские окончания
    меняются, основа — нет.
    """
    left, right = (left or "").lower(), (right or "").lower()
    if not left or not right:
        return False
    shortest, longest = sorted((len(left), len(right)))
    if shortest < 3:
        return left == right
    # «Саша»/«Саши» — разница только в окончании, а «Марк»/«Марата» — разные имена
    if longest - shortest > 1:
        return right.startswith(left) if shortest == len(left) else left.startswith(right)
    size = 3 if shortest <= 4 else prefix
    return left[:size] == right[:size]


def _contains_phrase(words: list[str], phrase: str) -> bool:
    """Все значимые слова фразы встречаются в тексте (с учётом падежей)."""
    parts = [t for t in _norm(phrase).split() if len(t) > 2]
    if not parts:
        return False
    return all(any(_close(word, part) for word in words) for part in parts)


def find_projects(text: str, projects: list[Project]) -> list[Project]:
    """Проект упомянут, если в тексте есть все значимые слова его названия."""
    words = _tokens(text)
    return [project for project in projects
            if _contains_phrase(words, project.name)
            or _contains_phrase(words, project.name.split(" ")[-1])]


def find_people(text: str, people: list[Person],
                project_ids: list[int] | None = None) -> tuple[list[Person], list[str]]:
    """
    Ищет людей по имени, короткому имени и роли.

    Если указан проект, роли других проектов не рассматриваются: «Сэм» в одном
    спектакле и «Сэм» в другом — разные люди.
    """
    words = _tokens(text)
    scored: dict[int, int] = {}
    unmatched: list[str] = []
    project_ids = project_ids or []

    for person in people:
        best = 0
        for alias, kind, project_id in person.aliases:
            if kind == "role" and project_ids and project_id not in project_ids:
                continue
            if _contains_phrase(words, alias):
                # роль и короткое имя весят больше, чем совпадение по фамилии
                weight = 3 if kind == "role" else 2
                best = max(best, weight)
        for part in _tokens(person.name):
            if len(part) > 3 and any(_close(word, part) for word in words):
                best = max(best, 1)
        if best:
            scored[person.id] = best

    for token in _tokens(text):
        if len(token) < 4:
            continue
        if any(_close(token, part) for person in people for part in _tokens(person.name)):
            continue
        if any(_close(token, part) for person in people
               for alias, _, _ in person.aliases for part in _tokens(alias)):
            continue
        if token in {"окна", "окно", "найди", "найти", "когда", "свободен", "свободны",
                     "свободна", "вечер", "вечером", "неделя", "неделю", "часа", "час",
                     "дня", "день", "для", "всех", "всей", "труппы", "тренинг", "сбор",
                     "жизни", "насекомых", "показ", "репетиция", "репетицию"}:
            continue
        unmatched.append(token)

    ordered = [pid for pid, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))]
    return [p for p in people if p.id in ordered], unmatched


def parse_subject(text: str, people: list[Person], projects: list[Project],
                  role_hint: str = "") -> Subject:
    """
    Главная функция: фраза → субъект поиска.

    role_hint — роль человека в боте (org/admin/troupe). Организатор по умолчанию
    ищет для всех, актёр — для себя (подставляется вызывающим кодом).
    """
    normalized = _norm(text)
    found_projects = find_projects(text, projects)
    project_ids = [p.id for p in found_projects]

    if MAX_FREE_RE.search(normalized):
        return Subject(mode="max_free", project_ids=project_ids,
                       confidence=0.9, hint="максимум свободных людей")

    matched, leftover = find_people(text, people, project_ids)

    if matched:
        confidence = 0.95 if len(matched) == 1 else 0.85
        if leftover:
            confidence -= 0.2 * min(len(leftover), 2)
        return Subject(mode="subset", participant_ids=[p.id for p in matched],
                       project_ids=project_ids, unresolved=leftover,
                       confidence=max(confidence, 0.3),
                       hint=", ".join(p.name for p in matched))

    if ALL_RE.search(normalized) or not normalized.strip():
        return Subject(mode="all", project_ids=project_ids, confidence=0.8,
                       hint="вся труппа")

    return Subject(mode="all", project_ids=project_ids, unresolved=leftover,
                   confidence=0.2, hint="не понял, для кого искать")
