from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import calendar_sync, core, google_targets

router = APIRouter()

@router.post("/tools/google/mode")
def google_mode_set(body: ModeIn, who: dict = Depends(core.require("*"))) -> dict:
    """Сменить режим приложения. Право администратора: это решение уровня владельца."""
    ...

@router.get("/tools/google/freshness")
def google_freshness(who: dict = Depends(core.require("schedule.read.all"))) -> dict:
    """Свежесть источников: экран показывает «данные от …» и не врёт про свежесть."""
    ...

@router.post("/tools/google/refresh")
def google_refresh(body: RefreshIn | None = None,
                   who: dict = Depends(core.require("schedule.read.all"))) -> dict:
    """Перечитать источники и вернуть расхождения. Ни живые листы, ни боевой не пишет."""
    body = body or RefreshIn()
    ...


# Helper functions

def _window(body: RefreshIn) -> tuple[datetime, datetime]:
    """Окно из тела или текущий календарный месяц в зоне сервиса."""
    ...


def read_as_of(conn) -> datetime | None:
    ...


def scan(conn, *, read_service, draft_service, time_from: datetime, time_to: datetime,
         dry_run: bool = False) -> dict:
    ...


# other function definitions remain
