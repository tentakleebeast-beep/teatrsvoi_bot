"""Состояние процесса gateway: экземпляр бота, общий для модулей."""

from __future__ import annotations

from aiogram import Bot

_bot: Bot | None = None
