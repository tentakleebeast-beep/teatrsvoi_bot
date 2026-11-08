# План: единый источник схемы БД (Alembic поверх `db/init/*.sql`)

Класс: architecture. Технический план к решению из карты `t_fee373f5` (комментарий 518).
Код, миграции и `db/init` в этой карте не меняются.

## 1. Снимок

- коммит: `d3d658f5de0f` (2026-11-05 07:10:59 +0400, `main`)
- дата замера: 2026-11-05
- что запускалось:
  - `env -u PYTHONPATH .venv/bin/python scripts/arch_drift.py`
  - `ls db/init/*.sql | wc -l`, `wc -l db/init/*.sql`
  - `grep -ci '^begin;' db/init/*.sql`, `grep -ci '^commit;' db/init/*.sql`
  - `grep -n "init_db\|core.DDL" services/tool-api/app/*.py`
  - `wc -l services/tool-api/app/core.py services/tool-api/app/main.py tests/conftest.py deploy/server-deploy.sh`
  - чтение `docker-compose.yml`, `services/tool-api/Dockerfile`, `deploy/server-deploy.sh`,
    `tests/conftest.py`, `.github/workflows/tests.yml`, `docs/infrastructure/75`

## 2. Числа

| Метрика | Сейчас | Прошлый замер (111, 1a9f7ec) | Порог | Статус |
|---|---|---|---|---|
| Источников схемы | 3 (`db/init`, `core.DDL`, `migration_marker`) | — | 1 | warn |
| Файлов схемы `db/init/*.sql` | 57 (2 863 строки) | — | — | факт |
| `core.DDL` | 60 строк (`core.py:172-231`), выполняется на старте | — | 0 | warn |
| `core.py` | 794 строки | 785 | 500/800 | warn |
| `main.py` | 254 строки | — | 500 | ok |
| Файлов с `BEGIN;`/`COMMIT;` | 45 из 57 | — | — | риск для Alembic |
| Файлов, заканчивающихся `SELECT` | 40 | — | — | риск для `op.execute` |
| `ALTER TABLE` без `IF NOT EXISTS` | 68 вхождений | — | — | риск для повторного прогона |
| Миграций с `UPDATE`/`DELETE` (данные) | 15 файлов | — | — | риск при повторе |
| `migration_duplicates` | 0 | 2 | 0 | ok |
| `migration_gaps` | 1 (номер 050) | 0 | 0 | warn |
| Применение на существующей БД | только вручную `psql` (initdb.d — лишь пустой том) | то же | — | warn |
| `files_over_500` / `files_over_800` | 12 / 5 | 9 / 3 | 0 | warn |

Дельта с 111: дубли номеров закрыты (046/047 переименованы в 053/047), появился пропуск 050
(переименование 046 → 053), `core.py` +9 строк. Остальные метрики не изменились по существу.

Отдельно зафиксировано: **прод запускает tool-api из venv через systemd**
(`deploy/systemd/svoi-tool-api.service`, `ExecStart=/opt/svoi/.venv/bin/python -m uvicorn …`),
Postgres — в контейнере `svoi-postgres`. Compose — только локальный контур. Поэтому «одна
команда» должна работать в двух исполнениях: venv на проде и образ в compose/CI.

## 3. Целевое состояние (из решения 518, кратко)

- Схема живёт только в `db/init/*.sql`; ни `core.DDL`, ни второго описания нет.
- Применение — `alembic upgrade head` в compose (`migrate`), на проде (venv) и в тестах.
- Каждая ревизия Alembic — обёртка над конкретным `db/init/NNN_*.sql`; SQL не переписывается.
- Приложение на старте схему не создаёт: read-only проверка `alembic_version` = head, иначе
  отказ старта с понятным сообщением.
- `migration_marker` остаётся журналом опасных data-миграций внутри SQL-файлов, но перестаёт
  быть механизмом учёта применённого: учёт — `alembic_version`.

Ключевое техническое правило, без которого единый источник снова разъедется:
**применённые файлы `db/init/NNN_*.sql` неизменяемы.** Правка схемы — новый файл с новым
номером и новой ревизией. Хэш каждого файла записан в ревизии и проверяется при накате —
случайная правка старого файла валит накат, а не молча меняет историю.

## 4. План по шагам

Порядок выбран так, чтобы каждый шаг оставлял дерево зелёным (`scripts/check_done.py`).

### Шаг 0. Спайк на одноразовом окружении (0,5–1 ч)

Цель — снять два технических риска до правок: многооператорный SQL и блокировка.

```bash
cd "/Users/viktormamlutov/repos/Projects/teatrsvoi_bot Project"
uv venv /tmp/alembic-spike
uv pip install --python /tmp/alembic-spike/bin/python alembic==1.20.1 SQLAlchemy==2.1.8 "psycopg[binary]==3.3.6"
```

Проверить на копии БД (`svoi_migr`, см. §5.2):
1. `op.get_bind().exec_driver_sql(<файл без BEGIN/COMMIT>)` применяет файл целиком (001 и 016).
2. Файл с `BEGIN;`/`COMMIT;` внутри транзакции Alembic ломает учёт версии — подтвердить и
   зафиксировать, что вырезание этих строк обязательно.
3. `select pg_advisory_lock(7311, 1)` на соединении `env.py` держится между коммитами ревизий.

Если п.1 не проходит на `exec_driver_sql`, запасной вариант — `op.get_bind().connection.driver_connection.cursor().execute(sql)`;
решение фиксируется в `migrations/sql.py` и в отчёте реализации.

### Шаг 1. Зависимости и каркас Alembic (2 ч)

Добавить в `requirements.txt` (точные версии проверены на PyPI 05.11.2026):

```
alembic==1.20.1
SQLAlchemy==2.1.8
```

`requirements-dev.txt` подключает рантайм по ссылке — правок не требует.

Новые файлы:

| Файл | Назначение |
|---|---|
| `alembic.ini` | `script_location = services/tool-api/migrations`, `prepend_sys_path = .`, `sqlalchemy.url =` пустой (URL берётся из `DATABASE_URL`), секции logging |
| `services/tool-api/migrations/env.py` | offline/online; URL из `config.attributes["url"]` (тесты) или `DATABASE_URL`; `postgresql://` → `postgresql+psycopg://`; `create_engine(..., poolclass=NullPool)`; advisory lock `pg_advisory_lock(7311, 1)` на соединении; `transaction_per_migration=True`; unlock в `finally` |
| `services/tool-api/migrations/sql.py` | `INIT_DIR` = `<корень>/db/init` (через `Path(__file__).resolve().parents[3]`), `run_sql(op, filename, sha256)`: проверка хэша, вырезание строк `^\s*(begin|commit)\s*;\s*$`, выполнение |
| `services/tool-api/migrations/script.py.mako` | шаблон ревизии (down_revision, docstring с именем SQL-файла, `NotImplementedError` в downgrade) |

`downgrade()` — осознанно не поддерживается: миграции аддитивные, откат кода не откатывает
базу (`docs/infrastructure/75` §10.2). В ревизии — явный `raise NotImplementedError`.

### Шаг 2. 57 ревизий генератором (1,5–2 ч)

Новый файл `scripts/gen_alembic_revisions.py` (dev-инструмент, в образ не копируется):

- читает `sorted(db/init/*.sql)`, для каждого пишет
  `services/tool-api/migrations/versions/<stem>.py`;
- `revision = "<stem>"` (например `016_availability_invert`), `down_revision` — предыдущий
  stem, у первого `None`; цепочка линейная, порядок = порядок имён файлов;
- в ревизии: `SQL_FILE`, `SQL_SHA256`, `upgrade() → run_sql(op, SQL_FILE, SQL_SHA256)`;
- печатает цепочку и head для сверки.

Запуск:

```bash
env -u PYTHONPATH .venv/bin/python scripts/gen_alembic_revisions.py
env -u PYTHONPATH .venv/bin/alembic -c alembic.ini history | head -5
env -u PYTHONPATH .venv/bin/alembic -c alembic.ini heads
```

Ожидание: 57 ревизий, один head `060_participant_role_revoked`, пропусков номеров в цепочке
нет (номера 018/050/059 отсутствуют и в `db/init`).

Проверка соответствия 1:1: `ls services/tool-api/migrations/versions/*.py | wc -l` = 57.

### Шаг 3. Приложение перестаёт создавать схему (1–1,5 ч)

Удаляется:
- `services/tool-api/app/core.py:172-231` — `DDL` и `init_db()` (60 строк);
- вызов `core.init_db()` в `services/tool-api/app/main.py:202`.

Добавляется `services/tool-api/app/schema_version.py` (~35 строк):
- читает head из `ScriptDirectory.from_config(Config(<project_root>/alembic.ini))`;
- `select version_num from alembic_version` (read-only, без DDL);
- нет таблицы/строки или версия ≠ head → `RuntimeError` с текстом
  «схема не применена (version=<…>, head=<…>); выполните `alembic upgrade head`».

`main.py::_startup` после `core.pool.open(...)` вызывает `check_schema_version()`.
Путь к `alembic.ini` — через существующий `app.version.project_root()` (в образе это `/app`).

### Шаг 4. Compose и образ (1–1,5 ч)

`docker-compose.yml`:
- удалить `- ./db/init:/docker-entrypoint-initdb.d:ro` у `postgres` (работает только на пустом томе);
- добавить one-shot сервис `migrate`: тот же образ, `env_file: .env`, `DATABASE_URL` на
  `postgres:5432`, `depends_on: postgres: condition: service_healthy`,
  `command: ["alembic","-c","alembic.ini","upgrade","head"]`, `restart: "no"`;
- у `tool-api`: `depends_on: migrate: condition: service_completed_successfully`
  (зависимость от `postgres` остаётся через `migrate`).

`services/tool-api/Dockerfile` — добавить после существующих `COPY`:

```dockerfile
COPY alembic.ini ./alembic.ini
COPY services/tool-api/migrations ./services/tool-api/migrations
COPY db/init ./db/init
```

`db/init` нужен в образе: ревизии читают SQL-файлы (единый источник), а не хранят копию.

### Шаг 5. Тесты (2–2,5 ч)

`tests/conftest.py:146-149`: вместо цикла `for sql_file in sorted(...): conn.execute(...)` —
создание БД (как сейчас) и `alembic.command.upgrade(cfg, "head")`:

```python
cfg = Config(str(ROOT / "alembic.ini"))
cfg.attributes["url"] = _test_dsn()          # env.py читает attributes, окружение не подменяем
command.upgrade(cfg, "head")
```

Окружение не мутируется — это правило conftest (комментарий в шапке) сохраняется.

Новые тесты:

| Файл | Что проверяет |
|---|---|
| `tests/unit/test_migrations_registry.py` | 57 ревизий = 57 файлов `db/init`; цепочка линейная и без ветвлений; один head; `SQL_SHA256` совпадает с файлом; в `core` нет `DDL`/`init_db`; в `docker-compose.yml` есть `migrate` и нет mount `db/init` |
| `tests/integration/test_migrations.py` | на `svoi_test_<pid>`: `alembic_version` — ровно одна строка = head; повторный `upgrade head` — no-op; схема содержит маркеры поздних файлов (`participant.is_active`, `participant_role.revoked_at`, `intent_log.steps`); `deploy/check_schema.py` возвращает 0 |

### Шаг 6. Деплой (1,5 ч)

`deploy/server-deploy.sh`, функция `cmd_db` (строки 217–276):
- удалить цикл `for file in "$REPO_DIR"/db/init/*.sql; do … psql … done` (259–270);
- на чистой БД и при `--migrate`:
  `as_user env DATABASE_URL="$(app_env DATABASE_URL)" "$REPO_DIR/.venv/bin/alembic" -c "$REPO_DIR/alembic.ini" upgrade head`;
- при `--restore`: после `pg_restore` — `deploy/check_schema.py --expect-head`; если схема
  равна цепочке — `alembic stamp head`, иначе `die` со списком расхождений и инструкцией
  (ручной `stamp`/точечный файл), без «на всякий случай upgrade».

`deploy/check_schema.py`: добавить read-only флаг `--expect-head` (сверка `alembic_version`
с head через `ScriptDirectory`, 15–20 строк). `cmd_verify` и `cmd_schema` (строки 318–345)
вызывают его с флагом: расхождение версии останавливает выкат до перезапуска служб.

`docs/infrastructure/75-hosting-and-migration.md`:
- §5.1 строка 509: «33 файла» → «57 файлов»;
- §7.1: команды `db --migrate` / `db --restore` описать через Alembic; убрать рецепт
  «накатывайте точечно psql-ом» как основной (оставить как аварийный);
- §10.1: `db --migrate` = `alembic upgrade head`; правило «применённые файлы не правим»;
- новый подраздел «Baseline существующей БД»: backup → копия → сверка schema-only → `stamp head`.

### Шаг 7. CI (1 ч, вне заявленной области — нужен явный ОК владельца)

`.github/workflows/tests.yml`, job `images` (строки 219–242): убрать
`-v "$PWD/db/init:/docker-entrypoint-initdb.d:ro"`, после старта `pg-ci` выполнить
`docker run --rm --network svoi-ci -e DATABASE_URL=… svoi-tool-api:ci alembic -c alembic.ini upgrade head`.
Иначе job соберёт образ, поднимет API на пустой БД и упадёт на fail-fast — что правильно,
но сообщение будет про схему, а не про забытый накат.

`scripts/check_services.py` / `deploy-check.sh` — проверить, что не ждут файлов в `db/init`;
если ждут — правка в этом же шаге.

### Шаг 8. Проверки (2 ч, входит в цену)

Все сценарии — §5. Отдельно: прогон на копии dev-БД и на «грязной» базе (см. риск 1).

### Что удаляется и что добавляется — сводка

Удаляется:
- `core.DDL`, `core.init_db()` (`core.py:172-231`);
- `core.init_db()` в `main.py:202`;
- mount `./db/init:/docker-entrypoint-initdb.d` в `docker-compose.yml`;
- цикл `psql` по файлам в `deploy/server-deploy.sh:259-270`;
- прямое чтение `db/init/*.sql` в `tests/conftest.py`.

Добавляется:
- `alembic.ini`, `services/tool-api/migrations/{env.py,sql.py,script.py.mako,versions/*.py}`;
- `services/tool-api/app/schema_version.py`;
- `scripts/gen_alembic_revisions.py`;
- `migrate` в compose, `COPY` в Dockerfile;
- `--expect-head` в `deploy/check_schema.py`;
- два тест-файла.

Не меняется: 57 файлов `db/init/*.sql` (ни один байт), схема БД, API-контракт
(`contracts/openapi.json`), `migration_marker` и его записи.

## 5. Проверки

### 5.1 Пустая БД (локально, только dev-контур)

```bash
cd "/Users/viktormamlutov/repos/Projects/teatrsvoi_bot Project"
# ВНИМАНИЕ: -v удаляет локальный том pgdata. На проде не выполнять.
docker compose down -v
docker compose up -d postgres
docker compose run --rm migrate          # ожидание: exit 0, «Running upgrade … -> 060_…»
docker compose up -d tool-api tg-gateway
curl -fsS http://127.0.0.1:8000/health   # ожидание: "ok":true
set -a; . ./.env; set +a
env -u PYTHONPATH .venv/bin/python deploy/check_schema.py --expect-head
# ожидание: «схема совпадает …», exit 0
```

### 5.2 Существующая БД (копия dev, без риска для рабочей)

```bash
docker exec svoi-postgres createdb -U svoi svoi_migr
docker exec svoi-postgres pg_dump -U svoi -Fc svoi | docker exec -i svoi-postgres pg_restore -U svoi -d svoi_migr --no-owner
docker exec svoi-postgres createdb -U svoi svoi_check

# эталон: пустая база, накатанная цепочкой
DATABASE_URL=postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi_check \
  env -u PYTHONPATH .venv/bin/alembic -c alembic.ini upgrade head

# сверка структуры: отличается только наличием alembic_version в эталоне
docker exec svoi-postgres pg_dump -U svoi --schema-only --no-owner --no-privileges svoi_migr  > /tmp/migr.sql
docker exec svoi-postgres pg_dump -U svoi --schema-only --no-owner --no-privileges svoi_check > /tmp/check.sql
diff /tmp/migr.sql /tmp/check.sql

# baseline только после пустого diff (кроме таблицы alembic_version)
DATABASE_URL=postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi_migr \
  env -u PYTHONPATH .venv/bin/alembic -c alembic.ini stamp head
DATABASE_URL=… .venv/bin/alembic -c alembic.ini current   # ожидание: 060_… (head)
```

Если diff непустой — **не ставить `stamp head`**: расхождения перечислить и решить
(точечный файл / `stamp` на предыдущую ревизию + upgrade). Это и есть проверка «контракты
не поехали» для схемы.

### 5.3 Повторный запуск

```bash
docker compose run --rm migrate    # второй раз: exit 0, ни одной ревизии не применено
docker compose run --rm migrate    # третий раз — то же
docker exec svoi-postgres psql -U svoi -d svoi -Atc "select count(*) from alembic_version"
# ожидание: 1
```

### 5.4 Параллельный запуск

```bash
docker exec svoi-postgres createdb -U svoi svoi_par
export DATABASE_URL=postgresql://svoi:svoi_dev_password@127.0.0.1:5433/svoi_par
( env -u PYTHONPATH .venv/bin/alembic -c alembic.ini upgrade head; echo "первый: $?" ) &
( env -u PYTHONPATH .venv/bin/alembic -c alembic.ini upgrade head; echo "второй: $?" ) &
wait
docker exec svoi-postgres psql -U svoi -d svoi_par -Atc "select version_num from alembic_version"
# ожидание: оба exit 0, одна строка 060_…; второй ждал advisory lock
```

### 5.5 Тесты и гейт

```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_migrations_registry.py tests/integration/test_migrations.py -q
env -u PYTHONPATH .venv/bin/python -m pytest -q --ignore=tests/webapp
env -u PYTHONPATH .venv/bin/python scripts/check_done.py
```

### 5.6 Прод (только владелец, после приёмки)

1. Бэкап: `deploy/backup.sh` / `pg_dump -Fc`.
2. `server-deploy.sh app` (ставит `alembic` в venv), затем на копии прод-дампа — §5.2.
3. На проде: `check_schema.py --expect-head` → при чистом diff `alembic stamp head` →
   `server-deploy.sh db --migrate` → `systemctl restart svoi-tool-api` → `/health`.
4. До выката остановить таймеры (`svoi-invariants`, `svoi-backup`, `svoi-conflict*`), чтобы
   они не читали базу между ревизиями; включить после `/health`.

Откат кода: предыдущий тег работает с новой базой — старый `init_db()` делает только
`create table if not exists` по подмножеству схемы, лишняя таблица `alembic_version` ему не
мешает. Откат базы не предусмотрен (решение 518).

## 6. Цена и риски

| Шаг | Работа | Часы |
|---|---|---|
| 0 | спайк (multi-statement, advisory lock) | 0,5–1 |
| 1 | зависимости, `alembic.ini`, `env.py`, `sql.py` | 2 |
| 2 | генератор и 57 ревизий, сверка цепочки | 1,5–2 |
| 3 | удаление DDL/`init_db`, проверка версии на старте | 1–1,5 |
| 4 | compose `migrate`, Dockerfile | 1–1,5 |
| 5 | conftest + два тест-файла | 2–2,5 |
| 6 | deploy-скрипт, `--expect-head`, docs/75 | 1,5 |
| 7 | CI `images` (вне области, по ОК) | 1 |
| 8 | проверки §5.1–5.5 на копиях | 2 |
| | **Итого** | **12–16** |

Нижняя граница — если спайк и baseline проходят без сюрпризов; верхняя — если прод-копия
даст расхождения и понадобится разбор.

| # | Риск | Где | Как снижаем |
|---|---|---|---|
| 1 | Прод/dev-схема ≠ чистая цепочка (в docs/75 уже описан случай: 004 упирается в ослабленную уникальность; 30.10 на проде не хватало колонок) | прод, dev | `stamp head` только после пустого `pg_dump --schema-only` diff (§5.2); иначе `stamp` на предыдущую и `upgrade` |
| 2 | `BEGIN;`/`COMMIT;` внутри 45 файлов ломают транзакцию Alembic и учёт версии | все контуры | вырезание строк в `sql.py`, проверено спайком; SQL-файлы не правим |
| 3 | Многооператорный `exec_driver_sql` + 40 файлов с хвостовым `SELECT` | все | спайк на 001/016 до правок; запасной путь — сырой курсор psycopg |
| 4 | Новая зависимость SQLAlchemy (~+10 МБ в образ и venv) | прод, CI | версии закреплены; ORM не используем, только движок Alembic |
| 5 | Fail-fast на старте: разработчик поднял `tool-api` без `migrate` — сервис в рестарт-лупе (`Restart=always`, `RestartSec=5`) | dev, прод | сообщение с командой; `docker compose up` включает `migrate`; в docs/75 — первый шаг |
| 6 | Забытый `alembic.ini`/`migrations`/`db/init` в образе — падение на старте | CI, compose | `COPY` в Dockerfile + job `images` ловит |
| 7 | Гонка двух `upgrade` (деплой + таймер/ручной запуск) | прод | advisory lock `pg_advisory_lock(7311, 1)` в `env.py`, проверка §5.4 |
| 8 | Правка применённого SQL-файла молча меняет историю | все | SHA256 в ревизии + тест; правило в docs/75 |
| 9 | Частичное применение при падении посередине | все | `transaction_per_migration=True`: каждая ревизия коммитится целиком; повторный запуск продолжает с первой неприменённой |
| 10 | `--restore` дампа, снятого до Alembic: нет `alembic_version` | прод | baseline после сверки, не «upgrade вслепую» |

Что может сломаться в dev: `docker compose down -v` (удаляет `pgdata`) — использовать только
осознанно; привычка «поднял API — схема сама появилась» исчезает.
Что может сломаться в prod: сервис не стартует после выката, если версия не выставлена;
лечится `alembic stamp head`/`upgrade head` по инструкции, код не откатывается.

## 7. Что в эту карту не входит

- Код, миграции, правки `db/init` — следующий шаг делает `worker-feature` по этому плану.
- Принятие решения «делаем/не делаем» и приоритет — владелец.
- Выкат на прод, прод-секреты, остановка таймеров — владелец отдельной картой.
- Заполнение/документирование пропуска номера 050 (`migration_gaps = 1`) — отдельная мелочь,
  к единому источнику не относится.
- Переписывание SQL-файлов под идемпотентность (`ALTER TABLE … IF NOT EXISTS`, 68 вхождений):
  Alembic-учёт снимает необходимость, файлы остаются как есть.
- Разрезание `core.py` (794 строки) сверх удаления DDL — отдельная карта.
- Autogenerate из моделей/ORM: моделей нет, источник — SQL.
- `downgrade` и откат данных.
- Правки CI (шаг 7) — вне заявленной области, включаются только с явного ОК владельца.
- Документы, кроме `docs/infrastructure/75` (24, 35, architecture/01 проверяются на ссылки,
  но не переписываются здесь).

## 8. Что техлид не трогал и почему

Код, `db/init`, `docker-compose.yml`, деплой-скрипты и прод не менялись: у роли нет задачи
«сделать», продукт — план с ценой и рисками. Числа §2 воспроизводимы командами из §1.
Решения по объёму (включать ли шаг 7, когда делать) остаются за владельцем.

## 9. Отличия исполнения от плана

Записано по факту реализации (карта `t_dbeef053`), чтобы план не расходился с кодом.

| Пункт плана | Как сделано | Почему |
|---|---|---|
| шаг 1: `script_location = services/tool-api/migrations`, `prepend_sys_path = .` | `%(here)s/services/tool-api/migrations`, `prepend_sys_path = %(here)s` | относительный путь считается от cwd: `alembic -c <abs>/alembic.ini` из `/tmp` падал «Path doesn't exist»; `%(here)s` — каталог ini |
| §5.2: копия базы через `pg_dump \| pg_restore` | `createdb -T svoi svoi_migr` | `pg_restore` молча теряет FK, если данные их нарушают: в dev 69 строк `budget_tx.category_id` и 17 `project_id` без пары, копия осталась с 70 FK вместо 72. Ловушка описана в docs/75 §7.1.1 |
| шаг 6: `--expect-head` сверяет схему с цепочкой | сверяет только номер версии (read-only) | структуру сравнивать нечем без полного дампа; для `--restore` это осознанно: легаси-дамп без версии останавливает восстановление, структура сверяется по §7.1.1 |
| шаг 7: job `images` перевести на `alembic upgrade head` | не тронут (вне области без ОК владельца) | зафиксировано в docs/75 §7.1.2: job с mount `db/init` даёт базу без версии, образ на ней не стартует; правка — отдельной картой |
| §5.1: проверка через `docker compose run --rm migrate` | тот же образ тем же CMD на отдельной базе | `run --rm migrate` на dev-томе накатил бы цепочку на `svoi`, а чистая проверка требует `down -v`, который удаляет локальный `pgdata` |
