# 100. Узкие `except` вместо широких и mypy на чистых модулях (R-5, R-7)

Карта: `t_f55bbe3a` (этап E архитектурного ревью). Источник находок —
`docs/reports/79-code-architecture-review.md`: R-5 (широкие `except` скрывают
программные ошибки под видом «сервис недоступен»), R-7 (нет проверки типов).

База: ветка карты D `b5ac42c` (main `d7cb259` + B + C + D). Поведение успешных
сценариев, схема БД и пользовательские тексты не менялись.

## Что было и почему это плохо

На `166e1a2` широких `except Exception` в `services/` было **45** (после разрезов
B/C они разъехались по роутерам, но не исчезли): 23 в `tool-api`, 6 в гейтвее
и ещё 16 в модулях. Каждый такой `except` возвращал `ok:false` **раньше**, чем
срабатывал общий конверт ошибок (`app/errors.py`: `request_id`, `code`,
`retryable`). Последствия две:

* опечатка в имени поля или неверный тип выглядели для человека как «календарь
  недоступен», и по логу нельзя было найти трейсбек — в ответе не было
  `request_id`;
* «сервис ждёт, пока внешний API оживёт» и «в коде ошибка» становились
  неразличимы, поэтому такие места никто не чинил.

Теперь широких `except` в `services/` **0**. Ловятся только типы, которые реально
приходят извне; всё остальное доходит до конверта и становится 500 с `request_id`.

## 1. `app/external.py` — один список «что ловим»

| Константа | Что входит | Где применяется |
|---|---|---|
| `GOOGLE_API_ERRORS` | `HttpError`, `GoogleAuthError`, `OSError`, `TimeoutError` | календарь и листы Google |
| `DB_ERRORS` | `psycopg.OperationalError`, `psycopg.InterfaceError` | «база не ответила» (best-effort ветки) |
| `SQL_ERRORS` | `psycopg.Error` | компенсация после ошибки записи, разбор ограничений |
| `HTTP_ERRORS` | `httpx.HTTPError` | провайдер LLM, n8n, соседний сервис |
| `STORAGE_ERRORS` | `BotoCoreError`, `ClientError`, `OSError` | объектное хранилище |
| `TOOL_ERRORS` | `ApiError`, `DB_ERRORS`, `STORAGE_ERRORS` | инструменты файлового агента |

Гейтвей получил такую же пару в `app/errors.py`: `TELEGRAM_ERRORS`
(`aiogram.exceptions.TelegramAPIError` — база для отказа Bot API и сетевых сбоев)
и `HTTP_ERRORS`.

## 2. Куда переехали 45 `except`

| Модуль | Было | Стало |
|---|---|---|
| `tool-api/app/core.py` | 2 | `GOOGLE_API_ERRORS`; `print` заменён на `log.warning` |
| `api/events.py` | 7 | `GOOGLE_API_ERRORS` (запись/отмена/перенос/health/список) |
| `api/schedule.py` | 6 | `GOOGLE_API_ERRORS` (чтение, запись, контрольное чтение) |
| `api/budget.py` | 3 | `DB_ERRORS` для отметки, `GOOGLE_API_ERRORS` для листа |
| `api/admin.py` | 3 | `HTTP_ERRORS + ValueError` (LLM), `DB_ERRORS` (экран статуса), `agent.ModelUnavailable` |
| `api/webapp_api.py` | 2 | `GOOGLE_API_ERRORS`, `DB_ERRORS` |
| `files_store.py` | 8 | `STORAGE_ERRORS` (байты и уборка) и `SQL_ERRORS` (компенсация после ошибки записи) |
| `folders.py` | 2 | `SQL_ERRORS` (уникальность пути, триггер глубины) |
| `agent.py` | 1 | `TOOL_ERRORS`; модель получила тип `ModelUnavailable` вместо `RuntimeError` |
| `extract.py` | 1 | `_document_errors()`: `OSError`, `ValueError`, `KeyError`, `BadZipFile`, `ParseError`, `InvalidFileException`, `PyPdfError` |
| `audio.py` | 1 | `MutagenError`, `OSError` |
| `cold.py` | 1 | `STORAGE_ERRORS` |
| `webapp.py` | 1 | `ValueError`, `TypeError` (разбор payload Mini App) |
| `intro_llm.py` | 1 | `HTTP_ERRORS`, `ValueError` |
| `tg-gateway/app/core.py` | 3 | `ValueError` (не JSON), `httpx.HTTPError` (n8n) |
| `tg-gateway/app/routers/files.py` | 1 | `TELEGRAM_ERRORS` |
| `tg-gateway/app/routers/menu.py` | 1 | `TELEGRAM_ERRORS` |
| `tg-gateway/app/routers/fallback.py` | 1 | `TELEGRAM_ERRORS` |

Правило, по которому решалось каждое место: **адаптер** внешнего вызова (календарь,
лист, хранилище, Telegram, LLM, n8n) ловит только свои типы и пишет лог; **ветка
бизнес-логики** ловит доменный отказ и не мешает остальному дойти до конверта.
Там, где раньше стоял `pass` (отметка `sheet_written_at`), теперь `log.warning`:
молчание скрывало расхождение «файл ↔ БД».

## 3. Проверка типов: пилот `mypy --strict`

`pyproject.toml`, `[tool.mypy]`: `strict = true`, `python_version = "3.12"`,
`files` — пять чистых модулей (`rules`, `slots`, `window_policy`, `assign`,
`subject`). Запуск — `mypy` без аргументов: проверяется ровно пилот, весь
репозиторий под строгий режим не переводится.

Починено по дороге (9 замечаний): `dict` → `dict[str, Any]` в публичных возвратах
(`parse_budget_text`, `parse_availability`, `parse_interval`, `cascade_decide`,
`Window.as_dict`, `RankedHour.as_dict`, `PolicyWindow.as_dict`), `tuple` →
`tuple[int, int, bool, int]` в `Window.score`, `today` получил тип `date | None`.

Версия закреплена: `mypy==2.4.1` в `requirements-dev.txt`. Новая версия находит
новые ошибки — она не должна молча красить чужой PR.

## 4. Гейты

* `.github/workflows/tests.yml`, job `types` — `pip install -r requirements-dev.txt`
  + `mypy`; отдельный job, чтобы падение типов было видно отдельно от тестов.
* `tests/unit/test_narrow_except.py` — сторож по исходникам: в `services/` нет
  `except Exception`, `except BaseException` и голого `except:`, плюс отдельная
  проверка `except Exception` == 0 в `tool-api/app/main.py`.
* `tests/integration/test_unexpected_error.py` — два поведения рядом:
  отказ Google (`HttpError`) даёт `ok:false calendar_unavailable`; опечатка
  в коде (`AttributeError`) даёт **500** с `code=internal_error` и `request_id`,
  текст ошибки клиенту не показывается.

## Что пришлось поправить в тестах

Тесты имитировали отказ Google через `RuntimeError("Google недоступен")`. Это
и была та самая подмена: «любое исключение = сервис недоступен». Теперь они
бросают настоящий `HttpError` через `tests/conftest.py:google_error()`, а
утверждения проверяют факт отказа, а не выдуманный текст. Это сознательное
изменение тестов: они стали строже и честнее.

## Побочная находка: индекс документации ломался на трёхзначном номере

Первый отчёт с номером 100 вскрыл дефект в `scripts/build_docs_index.py` и
`tests/unit/test_docs_links.py`: номер брался как `name[:2]`, поэтому
`100-narrow-except-and-types.md` попадал в индекс как `10` и «дублировал» документ
10. Оба места читают номер до первого дефиса (`name.split("-", 1)[0]`); проверка
уникальности номеров снова осмысленна.

## Проверки фактами

* `grep -rc "except Exception" services/ --include=*.py` — **0** (было 45);
  `grep -c "except Exception" services/tool-api/app/main.py` — **0**.
* `mypy` (пилот, strict) — `Success: no issues found in 5 source files`.
* `ruff check .` — чисто.
* `pytest tests/unit` — все пройдены; `pytest tests/integration` — все пройдены
  (на Postgres `svoi_test`); `SVOI_WEBAPP_TESTS=1 pytest tests/webapp --browser chromium` — пройдены.
* `scripts/check_invariants.py` — все проверки пройдены; `scripts/check_registers.py` — пройдены.
