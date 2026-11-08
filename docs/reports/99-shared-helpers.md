# 99. Общие помощники: format.py, scripts/_cli.py, JS api()/auth() (R-6, R-8)

Карта: `t_0489c36f` (этап D архитектурного ревью). Источник находок —
`docs/reports/79-code-architecture-review.md` (R-6 — дубли форматов денег,
R-8 — каждый скрипт сам грузит окружение и разбирает аргументы).

База: main `d7cb259` + карты B (`wt/t_135f65cd`) и C (`wt/t_f4b2ed7b`), слитые
в ветку карты. Поведение, схема БД и пользовательские тексты не менялись.

## Что сделано

### 1. `services/shared/format.py` — одни форматы на оба сервиса

* `money(value, *, design=False, empty="—")`: обычный пробел и дефис в минусе
  для сообщений бота (`1 234 567 ₽`, `-1 500 ₽`), неразрывный пробел и
  типографский минус для экранов Mini App (`1 234 567 ₽`, `−1 500 ₽`).
* `human_date`, `human_month`, `MONTHS`, `MONTHS_NOM`, `WEEKDAYS_SHORT`,
  `WEEKDAYS_FULL` — те же значения, что были в `pages/layout.py`.
* Потребители: `tool-api` (`notifications.py`, `pages/layout.py` — тонкие
  обёртки с прежними именами), `tg-gateway` (`texts.py`, вместо собственной
  копии).
* Оба `Dockerfile` копируют `services/shared`; в гейтвее путь к корню
  репозитория добавляет `app/_bootstrap.py` (импортируется первым в `texts.py`
  и `config.py`), в скриптах — `_cli.add_tool_api_path()`, в тестах —
  `tests/conftest.py`.

Итог: `grep -rn "def money" services/` даёт одну реализацию и две обёртки
(`layout.money` — оформление экрана, `scripts/import_finance.money` — разбор
суммы из таблицы, это не форматирование).

### 2. `scripts/_cli.py` — один вход для скриптов

`ROOT`/`ENV_FILE`/`TOOL_API`, `read_env` (python-dotenv: инлайн-комментарии
` #` режутся, как в shell), `apply_env`, `build_env`, `env`, `dsn`,
`api_url`, `add_tool_api_path`, `parser`, `add_write_mode`, `writes`,
`add_json_flag`, `add_allow_dev_write`.

Переведены: `alerts` (строку подключения передаёт точка входа),
`build_reports`, `check_invariants`, `cold_rotate`, `copy_finance_sheet`,
`feedback_digest`, `feedback_queue`, `fill_budget_sheet`, `import_finance`,
`import_schedule`, `import_students_schedule`, `read_finance_sheet`,
`reset_for_release`, `run_service` (`build_env` остаётся точкой входа для
`start_services`/`notify_admin`), `send_notifications`, `storage_backup`,
`storage_smoke`, `storage_up`, `sync_finance_sheet`, `reconcile`,
`intent_report`, `parse_colored_schedule`, `plan_reminders`,
`fill_test_schedule`, `calendar_fill_draft`, `check_router_scenarios`.

`dsn()` — единственное место, где решается, к какой базе идёт скрипт.
Источники по старшинству: переменная окружения → значение из `.env` → умолчание
локальной разработки (`DEFAULT_DSN`, порт 5433 и пароль контейнера postgres из
`docker-compose.yml`). `.env` функция читает сама, поэтому вызов `apply_env()`
перед `dsn()` не обязателен: у cron и в контейнере переменная уже в окружении,
у нового разработчика она есть только в файле. Пароль в умолчании — дефолт
контейнера, а не боевой: на сервере `DATABASE_URL` приходит из окружения.
Заглушка вида `svoi:***` недопустима — с ней скрипт без `.env` падал на
аутентификации (t_cfdc544f, держит `tests/unit/test_cli_dsn.py`).

Самодельные парсеры `"--apply" in sys.argv` заменены на `argparse`
(`add_write_mode`/`writes`): по умолчанию скрипт ничего не пишет, запись —
явный `--apply`. Наивный `read_env` в `storage_up.py` заменён на
`_cli.read_env`.

Итог: `grep -rn "load_dotenv" scripts/*.py` пуст (упоминание осталось только
в докстроке самого `_cli.py` как объяснение мотива).

### 3. JS: `SVOI.api()` и `SVOI.auth()` в `webapp-shell.js`

Оболочка получила два метода и отдаёт их через `window.SVOI`:

* `SVOI.api(path)` — GET; `SVOI.api(path, {поля})` — POST JSON;
  `SVOI.api(path, {method, body})` — готовая настройка fetch. Ответ всегда
  `{status, body}`, обрыв связи — `{status: 0, body: {}}`. Bearer и повтор
  после 401 подставляет уже существующая обёртка `fetch` в той же оболочке.
* `SVOI.auth()` — `true`, если сессия есть; иначе тихий перевыпуск по initData.

Страницы (`admin`, `legacy`, `misc`, `week` — два блока) оставили тонкие
обёртки с прежними именами, поэтому код страниц не тронут. Копий `api()`
в страницах и `fetch("/webapp/auth")` больше нет.

### 4. Попутно: откат подмен в тестах гейтвея

`tests/unit/gateway_modules.Namespace` — плоский вид модулей гейтвея после
разреза карты C. `monkeypatch.setattr(gw, "api", ...)` запоминает старое
значение через `target.__dict__.get(name)`, а у `Namespace` своего `__dict__`
не было: откат делал `delattr` вместо возврата функции, подмена оставалась в
модуле и роняла следующие тесты этого файла. Добавлен вид `__dict__`
(`_NamespaceView`) и безопасный `__delattr__` — тесты снова изолированы.

## Проверки

| Проверка | Результат |
|---|---|
| `python -m pytest` (unit + integration) | exit 0, падений нет |
| `python -m pytest tests/webapp` с `SVOI_WEBAPP_TESTS=1` | exit 0 (пропуски и xfail — по конфигурации) |
| `ruff check .` | All checks passed |
| `python scripts/check_invariants.py` | Все проверки пройдены |
| `python scripts/check_registers.py` | Все проверки пройдены, предупреждений 0 |
| `grep -rn "def money" services/` | одна реализация + тонкие обёртки |
| `grep -rn "load_dotenv" scripts/*.py` | нет |

## Замечания на будущее

* `scripts/import_finance.py:money` и `scripts/build_reports.py:money_range` —
  не форматирование, а разбор/сборка сумм в таблицах; к shared не относятся.
* `services/tool-api/app/pages/files.py` по-прежнему строит `fetch` с Bearer
  вручную для скачивания zip: там нужен `response.blob()`, а не JSON —
  общий `SVOI.api` на это не рассчитан.
