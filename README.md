# Театр-студия «СВОИ» — AI-ассистент в Telegram

Бот ведёт бюджет, занятость, взносы и расписание театра-студии. Люди работают в Telegram,
данные живут в Postgres, отчёты — в Google Sheets.

## Что это и чего здесь нет

Репозиторий — исходный код и документация бота, а не публичный сервис. Бот работает в одном
закрытом Telegram-чате театра: доступ выдаётся приглашением, регистрация — по коду от
организатора. Своего экземпляра для посторонних нет.

Данные театра в репозиторий не выкладываются: имена участников, суммы взносов, расписание и
переписка живут в боевой базе и в файлах Google на сервере. В репозитории — код, схема,
правила и примеры; боевые значения остаются на сервере. Секретов в индексе тоже нет: `.env`,
`creds.json` и `secrets/` закрыты `.gitignore`, в репозитории лежат только шаблоны.

## Статус

Требования и статусы — [`docs/development/19`](docs/development/19-functional-requirements.md);
документы — [`docs/README.md`](docs/README.md); схема и миграции — `db/init/*.sql`;
контракт — `contracts/openapi.json`.

Работает сквозной путь: участник отмечает занятость (файл, Mini App, чат) → бот сводит в БД →
организатор ищет окна (по людям, ролям, дороге, помещению) → назначает событие из формы или
текстом → событие попадает в календарь и расписание → участники получают уведомления.

Работает: доступ и права (RBAC: доступ выдаётся возможностями, роли множественные), бюджет (импорт, разбор, запись с идемпотентностью,
отчёты, доли, агрегаты), занятость (ввод, импорт, сводка), сверка файл ↔ БД, каскад разбора
(правила → модель → человек), календарь (чтение боевого, черновой контур, палитра).

Не закрыто: заметки и файлы, сверка месячных листов, system/e2e тесты, права педагогов
и режиссёра в интерфейсе, переезд на сервер (INT-005).

Состояние и выводы — [`docs/reports/27-audit-and-conclusions.md`](docs/reports/27-audit-and-conclusions.md).
Прод и переезд — [`docs/infrastructure/24-infrastructure-deploy.md`](docs/infrastructure/24-infrastructure-deploy.md).

## Состав

```
services/tool-api/      FastAPI — единственная точка доступа к данным, правилам, аудиту
services/tg-gateway/    aiogram 3 — транспорт: команды, кнопки, формы, состояние шага
                        (роутер свободного текста — POST /tools/route в tool-api, V-026)
bot/manifests/          SOUL.md, STYLE.md, roles/*, participants/* — поведение бота
db/init/                схема и миграции NNN_имя.sql, порядок — по имени файла
tests/                  unit (быстрые, без БД) и integration (svoi_test)
scripts/                импорт, отчёты, сверка, проверки, календарь, каскад
docs/                   группы: architecture, infrastructure, development, plans, reports, archive
```

## Требования

**Python 3.12** — одна версия на весь контур: проверка в CI (`.github/workflows/tests.yml`),
образы (`services/*/Dockerfile`), `requires-python` в `pyproject.toml`, `target-version` для
`ruff` и venv на сервере (`docs/infrastructure/35`). Версия закреплена в `.python-version`;
совпадение проверяет `tests/unit/test_toolchain_versions.py` — расхождение роняет тесты.
Окружение проекта — venv и `requirements-dev.txt`; команды установки — шаг 1 раздела
«Запуск (локально)».

### Хуки перед коммитом и push

`git commit` сам прогоняет `ruff check` по staged-файлам и
`scripts/check_invariants.py`: коммит с неиспользуемым импортом или с нарушенным
инвариантом не создаётся. Хуки лежат в репозитории (`.githooks/pre-commit`,
`.githooks/pre-push`), включаются один раз на клон:

```bash
git config core.hooksPath .githooks
```

`git clone` сам хуки не включает — на новой машине команду выполняют один раз.
Без этой настройки git смотрит в `.git/hooks` и оба хука не работают: ни ruff с
инвариантами, ни защита основной ветки. Проверить, что включено:
`git config --get core.hooksPath` (должно быть `.githooks`).

`pre-push` закрывает основную ветку: в контексте воркера (задана
`HERMES_KANBAN_TASK` или `HERMES_PROFILE=worker-*`) push в `main`/`master`
отклоняется — воркер пушит ветку `wt/<task-id>`, а `main` собирает
`worker-review`. Самому ревьюеру (`HERMES_PROFILE=worker-review`) `main`
разрешён без предупреждения: это его работа. Человеку push в `main` разрешён
с предупреждением, push ветки вне схемы `wt/<task-id>`/`card-*` —
предупреждение без блокировки. Правило — `docs/development/26` §7a п.9.

Проверка инвариантов требует Postgres: если база не поднята, она пропускается
с сообщением, коммит проходит. Обойти проверки: `git commit --no-verify`,
`git push --no-verify`.

Postgres 16 (на :5433) — из `docker compose`, см. ниже. n8n в контуре больше нет (V-026).

## Запуск (локально)

По шагам, с чистой машины. Нужны `git`, Docker с compose v2 и Python 3.12.

**1. Клон и окружение Python**

```bash
git clone <адрес репозитория> teatrsvoi_bot && cd teatrsvoi_bot
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements-dev.txt      # рантайм + pytest/ruff/playwright
.venv/bin/python -m playwright install chromium    # движок для tests/webapp
```

Хуки перед коммитом включаются один раз — командой из раздела «Хуки перед коммитом и push».

**2. Файлы окружения**

В репозитории два шаблона, и они не взаимозаменяемы:

```bash
cp .env.dev.example .env.dev   # среда dev: локальные сервисы на маке, dev-бот, локальная БД
cp .env.example .env           # файл для docker compose (сервисы compose читают его через env_file)
chmod 600 .env .env.dev
```

`DEV_BOT_TOKEN` — токен отдельного бота «СВОИ dev» из BotFather, вписать в `.env.dev`. Без него
локальный запуск отказывается стартовать, а прод-токен в dev-контуре не читается вовсе
(`docs/infrastructure/08` §1a). Для варианта 4-А (сервисы на маке) этого достаточно:
остальные значения в шаблоне — dev-овские по умолчанию. Для варианта 4-Б (весь контур в Docker)
токен бота и chat_id администратора вписываются в `.env`.

**3. База**

```bash
docker compose up -d postgres migrate
```

Postgres 16 на `127.0.0.1:5433`; сервис `migrate` накатывает схему Alembic. Ревизии — обёртки
над `db/init/*.sql` (`scripts/gen_alembic_revisions.py`), поэтому postgres при создании тома
ничего не инициализирует. Повторный запуск `migrate` ничего не применяет.

**4. Сервисы: выберите один из двух вариантов**

Варианты A и B взаимоисключающие: оба занимают порт `:8000` и поднимают поллер бота.
Выполняйте **4-А или 4-Б**, не оба подряд. Если один уже запущен, остановите его
(Ctrl+C в терминале с `run_service.py`; для контура в Docker — `docker compose down`)
и только потом переходите к другому.

*4-А. Сервисы на маке* — в двух терминалах, каждый читает `.env.dev`:

```bash
.venv/bin/python scripts/run_service.py tool-api    # FastAPI на :8000
.venv/bin/python scripts/run_service.py gateway     # aiogram, long polling
.venv/bin/python scripts/run_service.py gateway --check   # показать среду и выйти
```

*4-Б. Весь контур в Docker* — вместо 4-А, не после него:

```bash
docker volume create s3-storage_gardata   # том Garage объявлен external и сам не создаётся
docker compose up -d
```

Поднимаются `tool-api` (:8000), `tg-gateway`, `searxng` (:8080, только с 127.0.0.1) и `garage`
(:3900, локальное хранилище файлов). Хранилище настраивает `scripts/storage_up.py`: генерирует
секрет RPC и ключ, создаёт бакеты и дописывает значения в `.env`.

Почему не подряд: оба варианта занимают один и тот же порт `127.0.0.1:8000` (uvicorn из
`run_service.py` и `ports: 8000:8000` в `docker-compose.yml`) и поднимают поллер бота. Два
сервиса на одном порту не работают вместе: запросы к порту достаются только тому, кто занял его
первым, второй фактически недоступен. Если в `.env` вписан тот же токен бота, что в `.env.dev`,
вдобавок два поллера будут отбирать друг у друга обновления — `getUpdates` отдаёт апдейт
только одному из них.

Чего в репозитории нет и быть не может: токена бота, chat_id администратора и доступа к файлам
Google (`GOOGLE_SA_JSON`, `*_SPREADSHEET_ID`, `CALENDAR_ID_*`). Без них контур поднимается не
целиком: `tg-gateway` без `TELEGRAM_TOKEN` не стартует, функции работы с Google-файлами
недоступны. Это нормально: бот обслуживает конкретный театр, а не публичный сервис.

## Проверки и отчёты

Перед merge в `main` гейт целиком — одной командой из корня репозитория:

```bash
env -u PYTHONPATH .venv/bin/python scripts/check_done.py            # полный прогон, включая браузерные тесты Mini App
env -u PYTHONPATH .venv/bin/python scripts/check_done.py --quick    # без браузерных, итог помечается явно
env -u PYTHONPATH .venv/bin/python scripts/check_done.py --json     # машинный отчёт: шаг, код, секунды
```

Скрипт идёт по шагам фиксированного порядка (`ruff`, полный `pytest`, браузерные
тесты Mini App, инварианты, реестры, deadcode, карты канбана), останавливается на
первом падении с кодом 1 и печатает итоговую строку. Описание шагов —
[`docs/development/26-process-and-git.md`](docs/development/26-process-and-git.md) §4.
Postgres и миграции он не поднимает: окружение готовится заранее.

Отдельные команды для точечной работы:

```bash
python scripts/check_invariants.py     # бизнес-инварианты из docs/architecture/11, код выхода 1 при нарушении
python scripts/build_reports.py        # месячные листы бюджета, матрицы занятости, журнал, проверки
python scripts/compose_manifest.py --role org --participant иванов   # что уйдёт в LLM
```

## Ключевые документы

| Документ | О чём |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | границы, ADR, деградация, безопасность |
| [`docs/architecture/11-business-rules.md`](docs/architecture/11-business-rules.md) | правила и инварианты — контракт с театром |
| [`docs/development/12-bot-behavior.md`](docs/development/12-bot-behavior.md) | манифесты, формы, квоты, что где настраивается |
| [`docs/development/13-registration.md`](docs/development/13-registration.md) | вход admin / org / студиец |
| [`docs/development/14-files-and-routing.md`](docs/development/14-files-and-routing.md) | файлы, БД, маршрутизация |
| [`docs/archive/15-checkpoint.md`](docs/archive/15-checkpoint.md) | план / факт / остаток |
| [`docs/development/16-test-strategy.md`](docs/development/16-test-strategy.md) | стратегия и требования к тестированию |
| [`docs/architecture/17-business-algorithms.md`](docs/architecture/17-business-algorithms.md) | алгоритмы работы с данными |
| [`docs/archive/18-mini-app.md`](docs/archive/18-mini-app.md) | Telegram Mini Apps |
| [`docs/development/19-functional-requirements.md`](docs/development/19-functional-requirements.md) | функциональные требования и статусы |
| [`docs/development/20-scenarios.md`](docs/development/20-scenarios.md) | пользовательские сценарии |
| [`docs/development/21-intent-cascade.md`](docs/development/21-intent-cascade.md) | каскад разбора: правила → модель → человек |
| [`docs/development/22-schedule-fill-rules.md`](docs/development/22-schedule-fill-rules.md) | заполнение расписания цветом |
| [`docs/development/23-ux-scenarios.md`](docs/development/23-ux-scenarios.md) | пользовательский опыт и сценарии |
| [`docs/infrastructure/24-infrastructure-deploy.md`](docs/infrastructure/24-infrastructure-deploy.md) | прод и переезд с локали |
| [`docs/architecture/25-architecture-evolution.md`](docs/architecture/25-architecture-evolution.md) | что внедрить, развить, упразднить |
| [`docs/development/26-process-and-git.md`](docs/development/26-process-and-git.md) | процесс работы с кодом и git |
| [`docs/reports/27-audit-and-conclusions.md`](docs/reports/27-audit-and-conclusions.md) | аудит и главные выводы |
| [`docs/development/28-use-cases.md`](docs/development/28-use-cases.md) | варианты использования и покрытие тестами |
| [`docs/infrastructure/75-hosting-and-migration.md`](docs/infrastructure/75-hosting-and-migration.md) | «Найти домик для Бота Театра»: сервер, домен, TLS, переезд, переключение поллера |

## Правила, которые нельзя нарушать

1. `tool-api` — единственный, кто ходит в Postgres и Google.
2. Истина — там, куда человек внёс данные руками (файл). БД — индекс и зеркало;
   расхождение выносится человеку на лист «Сверка», а не «подкручивается».
3. Запись в бюджет только после подтверждения кнопкой.
4. Числа в ответе бота берутся из инструмента, иначе ответ «не могу подтвердить».
5. Эмодзи в сообщениях и кнопках не используем.

## Безопасность

Находку об уязвимости присылайте приватно: вкладка **Security** → **Report a vulnerability**
в GitHub. Публичный issue с деталями уязвимости не открываем ни мы, ни автор находки.
Границы проверки и правило раскрытия — [`SECURITY.md`](SECURITY.md).

## Лицензия

MIT — полный текст в [`LICENSE`](LICENSE). Код можно использовать, менять, распространять и
продавать, в том числе в закрытых форках; гарантий нет, ответственность за использование —
на том, кто использует.

