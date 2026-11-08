"""Типы ошибок внешних вызовов — одно место, где перечислено, что ловим.

Зачем (R-5, docs/reports/79): широкий перехват любого исключения в обработчике
превращал программную ошибку — опечатку в имени поля, неверный тип — в ответ
`ok:false` «сервис недоступен». Настоящая поломка кода обязана дойти до общего
конверта (`app/errors.py`) и стать 500 с `request_id`: иначе её не видно ни в
логах по идентификатору, ни в мониторинге.

Правило: адаптер ловит только типы из этого модуля. Всё остальное — не отказ
внешнего сервиса, а ошибка кода, и `except` её не перехватывает.
"""

from __future__ import annotations

import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from .errors import ApiError

# Google Calendar/Sheets: HTTP-отказ API, отказ авторизации сервисной учётки и
# сетевой сбой на уровне сокета (httplib2 отдаёт OSError/TimeoutError).
GOOGLE_API_ERRORS = (HttpError, GoogleAuthError, OSError, TimeoutError)

# БД: обрыв соединения, таймаут, закрытое соединение. Здесь ошибок SQL нет
# намеренно: там, где нужен «сервис недоступен», неправильный запрос — не отказ связи.
DB_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)

# Любая ошибка БД (включая нарушение ограничений): нужна там, где по факту ошибки
# делаем компенсацию (убрать осиротевший объект) или разбираем текст ограничения.
SQL_ERRORS = (psycopg.Error,)

# HTTP-клиент: отказ провайдера LLM, n8n, недоступный соседний сервис.
HTTP_ERRORS = (httpx.HTTPError,)

# Объектное хранилище (Garage/S3): отказ сервиса и сетевой сбой.
STORAGE_ERRORS = (BotoCoreError, ClientError, OSError)

# Инструмент агента: доменная ошибка (ApiError) плюс сбой БД/хранилища. Список
# закрытый: ошибка разбора аргументов (TypeError/KeyError) — поломка кода, её
# должен показать конверт, а не «инструмент не сработал».
TOOL_ERRORS = (ApiError, *DB_ERRORS, *STORAGE_ERRORS)
