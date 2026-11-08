"""
Политики поиска окон: строгость зависит от типа события (docs/architecture/42).

Разные события требуют разного:

  аренда     — занятость студийцев не важна, нужен свободный зал;
  спектакль  — критична занятость всех занятых в нём актёров, опаздывать нельзя;
  репетиция  — обязательные участники должны быть с начала, остальные могут подойти;
  тренинг    — кем-то можно пожертвовать: он просто пропустит занятие.

Плюс общее правило: к занятости добавляется дорога до театра (по умолчанию час).
Человек, занятый до 19:00, реально может начать в 20:00.

Сетка часов и границы дня — общие с остальным поиском окон (`app/rules.py`),
иначе политика и голый поиск давали бы разные окна на одних данных
(docs/architecture/40 §2).

Модуль чистый: никакой БД, только структуры.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .rules import WORK_DAY_END, WORK_DAY_START

DAY_START = WORK_DAY_START
DAY_END = WORK_DAY_END          # граница «до», не включается: последняя клетка 23:00–24:00

FREE, BUSY, UNKNOWN = "free", "busy", "unknown"

Marks = dict[int, dict[tuple[date, int], bool] | None]


@dataclass(frozen=True)
class WindowPolicy:
    """Насколько строго проверяем людей для этого типа события."""

    kind: str
    ignores_people: bool = False       # зал важнее людей (аренда)
    allow_late: bool = False           # можно начинать без части участников
    allow_absent: bool = False         # кто-то может пропустить совсем
    min_present_share: float = 1.0     # какую долю участников нужно в начале
    travel_hours: int = 1              # дорога до театра, в часах

    @property
    def title(self) -> str:
        return {"rental": "аренда", "rental_staff": "аренда с персоналом",
                "show": "спектакль", "rehearsal": "репетиция",
                "training": "тренинг", "meeting": "встреча"}.get(self.kind, self.kind)


POLICIES: dict[str, WindowPolicy] = {
    # зал сдаётся независимо от того, кто из труппы занят
    # аренда без персонала: смотрим только зал, люди не важны
    "rental": WindowPolicy("rental", ignores_people=True, allow_late=True,
                           allow_absent=True, min_present_share=0.0, travel_hours=0),
    # аренда, где нужны свои люди (сопровождение, администратор события)
    "rental_staff": WindowPolicy("rental_staff", ignores_people=False, allow_late=False,
                                 allow_absent=False, min_present_share=1.0, travel_hours=1),
    # показ: все занятые в спектакле обязаны быть с начала
    "show": WindowPolicy("show", allow_late=False, allow_absent=False,
                         min_present_share=1.0, travel_hours=1),
    # репетиция: обязательные с начала, факультативные подходят позже
    "rehearsal": WindowPolicy("rehearsal", allow_late=True, allow_absent=False,
                              min_present_share=1.0, travel_hours=1),
    # тренинг: можно потерять часть людей
    "training": WindowPolicy("training", allow_late=True, allow_absent=True,
                             min_present_share=0.6, travel_hours=1),
    "meeting": WindowPolicy("meeting", allow_late=True, allow_absent=True,
                            min_present_share=0.7, travel_hours=1),
}

DEFAULT_POLICY = POLICIES["rehearsal"]


def policy_for(kind: str | None) -> WindowPolicy:
    return POLICIES.get((kind or "").strip().lower(), DEFAULT_POLICY)


# --- дорога до театра -------------------------------------------------------

DEFAULT_TRAVEL_MINUTES = 60
# «уже в театре» (аренда, хоз.работы) — дорога не тратится вовсе


def travel_hours(travel_minutes: int) -> int:
    """
    Минуты в пути → сдвиг по часовой сетке.

    До 30 минут человек успевает внутри того же часа, 31–90 — теряет час,
    больше 90 — два. Данные о занятости часовые, точнее дробить нечего.
    """
    if travel_minutes <= 30:
        return 0
    if travel_minutes <= 90:
        return 1
    return 2


def available_from(marks: Marks, pid: int, day: date,
                   travel_minutes: int = DEFAULT_TRAVEL_MINUTES,
                   theatre_hours: dict[int, set[tuple[date, int]]] | None = None) -> int | None:
    """
    С какого часа человек реально может начать в этот день.

    Дорога нужна всегда: свободен с 18:00 — значит в театре к 19:00 (если ехать час).
    Исключение — если он в это время уже в театре (аренда, хоз.работы):
    тогда дороги нет.
    """
    table = marks.get(pid)
    if not table:
        return None

    shift = travel_hours(travel_minutes)
    in_theatre = (theatre_hours or {}).get(pid, set())

    # ищем первый непрерывный свободный отрезок дня и его конец
    run_start: int | None = None
    run_end: int | None = None
    for hour in range(DAY_START, DAY_END):
        if table.get((day, hour)):
            if run_start is None:
                run_start = hour
            run_end = hour + 1
        elif run_start is not None:
            break

    if run_start is None or run_end is None:
        return None

    # был в театре в предыдущий час — уже на месте
    if (day, run_start - 1) in in_theatre:
        return run_start

    shifted = run_start + shift
    # доехать нужно успеть внутри свободного отрезка
    return shifted if shifted < run_end and shifted < DAY_END else None




# --- окна с учётом политики -------------------------------------------------

@dataclass(frozen=True)
class PolicyWindow:
    day: date
    from_hour: int
    to_hour: int
    present: tuple[int, ...] = ()          # будут с самого начала
    joining: tuple[tuple[int, int], ...] = ()   # (кто, с какого часа)
    absent: tuple[int, ...] = ()           # не смогут вовсе
    unknown: tuple[int, ...] = ()          # нет данных
    kind: str = "rehearsal"

    @property
    def hours(self) -> int:
        return self.to_hour - self.from_hour

    @property
    def ok(self) -> bool:
        return self.from_hour < self.to_hour

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "from_hour": self.from_hour,
                "to_hour": self.to_hour, "hours": self.hours,
                "present": list(self.present),
                "joining": [{"id": pid, "from_hour": hour} for pid, hour in self.joining],
                "absent": list(self.absent), "unknown": list(self.unknown),
                "kind": self.kind}


def _status(marks: Marks, pid: int, day: date, hour: int) -> str:
    table = marks.get(pid)
    if not table:
        return UNKNOWN
    value = table.get((day, hour))
    if value is None:
        return UNKNOWN
    return FREE if value else BUSY


def find_policy_windows(marks: Marks,
                        days: list[date],
                        required: list[int],
                        optional: list[int] | None = None,
                        policy: WindowPolicy = DEFAULT_POLICY,
                        min_hours: int = 2,
                        day_start: int = DAY_START,
                        day_end: int = DAY_END,
                        limit: int = 3,
                        travel: dict[int, int] | None = None,
                        theatre_hours: dict[int, set[tuple[date, int]]] | None = None
                        ) -> list[PolicyWindow]:
    """
    Окна по правилам события.

    required — те, без кого событие не имеет смысла (актёры показа, ядро репетиции).
    optional — остальные: могут подойти позже или пропустить.
    """
    optional = optional or []
    if int(min_hours) < 1:
        return []
    if policy.ignores_people:
        # Аренда без персонала считается по залу (`app/venue.py`), а не здесь.
        # Раньше на этом месте выдавалось окно 08:00–10:00 на каждый день —
        # «свободно» при полностью неизвестной занятости зала. Это выдумка:
        # вызывающий обязан посчитать зал и, если данных нет, честно сказать
        # «неизвестно», а не показать окно (docs/architecture/40 §4).
        return []

    if not required and not optional:
        return []

    everyone = list(dict.fromkeys(list(required) + list(optional)))
    windows: list[PolicyWindow] = []

    for day in days:
        starts = {pid: available_from(
            marks, pid, day,
            (travel or {}).get(pid, DEFAULT_TRAVEL_MINUTES),
            theatre_hours) for pid in everyone}

        for start in range(day_start, day_end - min_hours + 1):
            present, joining, absent, unknown = [], [], [], []

            for pid in required:
                begin = starts[pid]
                if begin is None:
                    if _status(marks, pid, day, start) == UNKNOWN and policy.allow_absent:
                        unknown.append(pid)
                        continue
                    absent.append(pid)
                elif begin <= start:
                    present.append(pid)
                elif policy.allow_late or policy.allow_absent:
                    joining.append((pid, begin))
                else:
                    absent.append(pid)

            for pid in optional:
                begin = starts[pid]
                if begin is None:
                    if _status(marks, pid, day, start) == UNKNOWN:
                        unknown.append(pid)
                    else:
                        absent.append(pid)
                elif begin <= start:
                    present.append(pid)
                else:
                    joining.append((pid, begin))

            if absent and not policy.allow_absent:
                continue
            if absent and policy.allow_absent:
                total = len(everyone)
                missing = len(absent)
                if total and (total - missing) / total < policy.min_present_share:
                    continue
            if len(present) < max(1, int(len(required) * policy.min_present_share)):
                continue

            run_end = _run_end(marks, day, [pid for pid in required if pid not in absent],
                               start, day_end, policy)
            if run_end - start < min_hours:
                continue

            windows.append(PolicyWindow(
                day=day, from_hour=start, to_hour=run_end,
                present=tuple(sorted(present)),
                joining=tuple(sorted((pid, hour) for pid, hour in joining if hour < run_end)),
                absent=tuple(sorted(absent)), unknown=tuple(sorted(unknown)),
                kind=policy.kind))

    # сначала убираем вложенные варианты (длинные окна должны идти первыми),
    # потом сортируем по качеству состава
    windows.sort(key=lambda w: (w.day.toordinal(), -w.hours, w.from_hour))
    unique = _drop_nested(windows)
    unique.sort(key=lambda w: (len(w.absent), len(w.joining), -w.hours,
                               not (w.from_hour >= 18), w.day.toordinal(), w.from_hour))
    return unique[:limit]


def _drop_nested(windows: list[PolicyWindow]) -> list[PolicyWindow]:
    """
    Убирает варианты, которые целиком лежат внутри другого окна того же дня
    и при этом не лучше его по составу.

    Окно «21:00–23:00, все на месте» внутри «19:00–23:00, один подойдёт позже» —
    это другой вариант, его оставляем: начать позже, но полным составом.
    """
    kept: list[PolicyWindow] = []
    for window in windows:
        worse_or_equal = any(
            other.day == window.day
            and other.from_hour <= window.from_hour
            and other.to_hour >= window.to_hour
            and other is not window
            and (len(other.absent), len(other.joining)) <= (len(window.absent),
                                                             len(window.joining))
            for other in kept)
        if not worse_or_equal:
            kept.append(window)
    return kept


def _run_end(marks: Marks, day: date, pids: list[int], start: int, day_end: int,
             policy: WindowPolicy) -> int:
    """До какого часа окно держится: пока все обязательные на месте."""
    end = start
    for hour in range(start, day_end):
        if not all(_status(marks, pid, day, hour) == FREE for pid in pids):
            break
        end = hour + 1
    return end
