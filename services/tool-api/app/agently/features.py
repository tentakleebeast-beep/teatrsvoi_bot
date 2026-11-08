"""Реестр функций агента: экран Mini App или кнопка чата → инструмент либо «не для агента».

Зачем модуль. До него агент знал только свои инструменты, а человек — только кнопки:
на вопрос «что можно записать/посмотреть» ответ собирался из памяти модели и потому
не совпадал с тем, что человек реально видит в приложении и боте. Здесь закрытый
список поверхностей (`config/agent-features.yaml`), и предложение агента строится
из него, а не из догадки.

Правила модуля:
  * у каждой записи ровно один смысл: `tool` (инструмент агента), `capabilities`
    (пункт «Что умею» — этот же реестр) или `not_for_agent` с причиной;
  * `tool` обязан существовать в закрытом реестре `registry.TOOLS`;
  * покрытие проверяется тестом (`tests/unit/test_agent_features.py`): новый экран
    Mini App или кнопка чата без записи роняют тест, а не всплывают в разговоре;
  * продуктовые формулировки кнопок не переписываются: `source` — их текущий текст;
  * агент не получает новых прав: `features_for` фильтрует по тем же возможностям,
    что и `registry.available_tools`.

Живой замер — `scripts/check_agent_features.py` на наборе
`tests/fixtures/agent_features_questions.json` (10 вопросов).
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

import yaml

from . import registry

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[4] / "config" / "agent-features.yaml"

KINDS = ("screen", "button")
SURFACES = ("webapp", "menu", "quick", "panel", "chat")
#: Файлы бота, которые сторож читает построчно (раздел `scenarios` реестра).
SCENARIO_FILES = ("menu.py", "keyboards.py")
SURFACE_HUMAN = {
    "webapp": "Mini App",
    "menu": "Меню бота",
    "quick": "Быстрые кнопки",
    "panel": "Админ-панель",
    "chat": "Кнопки в чате",
}

#: Инструмент, который только предлагает запись: без кнопки человека данных не меняет (A-3).
PROPOSE_TOOLS = frozenset({"propose_budget_tx", "propose_event", "propose_note",
                           "propose_move"})


class RegistryError(ValueError):
    """Реестр функций сломан: это ошибка кода, а не данных человека."""


@dataclass(frozen=True)
class Scenario:
    """Кнопка, подпись которой приходит из данных, — описана с причиной.

    Пример: `_candidates_kb` строит кнопки с именами людей из БД, `kb` — общий
    построитель строк. Такую подпись нельзя сверить с реестром построчно, поэтому
    место объявляется здесь явно: файл, функция, вид кнопки и выражение подписи.
    Новое непроверяемое место без записи роняет тест.
    """

    id: str
    file: str
    function: str
    kind: str
    expr: str
    reason: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.file, self.function, self.kind, self.expr)


@dataclass(frozen=True)
class Feature:
    """Одна поверхность: экран Mini App или кнопка чата.

    `tool` — имя инструмента агента; `capabilities` — пункт «Что умею»;
    `not_for_agent` — причина, по которой агент этого не делает. Ровно одно из трёх.
    """

    id: str
    kind: str
    surface: str
    source: str
    title: str
    tool: str | None = None
    capabilities: bool = False
    not_for_agent: str | None = None
    note: str | None = None

    @property
    def is_action(self) -> bool:
        """Агент может предложить это действие (или оно само — список действий)."""
        return bool(self.tool) or self.capabilities

    @property
    def is_write_proposal(self) -> bool:
        return self.tool in PROPOSE_TOOLS


def _tool_names() -> set[str]:
    return {tool.name for tool in registry.all_tools()}


def _parse_entry(raw: Any, index: int, seen_ids: set[str],
                 seen_sources: set[tuple[str, str]]) -> Feature:
    where = f"запись #{index + 1}"
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: ожидался словарь, получено {type(raw).__name__}")
    feature_id = str(raw.get("id") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    surface = str(raw.get("surface") or "").strip()
    source = str(raw.get("source") or "").strip()
    title = str(raw.get("title") or "").strip()
    tool = raw.get("tool")
    capabilities = bool(raw.get("capabilities"))
    not_for_agent = raw.get("not_for_agent")
    note = raw.get("note")

    if not feature_id:
        raise RegistryError(f"{where}: пустой id")
    if feature_id in seen_ids:
        raise RegistryError(f"{where}: id «{feature_id}» повторяется")
    if kind not in KINDS:
        raise RegistryError(f"{where} ({feature_id}): kind «{kind}» не из {KINDS}")
    if surface not in SURFACES:
        raise RegistryError(f"{where} ({feature_id}): surface «{surface}» не из {SURFACES}")
    if not source:
        raise RegistryError(f"{where} ({feature_id}): пустой source")
    if not title:
        raise RegistryError(f"{where} ({feature_id}): пустой title")
    if (surface, source) in seen_sources:
        raise RegistryError(f"{where} ({feature_id}): источник «{source}» на {surface} повторяется")

    meanings = [bool(tool), capabilities, bool(not_for_agent)]
    if sum(meanings) != 1:
        raise RegistryError(
            f"{where} ({feature_id}): нужен ровно один смысл — tool, capabilities "
            f"или not_for_agent; сейчас {meanings}")
    if not_for_agent is not None and not str(not_for_agent).strip():
        raise RegistryError(f"{where} ({feature_id}): not_for_agent без причины")
    if kind == "screen" and not source.startswith("/webapp"):
        raise RegistryError(f"{where} ({feature_id}): экран должен начинаться с /webapp")
    if kind == "button" and source.startswith("/webapp"):
        raise RegistryError(f"{where} ({feature_id}): кнопка не может быть адресом /webapp")
    if tool is not None:
        tool = str(tool).strip()
        if tool not in _tool_names():
            raise RegistryError(f"{where} ({feature_id}): инструмента «{tool}» нет в registry.TOOLS")
        read_only = {item.name: item.read_only for item in registry.all_tools()}
        if read_only.get(tool) is False:
            raise RegistryError(f"{where} ({feature_id}): пишущий инструмент «{tool}» агенту не даётся")

    seen_ids.add(feature_id)
    seen_sources.add((surface, source))
    return Feature(id=feature_id, kind=kind, surface=surface, source=source, title=title,
                   tool=tool, capabilities=capabilities,
                   not_for_agent=str(not_for_agent).strip() if not_for_agent else None,
                   note=str(note).strip() if note else None)


_CACHE: dict[tuple[str, int], tuple[Feature, ...]] = {}
_SCENARIO_CACHE: dict[tuple[str, int], tuple[Scenario, ...]] = {}


def _parse_file(path_str: str) -> tuple[Feature, ...]:
    """Разбор файла однажды: кэш держится по пути и времени правки."""
    raw = yaml.safe_load(pathlib.Path(path_str).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RegistryError("верхний уровень реестра должен быть словарём")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RegistryError("в реестре нет списка entries")
    seen_ids: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    return tuple(_parse_entry(item, index, seen_ids, seen_sources)
                 for index, item in enumerate(entries))


def load(path: str | pathlib.Path | None = None) -> tuple[Feature, ...]:
    """Загрузить и проверить реестр; кэш — по пути и времени правки файла."""
    target = pathlib.Path(path) if path else DEFAULT_PATH
    try:
        mtime = target.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise RegistryError(f"файл реестра не найден: {target}") from exc
    key = (str(target), mtime)
    cached = _CACHE.get(key)
    if cached is None:
        cached = _parse_file(key[0])
        _CACHE.clear()          # держим только актуальную версию каждого файла
        _CACHE[key] = cached
    return cached


def _parse_scenarios(path_str: str) -> tuple[Scenario, ...]:
    """Разбор раздела `scenarios`: непроверяемые построчно подписи с причиной."""
    raw = yaml.safe_load(pathlib.Path(path_str).read_text(encoding="utf-8")) or {}
    entries = raw.get("scenarios")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise RegistryError("раздел scenarios должен быть списком")
    seen_ids: set[str] = set()
    seen_keys: set[tuple[str, str, str, str]] = set()
    out: list[Scenario] = []
    for index, item in enumerate(entries):
        where = f"сценарий #{index + 1}"
        if not isinstance(item, dict):
            raise RegistryError(f"{where}: ожидался словарь")
        scenario = Scenario(
            id=str(item.get("id") or "").strip(),
            file=str(item.get("file") or "").strip(),
            function=str(item.get("function") or "").strip(),
            kind=str(item.get("kind") or "").strip(),
            expr=str(item.get("expr") or "").strip(),
            reason=str(item.get("reason") or "").strip(),
        )
        if not scenario.id:
            raise RegistryError(f"{where}: пустой id")
        if scenario.id in seen_ids:
            raise RegistryError(f"{where}: id «{scenario.id}» повторяется")
        if scenario.file not in SCENARIO_FILES:
            raise RegistryError(f"{where} ({scenario.id}): файл «{scenario.file}» не из {SCENARIO_FILES}")
        if not scenario.function or not scenario.kind or not scenario.expr:
            raise RegistryError(f"{where} ({scenario.id}): нужны function, kind и expr")
        if len(scenario.reason) <= 10:
            raise RegistryError(f"{where} ({scenario.id}): причина — отписка")
        if scenario.key in seen_keys:
            raise RegistryError(f"{where} ({scenario.id}): место {scenario.key} описано дважды")
        seen_ids.add(scenario.id)
        seen_keys.add(scenario.key)
        out.append(scenario)
    return tuple(out)


def load_scenarios(path: str | pathlib.Path | None = None) -> tuple[Scenario, ...]:
    """Загрузить раздел `scenarios`; кэш — по пути и времени правки файла."""
    target = pathlib.Path(path) if path else DEFAULT_PATH
    try:
        mtime = target.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise RegistryError(f"файл реестра не найден: {target}") from exc
    key = (str(target), mtime)
    cached = _SCENARIO_CACHE.get(key)
    if cached is None:
        cached = _parse_scenarios(key[0])
        _SCENARIO_CACHE.clear()
        _SCENARIO_CACHE[key] = cached
    return cached


def scenarios() -> tuple[Scenario, ...]:
    return load_scenarios()


def scenario_keys() -> set[tuple[str, str, str, str]]:
    """Ключи объявленных сценариев: файл, функция, вид кнопки, выражение подписи."""
    return {scenario.key for scenario in scenarios()}


def all_features() -> tuple[Feature, ...]:
    return load()


def by_id(feature_id: str) -> Feature:
    for feature in all_features():
        if feature.id == feature_id:
            return feature
    raise KeyError(feature_id)


def screen_sources() -> set[str]:
    return {feature.source for feature in all_features() if feature.kind == "screen"}


def button_sources(surface: str | None = None) -> set[str]:
    return {feature.source for feature in all_features()
            if feature.kind == "button" and (surface is None or feature.surface == surface)}


def action_features() -> tuple[Feature, ...]:
    """Поверхности, которые агент вправе предлагать (инструменты), без пункта «Что умею»."""
    return tuple(feature for feature in all_features() if feature.tool)


def features_for(who: dict | None) -> tuple[Feature, ...]:
    """Действия, доступные этому человеку по его правам.

    Фильтр тот же, что у инструментов: `registry.available_tools(who)`. Никаких новых
    прав реестр не добавляет — он только показывает то, что человеку и так разрешено.
    """
    allowed = {tool.name for tool in registry.available_tools(who or {})}
    return tuple(feature for feature in action_features() if feature.tool in allowed)


def _titles(features: tuple[Feature, ...]) -> str:
    titles = []
    for feature in features:
        if feature.title not in titles:
            titles.append(feature.title)
    return ", ".join(f"«{title}»" for title in titles)


def _unique_actions(features: tuple[Feature, ...]) -> tuple[Feature, ...]:
    """Одно действие — одна строка, и названо оно так, как человек его видит.

    Один и тот же инструмент стоит на нескольких поверхностях: `propose_budget_tx` —
    на экране формы, в меню и на быстрой кнопке; `read_budget` — на экране «Деньги»
    и пунктом «Бюджет». Человеку это одно действие, поэтому в списке оно одно,
    и подпись берётся с кнопки (меню, быстрые, панель, чат), а не с экрана —
    кнопку человек видит и нажимает.
    """
    chosen: dict[str, Feature] = {}
    order: list[str] = []
    for feature in features:
        key = feature.tool or feature.title
        previous = chosen.get(key)
        if previous is None:
            chosen[key] = feature
            order.append(key)
        elif previous.surface == "webapp" and feature.surface != "webapp":
            chosen[key] = feature
    return tuple(chosen[key] for key in order)


def human_capabilities(who: dict | None) -> str:
    """Детерминированный ответ «что можно записать/посмотреть» по правам человека.

    Список берётся из реестра, а не из памяти модели: перечислены ровно те действия,
    которые человеку разрешены. Запись отделена от чтения — она всегда через кнопку.
    """
    permitted = _unique_actions(features_for(who))
    writes = tuple(feature for feature in permitted if feature.is_write_proposal)
    reads = tuple(feature for feature in permitted if not feature.is_write_proposal)
    lines: list[str] = []
    if writes:
        lines.append("Записать (после вашего подтверждения кнопкой): " + _titles(writes) + ".")
    if reads:
        lines.append("Посмотреть: " + _titles(reads) + ".")
    if not lines:
        return ("По вашим правам у меня нет доступных действий. "
                "Кнопки и формы в чате и Mini App работают как обычно.")
    lines.append("Если чего-то нет в списке — значит, по вашим правам мне это недоступно.")
    return "\n".join(lines)


def prompt_block(who: dict | None) -> str:
    """Строка для системного промпта: закрытый список действий этого человека."""
    permitted = _unique_actions(features_for(who))
    if not permitted:
        return ("Доступных тебе действий по правам этого человека нет: не предлагай "
                "записывать или смотреть данные студии, предложи кнопки и форму.")
    parts = ["Доступные тебе действия (только они, другие не предлагай):"]
    for feature in permitted:
        line = f"- «{feature.title}» ({SURFACE_HUMAN.get(feature.surface, feature.surface)})"
        if feature.note:
            line += ": " + feature.note
        if feature.is_write_proposal:
            line += "; запись — только после подтверждения кнопкой"
        parts.append(line)
    parts.append("Если вопрос о том, что можно записать или посмотреть — перечисли ровно "
                 "эти действия. Новые экраны, кнопки и действия не придумывай.")
    return "\n".join(parts)


#: Вопросы про возможности агента, а не про конкретные данные.
_CAPABILITY_PHRASES = (
    "что можно записать", "что можно посмотреть", "что можно сделать",
    "что ты умеешь", "что вы умеете", "что умеешь", "что можете", "что можешь",
    "что ты можешь", "какие действия", "какие возможности", "что доступно",
    "твои возможности", "ваши возможности", "список действий", "что можно",
)
#: Если в вопросе есть конкретика, это вопрос про данные, а не про возможности.
_DATA_WORDS = (
    "сегодня", "завтра", "вчера", "недел", "месяц", "числ", "день", "врем",
    "сейчас", "расписан", "занят", "бюджет", "взнос", "свобод", "кто ", "когда ",
)


def answers_capability_question(text: str) -> bool:
    """Это вопрос «что ты умеешь / что можно записать/посмотреть», а не про данные."""
    lowered = (text or "").lower().replace("ё", "е").strip()
    if not lowered or len(lowered.split()) > 9:
        return False
    if any(word in lowered for word in _DATA_WORDS):
        return False
    return any(phrase in lowered for phrase in _CAPABILITY_PHRASES)


def capability_answer(who: dict | None) -> dict:
    """Ответ агента на вопрос о возможностях: без инструментов и без выдумок."""
    return {"ok": True, "answer": human_capabilities(who), "scenario": "assistant",
            "propose": None, "steps": [], "step_count": 0, "tokens": 0,
            "model": None, "engine": "rules", "note": "registry_capabilities"}


#: Глаголы действий, которых у агента нет в реестре. Перенос, отмена, удаление и правка
#: событий — работа человека в Mini App; агент не изображает их выполнение.
_UNSUPPORTED_VERBS = frozenset({
    "перенеси", "перенесите", "перенести", "перенос",
    "отмени", "отмените", "отменить",
    "удали", "удалите", "удалить",
    "измени", "измените", "изменить", "поменяй", "поменяйте",
    "сдвинь", "сдвиньте",
})


def asks_unsupported_action(text: str) -> bool:
    """Человек просит действие, которого нет в закрытом реестре инструментов."""
    words = (text or "").lower().replace("ё", "е").strip().split()
    return bool(words) and words[0].strip(",.!?;:") in _UNSUPPORTED_VERBS


def unsupported_action_answer(who: dict | None) -> dict:
    """Честный отказ без выдуманного выполнения: что не умею и что умею вместо этого."""
    answer = ("Пока не умею: переносить, отменять, удалять и править события я не могу — "
              "это делает человек в Mini App.\n" + human_capabilities(who))
    return {"ok": True, "answer": answer, "scenario": "assistant",
            "propose": None, "steps": [], "step_count": 0, "tokens": 0,
            "model": None, "engine": "rules", "note": "registry_unsupported"}


def check_coverage(screens: set[str], buttons: dict[str, set[str]]) -> list[str]:
    """Чего не хватает реестру: экраны и кнопки приложения без записи.

    `screens` — все адреса экранов Mini App; `buttons` — подписи по поверхностям
    (`menu`, `quick`, `panel`, `chat`). Возвращает человекочитаемые ошибки; пусто —
    покрытие полное.
    """
    errors: list[str] = []
    registered_screens = screen_sources()
    for source in sorted(set(screens) - registered_screens):
        errors.append(f"экран {source} есть в приложении, но не в реестре")
    for surface, sources in buttons.items():
        registered = button_sources(surface)
        for source in sorted(set(sources) - registered):
            errors.append(f"кнопка «{source}» на поверхности {surface} есть, но не в реестре")
    return errors
