"""Люди: участники, профиль, карточка, коды и приглашения."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field, field_validator

from .. import core, intro_llm, llm
from ..person_match import rank, resolve, words
from . import finance

router = APIRouter()

@router.get("/tools/participants/{participant_id}/card")
def participant_card(participant_id: int,
                     who: dict = Depends(core.require("participants.view"))) -> dict:
    """Карточка участника в админ-панели (Ш-50): имя, роли, права, дорога,
    телефон и день рождения, тариф, спектакли, последние обращения.

    Чужой номер приходит маской: полностью его видят сам человек и те, кому
    выдали `participants.contact` (FR-A10, правило — `app/privacy.py`).
    """
    return core._person_card(participant_id, who.get("id"), who.get("caps"))



def _bind_master_discipline(conn, participant_id: int, code_role: str,
                            discipline_code: str | None) -> str | None:
    """Педагог выбирает дисциплину при регистрации (064).

    Источник — сначала регистрация, потом поле кода. Отсутствие имени дисциплины
    не блокирует вход: мастер без дисциплины работает, `core.discipline_warnings`
    показывает предупреждение (V-037, 061). Неизвестный код — ошибка, а не догадка.
    """
    if code_role != "master" or not discipline_code:
        return None
    row = conn.execute("select id from discipline where code = %s", (discipline_code,)).fetchone()
    if row is None:
        return "unknown_discipline"
    conn.execute(
        """insert into participant_discipline (participant_id, discipline_id, relation)
           values (%s, %s, 'teacher')
           on conflict (participant_id, discipline_id) do update set relation = 'teacher'""",
        (participant_id, row["id"]))
    return None




@router.get("/tools/participants/me")
def me(who: dict = Depends(core.current)) -> dict:
    """Своя запись: роли, возможности и дисциплины, которые человек ведёт (140 §4.1)."""
    with core.pool.connection() as conn:
        disciplines = core.disciplines_out(conn, who["id"])
    return {"ok": True, "data": core._participant_out(who, disciplines=disciplines)}



@router.get("/tools/participants")
def participants(include_archived: bool = False,
                 who: dict = Depends(core.require("participants.view"))) -> dict:
    """Действующие участники. Ушедших показываем только по явному запросу."""
    with core.pool.connection() as conn:
        if include_archived:
            rows = conn.execute("select * from participant order by id").fetchall()
        else:
            rows = conn.execute(
                "select * from participant where status <> 'archived' order by id").fetchall()
        # роли и права считаем так же, как для /me — иначе панель видит пустые права
        # и одну устаревшую роль (smoke docs/reports/62, S-6)
        ids = [r["id"] for r in rows]
        roles = {r["participant_id"]: core._clean_roles(r["roles"]) for r in conn.execute(
            "select participant_id, roles from v_participant_roles "
            "where participant_id = any(%s)", (ids,)).fetchall()}
        caps: dict[int, list[str]] = {}
        for r in conn.execute(
                "select participant_id, capability from v_participant_capabilities "
                "where participant_id = any(%s)", (ids,)).fetchall():
            caps.setdefault(r["participant_id"], []).append(r["capability"])
        # дисциплины педагогов — одним запросом на список, а не по человеку:
        # иначе «Люди» из 30 человек давала бы 30 лишних запросов (140 §4.1)
        teaching: dict[int, list[dict]] = {}
        for r in conn.execute(
                "select participant_id, teacher_disciplines, teacher_titles "
                "from v_participant_disciplines where participant_id = any(%s)",
                (ids,)).fetchall():
            codes = list(r["teacher_disciplines"] or [])
            titles = list(r["teacher_titles"] or [])
            teaching[r["participant_id"]] = [
                {"code": code, "name": titles[i] if i < len(titles) else code}
                for i, code in enumerate(codes)]
    return {"ok": True, "data": [
        core._participant_out(r, roles.get(r["id"]) or [r["role"]],
                              sorted(caps.get(r["id"], [])), teaching.get(r["id"], []))
        for r in rows]}



class ProfileIn(core.ApiIn):
    travel_minutes: int | None = None
    roles: list[dict] | None = None
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    aliases: list[str] | None = None            # короткие имена, полная замена
    ai_manifest: str | None = Field(default=None, max_length=600)



def _find_person(conn, query: str) -> dict:
    """Один вход для всех интерфейсов: найден / не найден / нужно выбрать."""
    return resolve(core._people_with_aliases(conn), query)



@router.get("/tools/profile/me")
def profile_me(who: dict = Depends(core.current)) -> dict:
    """Своя анкета для бота: время в пути и роли (без Mini App)."""
    with core.pool.connection() as conn:
        person = conn.execute("select id, display_name, travel_minutes, phone, "
                              "birth_date, plays, roles_note, interests, write_gender, "
                              "dues_tariff, dues_tariff_from from participant "
                              "where id = %s", (who["id"],)).fetchone()
        roles = conn.execute("""
            select a.alias, pr.name as project_name
            from person_alias a left join project pr on pr.id = a.project_id
            where a.participant_id = %s and a.alias_kind = 'role'
            order by pr.name, a.alias""", (who["id"],)).fetchall()
        aliases = conn.execute("""
            select alias from person_alias
            where participant_id = %s and alias_kind in ('short', 'name')
            order by alias""", (who["id"],)).fetchall()
        manifest = conn.execute("select ai_manifest from participant where id = %s",
                                (who["id"],)).fetchone()
    return {"ok": True, "name": person["display_name"],
            "travel_minutes": person["travel_minutes"],
            "phone": person["phone"] or "",
            "birth_date": (person["birth_date"].isoformat()
                           if person["birth_date"] else ""),
            "plays": person["plays"] or "",
            "roles_note": person["roles_note"] or "",
            "interests": person["interests"] or "",
            "write_gender": person["write_gender"] or "m",
            "dues_tariff": (float(person["dues_tariff"])
                            if person["dues_tariff"] is not None else None),
            "dues_tariff_from": (person["dues_tariff_from"].isoformat()
                                 if person["dues_tariff_from"] else None),
            "aliases": [row["alias"] for row in aliases],
            "ai_manifest": manifest["ai_manifest"] or "",
            "roles": [dict(r) for r in roles]}



class ProfileEditIn(core.ApiIn):
    travel_minutes: int | None = Field(default=None, ge=0, le=240)
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    aliases: list[str] | None = None        # полная замена коротких имён
    alias_add: str | None = Field(default=None, max_length=40)   # добавить одно, не трогая прочие
    ai_manifest: str | None = Field(default=None, max_length=600)
    # поля знакомства (V-009): телефон, день рождения, спектакли, роли, тренинги
    phone: str | None = Field(default=None, max_length=20)
    birth_date: date | None = None
    plays: str | None = Field(default=None, max_length=300)
    roles_note: str | None = Field(default=None, max_length=300)
    interests: str | None = Field(default=None, max_length=300)
    # как писать человека в лист занятости: «занят» или «занята» (FR-C9)
    write_gender: Literal["m", "f"] | None = None

    @field_validator("phone")
    @classmethod
    def _phone_ok(cls, value: str | None) -> str | None:
        """Телефон — только цифры и необязательный «+»: иначе в профиле мусор."""
        if value is None or value == "":
            return value or None
        digits = re.sub(r"\D", "", value)
        if not 10 <= len(digits) <= 15:
            raise ValueError("phone_bad_format")
        return "+" + digits

    @field_validator("birth_date")
    @classmethod
    def _birthday_ok(cls, value: date | None) -> date | None:
        if value is not None and not date(1900, 1, 1) <= value <= date(2020, 12, 31):
            raise ValueError("birth_date_out_of_range")
        return value



@router.post("/tools/profile/me")
def profile_me_save(body: ProfileEditIn, who: dict = Depends(core.current)) -> dict:
    """
    Правка своего профиля: имя, короткие имена, время в пути, манифест для AI.

    Роли и спектакли меняются только через анкету или администратора: это
    не личное предпочтение, а факт участия в постановке.
    """
    changed: dict[str, object] = {}
    aliases = None
    if body.aliases is not None:
        cleaned, seen = [], set()
        for alias in body.aliases:
            value = " ".join(str(alias).split())
            if not 2 <= len(value) <= 40:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "alias_bad_length")
            if value.lower() in seen:
                continue
            seen.add(value.lower())
            cleaned.append(value)
        if len(cleaned) > 6:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "aliases_too_many")
        aliases = cleaned

    with core.pool.connection() as conn:
        if body.travel_minutes is not None:
            conn.execute("update participant set travel_minutes = %s where id = %s",
                         (body.travel_minutes, who["id"]))
            changed["travel_minutes"] = body.travel_minutes
        if body.display_name is not None:
            # Ключ не пересчитываем: это идентификатор (и имя файла настроек),
            # а не подпись. Иначе переименование тихо отвязывает личный манифест.
            name = " ".join(body.display_name.split())
            conn.execute("update participant set display_name = %s where id = %s",
                         (name, who["id"]))
            changed["display_name"] = name
        if body.ai_manifest is not None:
            manifest = body.ai_manifest.strip()
            conn.execute("update participant set ai_manifest = %s where id = %s",
                         (manifest or None, who["id"]))
            changed["ai_manifest"] = manifest[:120]
        # Поля знакомства (V-009): пустая строка — осознанная очистка поля.
        for field in ("phone", "plays", "roles_note", "interests"):
            value = getattr(body, field)
            if value is not None:
                conn.execute(f"update participant set {field} = %s where id = %s",
                             (value.strip() or None, who["id"]))
                changed[field] = value[:60]
        if body.write_gender is not None:
            conn.execute("update participant set write_gender = %s where id = %s",
                         (body.write_gender, who["id"]))
            changed["write_gender"] = body.write_gender
        if body.birth_date is not None:
            conn.execute("update participant set birth_date = %s where id = %s",
                         (body.birth_date, who["id"]))
            changed["birth_date"] = body.birth_date.isoformat()
        if aliases is not None:
            conn.execute("""delete from person_alias
                            where participant_id = %s and alias_kind = 'short'""",
                         (who["id"],))
            for alias in aliases:
                conn.execute("""insert into person_alias (participant_id, alias, alias_kind)
                                values (%s, %s, 'short')
                                on conflict do nothing""", (who["id"], alias))
            changed["aliases"] = aliases
        if body.alias_add is not None:
            # Сценарий «знакомство» (§docs/development/13 D) спрашивает одно имя,
            # поэтому не заменяет список целиком, иначе прогон /intro повторно
            # стирал бы прочие короткие имена, уже записанные человеком.
            value = " ".join(body.alias_add.split())
            if not 2 <= len(value) <= 40:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "alias_bad_length")
            conn.execute("""insert into person_alias (participant_id, alias, alias_kind)
                            values (%s, %s, 'short')
                            on conflict do nothing""", (who["id"], value))
            changed["alias_add"] = value
        core.audit(conn, "human", who["id"], "profile.updated", "participant",
              str(who["id"]), after=changed)
    return {"ok": True, **changed}



class IntroParseIn(core.ApiIn):
    """Один ответ знакомства, который правила не поняли (docs/development/13 §D)."""

    step: Literal["alias", "travel", "manifest"]
    text: str = Field(min_length=1, max_length=1000)



@router.post("/tools/profile/intro-parse")
async def profile_intro_parse(body: IntroParseIn, who: dict = Depends(core.current)) -> dict:
    """
    LLM-ветка разбора знакомства: закрытый набор полей, без права писать что-то ещё.

    Вызывается гейтвеем только тогда, когда правила промолчали. Квота та же, что
    у ассистента: модель не должна обходить лимит AI. Любой сбой возвращается как
    `ok:false` — человек получит обычный переспрос, сценарий не сдвинется.
    """
    pid = who.get("id")
    with core.pool.connection() as conn:
        bal = conn.execute("select tokens_left, requests_left from v_llm_balance "
                           "where participant_id = %s", (pid,)).fetchone()
    if bal and bal["tokens_left"] is not None and bal["tokens_left"] <= 0:
        return {"ok": False, "error": "quota_exceeded"}
    if bal and bal["requests_left"] is not None and bal["requests_left"] <= 0:
        return {"ok": False, "error": "quota_exceeded"}
    try:
        patch = await intro_llm.ask(body.step, body.text,
                                    api_key=core.LLM_API_KEY,
                                    model=core.LLM_MODEL)
    except intro_llm.LlmError as exc:
        return {"ok": False, "error": exc.code}
    patch = intro_llm.clean(body.step, (patch or {}).get(intro_llm.FIELD_OF[body.step]))
    if not patch:
        return {"ok": False, "error": "llm_unparsed"}
    with core.pool.connection() as conn:
        conn.execute("""insert into llm_usage (participant_id, model, tokens_in, tokens_out,
                           cost_rub, intent, provider)
                        values (%s,%s,0,0,0,%s,%s)""",
                     (pid, core.LLM_MODEL, "intro", llm.provider()))
        core.audit(conn, "bot", pid, "intro.llm_parse", "participant", str(pid),
              after={"step": body.step, "fields": sorted(patch)})
    return {"ok": True, "patch": patch, "source": "llm"}



@router.get("/webapp/my")
def webapp_my(month: str | None = None, who: dict = Depends(core.webapp_actor)) -> dict:
    """
    Личный лист занятости: часы в строках, дни в колонках — как персональная
    страница в таблице (docs/development/22 §2). Только свои данные (docs/development/48).
    """
    month = month or datetime.now(core.TZ).strftime("%Y-%m")
    try:
        days = core._month_days(month)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_month") from None

    with core.pool.connection() as conn:
        name = conn.execute("select display_name from participant where id = %s",
                            (who["id"],)).fetchone()["display_name"]
        rows = conn.execute("""
            select day, hour, is_free, kind from availability_hour
            where participant_id = %s and day between %s and %s""",
            (who["id"], days[0], days[-1])).fetchall()

    cells: dict[str, dict[str, str]] = {}
    # лист рисует только рабочий день; отметки вне его считаем явно, а не теряем
    # молча (smoke docs/reports/62, S-6b: 432 строки с hour < 8 были невидимы)
    outside_hours = 0
    for row in rows:
        if row["hour"] not in core.GRID_HOURS:
            outside_hours += 1
            continue
        if row["is_free"]:
            state = "free"
        elif row["kind"] == "uncertain":
            state = "uncertain"
        else:
            state = "busy"
        cells.setdefault(row["day"].isoformat(), {})[str(row["hour"])] = state

    return {"ok": True, "month": month, "name": name,
            "days": [day.isoformat() for day in days],
            "weekdays": [day.weekday() for day in days],
            "hours": [core.GRID_HOURS.start, core.GRID_HOURS.stop - 1],
            "states": list(core.GRID_STATES),
            "cells": cells,
            "outside_hours": outside_hours}



@router.get("/webapp/profile")
def webapp_profile(who: dict = Depends(core.webapp_actor)) -> dict:
    """Анкета: время в пути, роли по спектаклям, проекты на выбор."""
    with core.pool.connection() as conn:
        person = conn.execute("select * from participant where id = %s",
                              (who["id"],)).fetchone()
        aliases = conn.execute("""
            select a.alias, a.alias_kind, a.project_id, pr.name as project_name
            from person_alias a left join project pr on pr.id = a.project_id
            where a.participant_id = %s order by a.alias_kind, a.alias""",
            (who["id"],)).fetchall()
        projects = conn.execute(
            "select id, name, kind from project where status <> 'archive' order by name"
        ).fetchall()
        # Дисциплины — только чтение (D1 дизайна V-037): правит их администратор
        # в карточке, а мастер видит свои и предупреждение о пустом списке.
        disciplines = core.disciplines_out(conn, who["id"])
        roles = core.resolve_roles(conn, who["id"])

    return {"ok": True,
            "profile": {"id": person["id"], "name": person["display_name"],
                        "travel_minutes": person["travel_minutes"],
                        "dues_tariff": (float(person["dues_tariff"])
                                        if person["dues_tariff"] is not None else None),
                        "dues_tariff_from": (person["dues_tariff_from"].isoformat()
                                             if person["dues_tariff_from"] else None),
                        "ai_manifest": person["ai_manifest"] or "",
                        "short_aliases": [a["alias"] for a in aliases
                                          if a["alias_kind"] in ("short", "name")],
                        "roles": [dict(a) for a in aliases if a["alias_kind"] == "role"],
                        "aliases": [dict(a) for a in aliases]},
            "projects": [dict(p) for p in projects],
            # ключи верхнего уровня: `profile` — состояние формы, дисциплины в ней
            # не редактируются, а предупреждение нужно анкете отдельно (D5)
            "disciplines": disciplines,
            "warnings": core.discipline_warnings(roles, disciplines, audience="self")}



@router.post("/webapp/profile")
def webapp_profile_save(body: ProfileIn, who: dict = Depends(core.webapp_actor)) -> dict:
    """
    Человек сам заполняет про себя: сколько ехать и какие роли играет.

    Роли заменяются целиком: присланный список считается правдой,
    поэтому повторные сохранения не накапливают мусор.
    """
    if body.travel_minutes is not None and not 0 <= body.travel_minutes <= 240:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "travel_minutes_out_of_range")

    short_aliases = None
    if body.aliases is not None:
        short_aliases, seen = [], set()
        for alias in body.aliases:
            value = " ".join(str(alias).split())
            if not 2 <= len(value) <= 40:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "alias_bad_length")
            if value.lower() in seen:
                continue
            seen.add(value.lower())
            short_aliases.append(value)
        if len(short_aliases) > 6:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "aliases_too_many")

    with core.pool.connection() as conn:
        if body.display_name is not None:
            # Ключ не пересчитываем: это идентификатор (и имя файла настроек),
            # а не подпись. Иначе переименование тихо отвязывает личный манифест.
            name = " ".join(body.display_name.split())
            if len(name) < 2:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "display_name_empty")
            conn.execute("update participant set display_name = %s where id = %s",
                         (name, who["id"]))
        if body.ai_manifest is not None:
            conn.execute("update participant set ai_manifest = %s where id = %s",
                         (body.ai_manifest.strip() or None, who["id"]))
        if short_aliases is not None:
            # трогаем только 'short': 'name' пришёл из импорта и служит распознаванию
            conn.execute("""delete from person_alias
                            where participant_id = %s and alias_kind = 'short'""",
                         (who["id"],))
            for alias in short_aliases:
                conn.execute("""insert into person_alias (participant_id, alias, alias_kind, source)
                                values (%s, %s, 'short', 'bot')
                                on conflict do nothing""", (who["id"], alias))

        if body.travel_minutes is not None:
            conn.execute("update participant set travel_minutes = %s where id = %s",
                         (body.travel_minutes, who["id"]))

        if body.roles is not None:
            conn.execute("delete from person_alias where participant_id = %s "
                         "and alias_kind = 'role'", (who["id"],))
            for item in body.roles:
                alias = (item.get("alias") or "").strip()
                project_id = item.get("project_id")
                if not alias or not project_id:
                    continue
                conn.execute("""insert into person_alias
                                  (participant_id, alias, alias_kind, project_id, source)
                                values (%s, %s, 'role', %s, 'bot')
                                on conflict (participant_id, alias) do update
                                  set project_id = excluded.project_id,
                                      alias_kind = 'role'""",
                             (who["id"], alias, project_id))

        rows = conn.execute("""select a.alias, a.project_id, pr.name as project_name
                               from person_alias a left join project pr on pr.id = a.project_id
                               where a.participant_id = %s and a.alias_kind = 'role'
                               order by a.alias""", (who["id"],)).fetchall()
        travel = conn.execute("select travel_minutes from participant where id = %s",
                              (who["id"],)).fetchone()["travel_minutes"]

        core.audit(conn, "human", who["id"], "profile.updated", "participant", str(who["id"]),
              after={"travel_minutes": travel, "roles": [r["alias"] for r in rows]})

    return {"ok": True, "travel_minutes": travel, "roles": [dict(r) for r in rows]}



class CheckCodeIn(core.ApiIn):
    chat_id: int
    code: str



class AttachIn(core.ApiIn):
    chat_id: int
    code: str
    discipline_code: Literal["acting", "music"] | None = None
    correlation_id: str | None = None



def _code_row(conn, code: str):
    row = conn.execute("select * from invite_code where upper(code) = upper(%s)",
                       (code.strip(),)).fetchone()
    if row is None:
        return None, "code_not_found"
    if row["expires_at"] < core.now():
        return None, "code_expired"
    if row["uses"] >= row["max_uses"]:
        return None, "code_exhausted"
    return row, None



def _search_candidates(conn, q: str, limit: int = core.SEARCH_LIMIT) -> list[dict]:
    """Люди без Telegram, подходящие под запрос, — лучшие совпадения первыми.

    Ищем по имени, ключу и псевдонимам тем же правилом, что и везде
    (`person_match`): «дарья», «смирн», «смирнова-дарья» находят «Смирнова Дарья».
    Автосопоставления нет: список сужает бот, запись выбирает человек.
    """
    free = [person for person in core._people_with_aliases(conn) if person["tg_chat_id"] is None]
    # пустой запрос — это «покажи всех свободных»: человек листает и выбирает себя
    matched = rank(free, q) if words(q) else free
    return [{"id": person["id"], "display_name": person["display_name"],
             "role": person["role"]}
            for person in matched[:limit]]



@router.post("/tools/participants/check_code")
def check_code(body: CheckCodeIn) -> dict:
    """Проверка персонального кода перед привязкой (docs/development/182 §1.4)."""
    with core.pool.connection() as conn:
        fails = conn.execute(
            """select count(*) c from invite_attempt
               where chat_id = %s and ok = false and ts > now() - interval '1 hour'""",
            (body.chat_id,),
        ).fetchone()["c"]
        if fails >= core.INVITE_MAX_ATTEMPTS:
            core.audit(conn, "system", None, "access.denied", "invite_code", body.code,
                       after={"reason": "rate_limited", "chat_id": body.chat_id})
            return {"ok": False, "error": "rate_limited"}

        code_row, err = _code_row(conn, body.code)
        if err:
            conn.execute("insert into invite_attempt (chat_id, ok) values (%s, false)",
                         (body.chat_id,))
            core.audit(conn, "human", None, "registration.reject", "invite_code", body.code,
                       after={"reason": err, "chat_id": body.chat_id})
            return {"ok": False, "error": err}

        existing = conn.execute("select * from participant where tg_chat_id = %s",
                                (body.chat_id,)).fetchone()
        if existing:
            return {"ok": False, "error": "already_registered",
                    "participant": core._participant_out(existing)}

        person = None
        if code_row["participant_id"] is not None:
            person = conn.execute("select * from participant where id = %s",
                                  (code_row["participant_id"],)).fetchone()
        if person is None:
            return {"ok": False, "error": "code_not_personal"}
        if person["status"] in ("blocked", "archived"):
            return {"ok": False, "error": "participant_blocked"}
        needs_discipline = (
            person["role"] == "master"
            and conn.execute("select 1 from participant_discipline where participant_id = %s",
                             (person["id"],)).fetchone() is None
        )
    return {"ok": True, "role": person["role"], "needs_discipline": needs_discipline,
            "participant": {"id": person["id"], "display_name": person["display_name"],
                            "role": person["role"]}}



@router.get("/tools/participants/find")
def participants_find(q: str = "",
                      who: dict = Depends(core.require("participants.view"))) -> dict:
    """Один человек или явный выбор из нескольких — для бота и админ-панели.

    Ответ: `status` = `resolved` (есть `person`), `ambiguous` (есть `candidates`,
    решает человек) или `not_found`. Интерфейс не берёт первого из списка.
    """
    with core.pool.connection() as conn:
        result = _find_person(conn, q)
    return {"ok": True, "query": q, "status": result["status"],
            "person": result["person"], "candidates": result["candidates"]}



@router.post("/tools/participants/attach")
def attach(body: AttachIn) -> dict:
    """Привязка chat_id к записи, на которую выписан персональный код."""
    with core.pool.connection() as conn:
        code_row, err = _code_row(conn, body.code)
        if err:
            return {"ok": False, "error": err}

        participant_id = code_row["participant_id"]
        person = None
        if participant_id is not None:
            person = conn.execute("select * from participant where id = %s",
                                  (participant_id,)).fetchone()
        if person is None:
            return {"ok": False, "error": "code_not_personal"}
        if person["tg_chat_id"] is not None:
            return {"ok": False, "error": "code_exhausted"}
        if person["status"] in ("blocked", "archived"):
            return {"ok": False, "error": "participant_blocked"}

        chosen_discipline = body.discipline_code or code_row.get("discipline_code")
        if person["role"] == "master" and not chosen_discipline and conn.execute(
                "select 1 from participant_discipline where participant_id = %s",
                (person["id"],)).fetchone() is None:
            return {"ok": False, "error": "need_discipline"}
        if chosen_discipline and conn.execute(
                "select 1 from discipline where code = %s", (chosen_discipline,)).fetchone() is None:
            return {"ok": False, "error": "unknown_discipline"}

        updated = conn.execute(
            """update participant
                  set tg_chat_id = %s, status = 'active',
                      activated_at = now(), drive_folder = coalesce(drive_folder, %s)
                where id = %s returning *""",
            (body.chat_id, f"TG_{body.chat_id}", person["id"])).fetchone()
        _bind_master_discipline(conn, person["id"], person["role"], chosen_discipline)
        conn.execute("update invite_code set uses = uses + 1, used_by = %s where id = %s",
                     (body.chat_id, code_row["id"]))
        conn.execute("insert into invite_attempt (chat_id, ok) values (%s, true)",
                     (body.chat_id,))
        core.audit(conn, "human", person["id"], "participant.attach", "participant",
                   str(person["id"]), before={"tg_chat_id": None},
                   after={"tg_chat_id": body.chat_id, "role": person["role"]},
                   correlation_id=body.correlation_id)
    return {"ok": True, "status": "attached", "participant": core._participant_out(updated)}



@router.get("/tools/me/capabilities")
def my_capabilities(who: dict = Depends(core.current)) -> dict:
    return {"ok": True, "roles": who.get("roles", []), "capabilities": who.get("caps", [])}



@router.get("/webapp/person/info")
def webapp_person_info(id: int, who: dict = Depends(core.webapp_actor)) -> dict:
    viewer = core._webapp_require(who, "participants.view")
    card = core._person_card(id, viewer.get("id"), viewer.get("caps"))
    dues = finance.dues_me(who=dict(viewer, id=id)) if id == viewer.get("id") else None
    month = None
    if dues:
        month = dues.get("current")
    else:
        with core.pool.connection() as conn:
            row = conn.execute("select period, amount_due, amount_paid from dues_charge "
                               "where participant_id = %s order by period desc limit 1",
                               (id,)).fetchone()
        if row:
            month = {"period": str(row["period"])[:7], "charged": True,
                     "settled": float(row["amount_paid"] or 0) >= float(row["amount_due"] or 0)}
    return {"ok": True, "data": card["data"], "month": month}



class WebAppPersonTariffIn(core.ApiIn):
    participant_id: int
    tariff: float | None = None



@router.post("/webapp/person/tariff")
def webapp_person_tariff(body: WebAppPersonTariffIn,
                         who: dict = Depends(core.webapp_actor)) -> dict:
    return dues_tariff_set(body.participant_id, core.DuesTariffIn(tariff=body.tariff),
                           who=core._webapp_require(who, "budget.write"))



@router.get("/tools/participants/{participant_id}/dues-tariff")
def dues_tariff_get(participant_id: int,
                    who: dict = Depends(core.require("participants.view"))) -> dict:
    """Тариф участника глазами администратора (FR-B7)."""
    with core.pool.connection() as conn:
        person = conn.execute("select id, display_name, dues_tariff, dues_tariff_from "
                              "from participant where id = %s", (participant_id,)).fetchone()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
    today = datetime.now(core.TZ).date()
    return {"ok": True, "participant_id": person["id"],
            "display_name": person["display_name"],
            "current": core._tariff_state(person, today.replace(day=1)),
            "next": core._tariff_state(person, core._next_month_start(today))}



@router.post("/tools/participants/{participant_id}/dues-tariff")
def dues_tariff_set(participant_id: int, body: core.DuesTariffIn,
                    who: dict = Depends(core.require("budget.write"))) -> dict:
    """Назначает тариф взноса. Новый тариф действует со следующего месяца.

    Перерасчётов нет (решение заказчика 28.10.2026): уже начисленные месяцы
    не трогаем, история тарифов не хранится. Снятие тарифа (`tariff: null`)
    останавливает будущие начисления, прошлые остаются как есть.
    """
    today = datetime.now(core.TZ).date()
    effective = core._next_month_start(today)
    with core.pool.connection() as conn:
        person = conn.execute("select id, display_name, dues_tariff, dues_tariff_from "
                              "from participant where id = %s", (participant_id,)).fetchone()
        if person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "participant_not_found")
        before = {"tariff": (float(person["dues_tariff"])
                             if person["dues_tariff"] is not None else None),
                  "from": (person["dues_tariff_from"].isoformat()
                           if person["dues_tariff_from"] else None)}
        if body.tariff is None:
            conn.execute("update participant set dues_tariff = null, "
                         "dues_tariff_from = null where id = %s", (participant_id,))
            after = {"tariff": None, "from": None}
        else:
            conn.execute("update participant set dues_tariff = %s, dues_tariff_from = %s "
                         "where id = %s", (body.tariff, effective, participant_id))
            after = {"tariff": body.tariff, "from": effective.isoformat()}
        core.audit(conn, "human", who.get("id"), "dues.tariff.set", "participant",
              str(participant_id), before=before, after=after)
    return {"ok": True, "participant_id": participant_id,
            "display_name": person["display_name"],
            "dues_tariff": after["tariff"], "dues_tariff_from": after["from"]}

