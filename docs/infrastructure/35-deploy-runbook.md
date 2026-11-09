# 35. Runbook развёртывания на удалённом сервере

> **n8n выведен из контура (V-026, 01.11.2026).** Где ниже n8n назван частью контура или
> источником разбора свободного текста, читать: `POST /tools/route` в tool-api
> (`services/tool-api/app/router_llm.py`); воркфлоу, публичный вебхук и переменные `N8N_*`
> удалены из деплоя. История контрактов — `docs/archive/29-n8n-scenarios.md`.

Практическая инструкция: от чистого VPS до работающего бота. Основание — `docs/infrastructure/24`
(архитектура прода). Здесь только команды и проверки, без теории.

Ориентир по времени: 1,5–2 часа при первом запуске, из них половина — ожидание
DNS и установка пакетов.

---

## 0. Что понадобится заранее

- [ ] VPS: 2 vCPU, 4 ГБ RAM, 60 ГБ SSD, Ubuntu 24.04 LTS
- [ ] домен, например `svoi.example.ru` (A-запись на IP сервера)
- [ ] доступ к репозиторию (deploy key или токен)
- [ ] `secrets/google-sa.json` — ключ сервисной учётк��и Google
- [ ] `TELEGRAM_TOKEN` бота от @BotFather
- [ ] `ADMIN_CHAT_ID` (ваш chat_id, узнать можно у @userinfobot)
- [ ] дамп локальной базы для переноса данных

---

## 1. Подготовка сервера

```bash
# локально
ssh root@<IP>

adduser svoi
usermod -aG sudo svoi

# ключ для деплоя (с локальной машины)
ssh-copy-id svoi@<IP>

# дальше — под пользователем svoi
ssh svoi@<IP>
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12-venv python3-pip git ufw fail2ban curl

sudo timedatectl set-timezone Europe/Samara
sudo ufw allow 22/tcp
sudo ufw allow 80,443/tcp
sudo ufw --force enable
```

Проверка: `sudo ufw status` → активен, открыты только 22, 80, 443.

### Docker (для Postgres и n8n)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker svoi
newgrp docker
docker --version
```

---

## 2. Код и окружение

```bash
sudo mkdir -p /opt/svoi && sudo chown svoi:svoi /opt/svoi
git clone git@github.com:<аккаунт>/teatrsvoi-bot.git /opt/svoi
cd /opt/svoi

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

Версия Python одна на весь контур — **3.12** (`.python-version`, `requires-python`, CI, образы,
локальный venv, `README.md`). На Ubuntu 24.04 системный `python3` — как раз 3.12, поэтому
отдельная установка не нужна; после создания venv проверьте: `.venv/bin/python -V` → 3.12.x.
Расхождение ловит `tests/unit/test_toolchain_versions.py`.

Если `requirements.txt` нет (сейчас зависимости ставятся вручную), создать его из локали:

```bash
# локально
env -u PYTHONPATH uv pip freeze --python .venv/bin/python | grep -v "^-e" > requirements.txt
```

### Секреты

```bash
cd /opt/svoi
cp .env.example .env
chmod 600 .env
nano .env
```

Обязательный минимум:

```env
ENV=prod
TELEGRAM_TOKEN=<токен>
ADMIN_CHAT_ID=<chat_id>
TOOL_API_TOKEN=<сгенерировать: openssl rand -hex 24>
DATABASE_URL=postgresql://svoi:<пароль>@127.0.0.1:5432/svoi
N8N_BASE_URL=http://127.0.0.1:5678
TOOL_API_URL=http://127.0.0.1:8000
TZ_OFFSET_HOURS=4
BUDGET_SPREADSHEET_ID=...
SCHEDULE_SPREADSHEET_ID=...
CALENDAR_ID_READ=...
CALENDAR_ID_DRAFT=...
SCHEDULE_XLSX_ID=...
CONTACTS_SPREADSHEET_ID=...
```

```bash
mkdir -p secrets && chmod 700 secrets
scp <локальный путь>/google-sa.json svoi@<IP>:/opt/svoi/secrets/
chmod 600 /opt/svoi/secrets/google-sa.json
```

**Никогда** не переносить локальный `.env` и локальный `TOOL_API_TOKEN` в прод.

---

## 3. База данных

```bash
docker run -d --name svoi-postgres \
  -e POSTGRES_DB=svoi -e POSTGRES_USER=svoi -e POSTGRES_PASSWORD=<пароль> \
  -p 127.0.0.1:5432:5432 \
  -v svoi_pgdata:/var/lib/postgresql/data \
  --restart unless-stopped postgres:16

# ждём готовности
until docker exec svoi-postgres pg_isready -U svoi; do sleep 1; done
```

### Перенос данных с локали

```bash
# локально
pg_dump -h 127.0.0.1 -p 5433 -U svoi -Fc svoi > svoi.dump
scp svoi.dump svoi@<IP>:/tmp/

# на сервере
docker cp /tmp/svoi.dump svoi-postgres:/tmp/
docker exec -i svoi-postgres pg_restore -U svoi -d svoi --clean --if-exists /tmp/svoi.dump
```

Если база создаётся с нуля — применить миграции цепочкой Alembic (она же ведёт
`alembic_version`; прогон `db/init/*.sql` напрямую оставляет учёт пустым и выкат
потом падает на сверке версии):

```bash
cd /opt/svoi
sudo deploy/server-deploy.sh db        # alembic upgrade head поверх db/init/*.sql
.venv/bin/python deploy/check_schema.py --expect-head --env-file .env
```

Нужен только файл без полного прогона — `repair-schema` (раздел 10.2), он ставит
версию сам.

Проверка:

```bash
docker exec -i svoi-postgres psql -U svoi -d svoi -c \
  "select (select count(*) from budget_tx) as операций,
          (select count(*) from availability_hour) as часов,
          (select count(*) from participant where status <> 'archived') as людей;"
```

Ожидание после переноса: 91 / 7 217 / 10.

---

## 3a. Хранилище файлов (Garage)

Файлы бота и Mini App живут в S3-совместимом хранилище на этом же сервере (`docs/development/86`).
Без него `POST /webapp/files/presign` отвечает 503, и загрузка файла из Mini App не работает —
именно так прошёл выкат до 09.11.2026 (Д-03, `docs/development/70` O-013): Garage был
развёрнут только локально, `STORAGE_*` в прод-`.env` не попадали.

Шаг хранилища — одна команда, идемпотентная (повторный запуск не трогает ключи и объекты):

```bash
cd /opt/svoi
sudo deploy/server-deploy.sh storage
```

Что она делает:

1. поднимает контейнер `garage` из `docker-compose.yml` (имя — `GARAGE_CONTAINER` из `.env`,
   на сервере `svoi-garage`, локально `svoi-garage-dev`);
2. догенерирует в `/opt/svoi/.env` отсутствующие `GARAGE_RPC_SECRET`, `STORAGE_ACCESS_KEY`,
   `STORAGE_SECRET_KEY`, `STORAGE_ENDPOINT=http://127.0.0.1:3900`, регион и имена бакетов;
3. дописывает адреса, которые знает только выкат: `STORAGE_PUBLIC_ENDPOINT=https://<SVOI_STORAGE_DOMAIN>`
   и `STORAGE_CORS_ORIGINS=https://<SVOI_DOMAIN>` (правило CORS — отдельное на каждый origin);
4. назначает узлу роль (layout), создаёт бакеты `svoi-private` / `svoi-shared` и выдаёт
   ключу права read+write+owner;
5. прогоняет `scripts/storage_smoke.py` (put/get, presigned PUT и GET, list, delete);
6. перезапускает `svoi-tool-api`, чтобы процесс увидел новые `STORAGE_*`.

Публичный адрес. `files.<домен>` обслуживает Caddy (`deploy/server-deploy.sh proxy`,
шаблон `deploy/templates/Caddyfile.template`), TLS выпускается сам. Нужна **A-запись**
`files.<домен>` на IP сервера — без неё сертификат не выпустится, и `proxy` об этом
предупредит. Другой адрес задаётся в `deploy/svoi.env`: `SVOI_STORAGE_DOMAIN="..."`.

Проверка фактом (после шага, в любой момент):

```bash
cd /opt/svoi
.venv/bin/python scripts/storage_smoke.py      # put/get, presigned, list, delete — «ок»
.venv/bin/python scripts/storage_up.py --status   # узел, бакеты, endpoint
docker ps --filter name=svoi-garage            # контейнер Up (healthy)
curl -sI https://files.<домен>/svoi-private/   # 403 и заголовок сертификата: хост живой
curl -s -o /dev/null -w '%{http_code}\n' -H 'Origin: https://<домен>' \
  -X OPTIONS https://files.<домен>/svoi-private/_cors \
  -H 'Access-Control-Request-Method: PUT'      # 200 и Access-Control-Allow-Origin: https://<домен>
journalctl -u svoi-tool-api -n 50 --no-pager | grep presign   # после загрузки — 200, не 503
```

`deploy/server-deploy.sh smoke` тоже проверяет хранилище и валит выкат, если оно не отвечает:
зелёный выкат без работающей загрузки файлов больше не считается успехом.

Бакет закрыт: анонимных ссылок нет, только presigned с TTL 15 минут (`files_store.PRESIGN_TTL`).
Ключи хранятся в `/opt/svoi/.env` (600, владелец `svoi`) и в git не попадают.

---

## 4. n8n

```bash
docker run -d --name n8n-prod \
  -p 127.0.0.1:5678:5678 \
  -e N8N_HOST=svoi.example.ru \
  -e N8N_PROTOCOL=https \
  -e WEBHOOK_URL=https://svoi.example.ru/ \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=svoi -e N8N_BASIC_AUTH_PASSWORD=<пароль> \
  -e DB_TYPE=postgresdb \
  -e DB_POSTGRESDB_HOST=<внешний IP или host.docker.internal> \
  -e DB_POSTGRESDB_DATABASE=n8n -e DB_POSTGRESDB_USER=svoi -e DB_POSTGRESDB_PASSWORD=<пароль> \
  -v n8n_data:/home/node/.n8n \
  --restart unless-stopped n8nio/n8n:2.44.6
```

Перед этим создать базу `n8n`:

```bash
docker exec -i svoi-postgres psql -U svoi -d postgres -c "create database n8n;"
```

Импорт воркфлоу (тот же приём, что локально, но без правки sqlite):

```bash
docker cp n8n/workflows/wf-01-router.json n8n-prod:/tmp/
docker exec -u node n8n-prod n8n import:workflow --input=/tmp/wf-01-router.json
docker restart n8n-prod
```

Дальше в UI `https://svoi.example.ru` (basic-auth):
1. открыть WF-01, проверить credential OpenRouter (создать заново, ключ OpenRouter);
2. включить воркфлоу (Published);
3. прогнать `scripts/check_router_scenarios.py` с `N8N_BASE_URL=https://svoi.example.ru`.

**Важно:** creden­tials не переносятся с локального n8n — их надо создать на сервере.
Это единственное место, где нужен ручной шаг.

---

## 5. Сервисы (systemd)

`/etc/systemd/system/svoi-tool-api.service`:

```ini
[Unit]
Description=SVOI tool-api
After=network.target docker.service

[Service]
Type=simple
User=svoi
WorkingDirectory=/opt/svoi
EnvironmentFile=/opt/svoi/.env
ExecStart=/opt/svoi/.venv/bin/python -m uvicorn app.main:app \
          --app-dir services/tool-api --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/svoi-gateway.service`:

```ini
[Unit]
Description=SVOI telegram gateway
After=network.target svoi-tool-api.service

[Service]
Type=simple
User=svoi
WorkingDirectory=/opt/svoi
EnvironmentFile=/opt/svoi/.env
ExecStart=/opt/svoi/.venv/bin/python services/tg-gateway/app/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now svoi-tool-api svoi-gateway
sudo systemctl status svoi-tool-api svoi-gateway --no-pager
```

---

## 5a. Планировщик уведомлений и присмотра (systemd timers)

Раньше напоминания и проверки жили в `crontab` и запускались «если вспомнят».
Теперь они в контуре сервисов: unit-файлы лежат в репозитории в `deploy/systemd/`
и подключаются к systemd ссылками, поэтому обновляются вместе с кодом. C-11 закрыт так.

| Юнит | Что делает | Расписание |
|---|---|---|
| `svoi-notify-plan.service`/`.timer` | `scripts/plan_reminders.py --apply` — ставит в очередь взносы и незаполненную занятость | каждый день в 09:00 |
| `svoi-notify-send.service`/`.timer` | `scripts/send_notifications.py --apply --limit 50` — отправляет очередь, тихие часы соблюдает сам скрипт | каждые 5 минут |
| `svoi-feedback-digest.service`/`.timer` | `scripts/feedback_digest.py --apply` — дайджест открытых обращений админу; раздел «требует вас» — без ответа больше 6 часов | каждый день в 11:00 |
| `svoi-feedback-triage.service`/`.timer` | `scripts/feedback_triage.py --apply` — серверный разбор: вопрос и благодарность получают ответ, баг и пожелание помечаются «передано в разработку» (карту заводит dev-среда) | каждые 10 минут |
| `svoi-invariants.service`/`.timer` | `scripts/check_invariants.py` — инварианты `docs/architecture/11` | каждый день в 04:30 |
| `svoi-watchdog.service`/`.timer` | `scripts/watchdog.sh` — сервисы, БД, диск, свежесть бэкапа | каждые 10 минут |
| `svoi-health.service`/`.timer` | `scripts/health_agent.py --apply` — сверяет `git_sha` из `/health` с HEAD и пишет админу в `notification` | каждые 5 минут |
| `svoi-geo-check.service`/`.timer` | `scripts/check_availability.py --apply` — замер доступности `/api/health` с узлов РФ (Globalping: МТС, Билайн, Ростелеком) и алерт админу, если адрес перестал открываться снаружи (docs/infrastructure/75 §16) | каждый час в :20 |
| `svoi-backup.service`/`.timer` | `scripts/backup.sh` — дамп базы и ротация 7 последних (docs/infrastructure/110) | каждый день в 03:30 |
| `svoi-conflicts.service`/`.timer` | `scripts/conflict_watch.py --source journal --apply` — ловит `TelegramConflictError`/`409 Conflict` в журнале гейтвея и шлёт админу один алерт на эпизод (карта t_4aa28fe9) | каждые 5 минут |
| `svoi-cold-rotate.service`/`.timer` | `scripts/cold_rotate.py` — переносит файлы, которые не открывали 180 дней, на холодный том (Ш-11, docs/development/86) | каждый день в 05:10 |

Почему таймеры, а не cron:

* журнал попыток и кодов возврата — в `journalctl`, а не в почту root;
* `Persistent=true` — пропущенный из-за выключения запуск выполнится после старта;
* `RandomizedDelaySec` у редких задач разводит нагрузку на Google и Telegram;
* зависимости (`After=svoi-tool-api.service`) описываются явно;
* файлы версионируются рядом с кодом, их видно в ревью, а не только на сервере.

Логи долгоживущих сервисов пишет journald (`StandardOutput=journal`), ротация — его
забота: в `/etc/systemd/journald.conf` ставим `SystemMaxUse=1G` и
`MaxRetentionSec=14day`, отдельные logrotate-конфиги не нужны. Смотреть:
`journalctl -u svoi-tool-api -u svoi-gateway -p err --since -1h`.

**Версия живой сборки (FR-H12/H13, урок `L-005`).** `/health` отдаёт `git_sha`
(сборка, с которой стартовал процесс), `started_at` и `uptime` в секундах. Сверка
с HEAD живёт в `scripts/health_agent.py`:

```bash
cd /opt/svoi
.venv/bin/python scripts/health_agent.py            # только посмотреть
.venv/bin/python scripts/health_agent.py --apply    # ещё и уведомление админу в очередь
```

Расхождение sha — значит на сервере старый выкат: дефекты смоука нельзя записывать,
пока не перезапустили сервис. Агент не пишет в Telegram напрямую, а кладёт запись в
`notification` с дедупликацией «проблема + час»; отправляет её обычный
`svoi-notify-send.timer`, поэтому тихие часы и лимит в час соблюдаются.

Подключение (первый выкат и обновления):

```bash
cd /opt/svoi
sudo systemctl link /opt/svoi/deploy/systemd/svoi-tool-api.service \
  /opt/svoi/deploy/systemd/svoi-gateway.service \
  /opt/svoi/deploy/systemd/svoi-notify-plan.service \
  /opt/svoi/deploy/systemd/svoi-notify-plan.timer \
  /opt/svoi/deploy/systemd/svoi-notify-send.service \
  /opt/svoi/deploy/systemd/svoi-notify-send.timer \
  /opt/svoi/deploy/systemd/svoi-feedback-digest.service \
  /opt/svoi/deploy/systemd/svoi-feedback-digest.timer \
  /opt/svoi/deploy/systemd/svoi-feedback-triage.service \
  /opt/svoi/deploy/systemd/svoi-feedback-triage.timer \
  /opt/svoi/deploy/systemd/svoi-invariants.service \
  /opt/svoi/deploy/systemd/svoi-invariants.timer \
  /opt/svoi/deploy/systemd/svoi-watchdog.service \
  /opt/svoi/deploy/systemd/svoi-watchdog.timer \
  /opt/svoi/deploy/systemd/svoi-health.service \
  /opt/svoi/deploy/systemd/svoi-health.timer \
  /opt/svoi/deploy/systemd/svoi-geo-check.service \
  /opt/svoi/deploy/systemd/svoi-geo-check.timer \
  /opt/svoi/deploy/systemd/svoi-backup.service \
  /opt/svoi/deploy/systemd/svoi-backup.timer \
  /opt/svoi/deploy/systemd/svoi-conflicts.service \
  /opt/svoi/deploy/systemd/svoi-conflicts.timer \
  /opt/svoi/deploy/systemd/svoi-cold-rotate.service \
  /opt/svoi/deploy/systemd/svoi-cold-rotate.timer
sudo systemctl daemon-reload
sudo systemctl enable --now svoi-notify-plan.timer svoi-notify-send.timer \
                            svoi-feedback-digest.timer svoi-feedback-triage.timer \
                            svoi-invariants.timer svoi-watchdog.timer \
                            svoi-health.timer svoi-conflicts.timer \
                            svoi-geo-check.timer \
                            svoi-backup.timer svoi-cold-rotate.timer
systemctl list-timers 'svoi-*' --no-pager
```

Ссылки удобнее копирования: правка unit-файла в репозитории сразу видна systemd
после `daemon-reload`, и «на сервере другая версия юнита» становится невозможной.

Проверка перед выкатом — в репозитории, работает и на macOS без systemd:

```bash
.venv/bin/python scripts/check_services.py          # текст
.venv/bin/python scripts/check_services.py --json   # для CI
```

Скрипт проверяет: у сервиса есть `ExecStart`, `WorkingDirectory`, `EnvironmentFile`,
скрипт из `ExecStart` существует в репозитории; у таймера есть `OnCalendar` и парный
`.service`; на месте обязательный набор юнитов. Код выхода 1 — есть проблемы.

Ручной запуск задачи и разбор:

```bash
sudo systemctl start svoi-notify-send.service
sudo systemctl status svoi-notify-send.service --no-pager
journalctl -u svoi-notify-send -n 50 --no-pager
journalctl -u svoi-invariants -p err --since "1 day ago"
```

Важно: `tg-gateway` и `tool-api` остаются долгоживущими сервисами с `Restart=always`
(раздел 5); таймеры — только `Type=oneshot` и не держат состояние в памяти.
Секреты берутся из `EnvironmentFile=/opt/svoi/.env`, в unit-файлах их нет.

---

## 5b. Почему polling и что будет при двух инстансах

Решение 28.10.2026: релиз выходит на long polling. Переменной `MODE` больше нет —
gateway всегда запускается через `dp.start_polling`, webhook-ветки в коде не существует.
Webhook — задача после релиза: нужен публичный HTTPS, `secret_token` Telegram и защита
эндпоинта от чужих запросов.

Почему polling: публичный HTTPS для запуска не нужен, на сервере нет входящего порта
для Telegram, отладка проще — весь поток апдейтов виден в `journalctl`. Для одного бота
с одним токеном polling полностью достаточен.

Главное ограничение polling: **один токен — один поллер**. Если запущены два экземпляра
gateway (локальный и серверный, или два процесса на сервере), Telegram отдаёт каждый
апдейт только одному из них. Второй процесс получает `409 Conflict`, и бот «мигает»:
часть кнопок не отвечает, FSM-формы обрываются, уведомления приходят с задержкой.
В логе это выглядит так:

```
telegram.error.Conflict: Conflict: terminated by other getUpdates request;
make sure that only one bot instance is running
```

Перед запуском на сервере остановить локальный поллер. Проверка на сервере:

```bash
pgrep -af "tg-gateway"          # ровно один процесс
systemctl status svoi-gateway --no-pager
journalctl -u svoi-gateway -n 50 --no-pager | grep -i conflict   # пусто
```

Если `409` появился после обновления — сначала проверить, не остался ли старый процесс:
перезапуск юнита через systemd его не оставит, ручной запуск в фоне — оставит.

### Замок одного экземпляра (Ш-28)

Проверять `pgrep` руками — не защита: между проверкой и запуском окно, а `nohup` из
старой инструкции второго процесса не замечает. Поэтому сам gateway при старте берёт
замок `flock` на файле `~/.local/state/svoi/tg-gateway.lock` (путь переопределяется
`SVOI_GATEWAY_LOCK`) и пишет туда свой PID. Второй экземпляр выходит сразу, до
подключения к Telegram, с сообщением:

```
tg-gateway уже запущен (PID 4821): замок /Users/.../.local/state/svoi/tg-gateway.lock занят.
Второй поллер получит 409 Conflict и потеряет сообщения — оставьте один экземпляр.
```

Замок — advisory, ядро снимает его само при завершении процесса, поэтому «залипших»
файлов не бывает: оставшийся от аварии файл с мёртвым PID следующий запуск просто
перезапишет. Код — `services/tg-gateway/app/single_instance.py`, вызов — первая строка
`main()`; проверка `TELEGRAM_TOKEN` идёт уже после замка, чтобы второй экземпляр
не выглядел как «не задан токен».

`scripts/check_services.py --live` проверяет не только число процессов, но и замок:

| ситуация | вывод проверки |
|---|---|
| процессов 0 | `tg-gateway не запущен` |
| процессов 2+ | `экземпляров 2 (…): остальные получают 409` |
| процесс один, замка нет | `запущен без замка …: перезапустите` (процесс старше Ш-28) |
| в замке чужой PID | `замок держит PID X, а живой поллер — PID Y` |

Проверка на живом окружении: `launchctl kickstart -k gui/$UID/com.svoi.gateway`, затем
`cat ~/.local/state/svoi/tg-gateway.lock` — там PID живого процесса, а
`pgrep -f "tg-gateway/app/main.py"` даёт ровно один PID. Попытка запустить второй
экземпляр вручную (`env -u PYTHONPATH .venv/bin/python services/tg-gateway/app/main.py`)
завершается тем же сообщением, что выше, и не мешает работающему боту.


---

## 5c. Сервисы на маке: launchd (dev-контур)

На сервере сервисы держит systemd (`Restart=always`, раздел 5). На маке systemd нет,
и до Ш-27 `tool-api` с `tg-gateway` поднимались `nohup`-ом изнутри cron-тика. Такой
процесс живёт ровно столько, сколько живёт сессия агента: к следующему тику `/health`
уже не отвечал, бот не читал апдейты, напоминания не уходили — каждый шаг начинался
с мёртвого окружения. Причина не в коде сервиса, а в родителе: у процесса, поднятого
из тика, родитель — оболочка тика.

Решение: **launchd-агенты с `RunAtLoad` + `KeepAlive`**. launchd (PID 1) сам держит
процесс, поднимает его после падения (не чаще `ThrottleInterval`, 10 с) и ничего не
знает про сессии Hermes — конец агентского тика процесс больше не убивает.

Что лежит в репозитории:

| файл | назначение |
|---|---|
| `deploy/launchd/com.svoi.tool-api.plist.template` | агент API (порт 8000) |
| `deploy/launchd/com.svoi.gateway.plist.template` | агент поллера Telegram |
| `deploy/launchd/com.svoi.conflicts.plist.template` | сторож 409: `conflict_watch.py --apply` раз в 5 минут (`StartInterval` 300), не демон |
| `scripts/run_service.py` | запуск сервиса с окружением из `.env` |
| `scripts/install_launchd.py` | рендер plist-ов в `~/Library/LaunchAgents` и загрузка |
| `scripts/start_services.py` | перезапуск обоих сервисов и ожидание готовности |

Шаблоны содержат подстановки `@PROJECT@`, `@PYTHON@`, `@LOG_DIR@` — путь проекта на
маке содержит пробел (`repos/Projects/teatrsvoi_bot Project`), поэтому в plist он
попадает одной строкой `ProgramArguments`, без шелла. Шелл тут принципиально не
участвует: рецепт `sh -c '...'` из тика блокируется политикой одобрений (Ш-29).

```bash
# поставить и загрузить оба агента (снимет процессы, поднятые руками)
env -u PYTHONPATH .venv/bin/python scripts/install_launchd.py --takeover

# что получилось
env -u PYTHONPATH .venv/bin/python scripts/install_launchd.py --status
launchctl print gui/$UID/com.svoi.tool-api | head -20

# проверка живого окружения (не только файлов юнитов)
env -u PYTHONPATH .venv/bin/python scripts/check_services.py --live
```

`--dry-run` печатает, что будет записано; `--uninstall` снимает агентов и удаляет
plist-ы. Загрузка (`bootstrap`) идёт с паузой после снятия и тремя попытками: launchctl
асинхронен, и сразу после `bootout` отвечает `Bootstrap failed: 5: Input/output error`,
оставляя сервис лежать. После каждой попытки скрипт проверяет, что процесс действительно
появился, — «bootstrap ok» при мёртвом сервисе не считается успехом. Без `--takeover` установка не тронет сервис, уже запущенный мимо launchd:
второй поллер — это `409 Conflict` и потерянные сообщения (раздел 5b, Ш-28).

Перезапуск после правок кода — один скрипт, без шелла и без heredoc (Ш-29):

```bash
env -u PYTHONPATH .venv/bin/python scripts/start_services.py
```

Он перезаписывает plist-ы, перезагружает агентов с `--takeover` (снимая процессы,
поднятые мимо launchd) и ждёт готовности фактами: `/health` → `ok: true`, поллер
ровно один и держит замок. Прямой вызов `launchctl` с ключом перезапуска из тика не
работает: политика одобрений блокирует его как перезапуск launchd-сервиса, а при
незагруженном агенте он вообще ничего не поднимает. `--direct` поднимает процессы в
отдельной сессии (`start_new_session`) — путь для контуров без launchd;
`--service gateway`, `--timeout 60`, `--json` — по потребности. Код выхода 1 и
список проблем, если сервисы не поднялись: «процесс запущен» успехом не считается.

Логи (launchd не отдаёт их в терминал): `~/Library/Logs/svoi/tool-api.log`,
`tool-api.err.log`, `gateway.log`, `gateway.err.log`. gateway пишет лог в stdout (Ш-39), поэтому
`gateway.log` не пуст при живом поллере. Ротация — на старте сервиса, см. ниже.

Окружение: `.env` читает `scripts/run_service.py` через `python-dotenv`, поэтому
инлайн-комментарии (`TZ_OFFSET_HOURS=4    # Самара, UTC+4`) отрезаются как в shell —
иначе `int()` в API падает на старте. `PYTHONPATH` из окружения агента удаляется:
с ним глобальные пакеты Hermes подменяют пакеты проекта.

Проверка, что сервисы переживают конец сессии: `curl -s http://127.0.0.1:8000/health`
до и после нового тика, `ps -o pid,ppid,command -p <pid>` — родитель процесса
`launchd` (PID 1), а не оболочка тика. Агенты ставятся один раз; после `git pull` и
правок в самих plist-шаблонах — повторить `install_launchd.py --takeover`.

`scripts/check_services.py` без флагов остаётся статическим (проверяет
`deploy/systemd`), с `--live` добавляет живое окружение: `/health` tool-api должен
вернуть `ok: true`, процесс `tg-gateway` — быть ровно один, и он же должен держать
замок одного экземпляра (§5b, Ш-28); файлы логов живых сервисов не пусты (Ш-39). Юниты в порядке, а сервисы лежат — это тоже
провал проверки.

### Расхождение агентов с шаблоном и путь проекта (Ш-38)

Плист launchd — снимок окружения на момент установки: путь проекта и интерпретатор
`.venv/bin/python` записаны в него буквально. Проект уже переезжал (`~/Documents` →
`~/repos`), а venv пересоздавался; агент после этого указывает в пустоту, `KeepAlive`
молча рестартит падение, и сервис выглядит живым только в `launchctl list`. Поэтому
есть сверка установленного с текущим рендером:

```bash
env -u PYTHONPATH .venv/bin/python scripts/install_launchd.py --doctor
```

Сравниваются `ProgramArguments`, `WorkingDirectory`, `EnvironmentVariables`,
`StandardOutPath`, `StandardErrorPath` обоих агентов; расхождение, отсутствующий или
битый plist — строка с конкретным путём из прошлого места и подсказкой
`install_launchd.py --takeover`, код выхода 1. Та же сверка включена в
`check_services.py --live` (только на macOS: на сервере сервисы держит systemd).
После переезда проекта, пересоздания venv и правок в `deploy/launchd/*.template`
агентов нужно переустановить — иначе проверка покажет расхождение.

### Ротация логов (Ш-38)

launchd пишет stdout/stderr в файлы без предела. Решение: ротация на старте сервиса —
`scripts/run_service.py` перед `exec` сдвигает файлы `{service}*.log` больше 5 МБ в
`*.log.1` (прежний `.1` заменяется). Пока процесс жив, файл не переименовывается;
ошибка файловой системы запуск не блокирует. Итог: на каждый лог не больше ~10 МБ,
хвост прошлого запуска сохраняется для разбора. Переменная `SVOI_LOG_DIR` меняет папку.


### Логи: gateway пишет в `gateway.log` (Ш-39)

`logging.basicConfig` без `stream` пишет в stderr, а launchd ловил её в `gateway.err.log`;
`gateway.log` оставался нулевым, и человек решал, что поллер мёртв. Теперь gateway
логирует в stdout (`stream=sys.stdout`), то есть в `gateway.log`; `gateway.err.log`
остаётся для необработанных падений. `check_services.py --live` на macOS проверяет,
что у живого сервиса хотя бы один файл лога не пуст.


---

## 6. Caddy и TLS

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
svoi.example.ru {
    encode gzip

    handle /webhook/* {
        reverse_proxy 127.0.0.1:5678
    }
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        respond "SVOI" 200
    }

    log {
        output file /var/log/caddy/svoi.log
        format json
    }
}
```

```bash
sudo systemctl reload caddy
curl -I https://svoi.example.ru          # 200, сертификат выдан
```

---

## 7. Проверка после развёртывания

```bash
# 1. сервисы живы
curl -s https://svoi.example.ru/api/health
# ожидание: {"ok":true,...,"env":"prod"}

# 2. база наполнена
docker exec -i svoi-postgres psql -U svoi -d svoi -c \
  "select count(*) from budget_tx;"

# 3. инварианты
cd /opt/svoi && .venv/bin/python scripts/check_invariants.py
# ожидание: 7 строк OK

# 4. n8n отвечает
curl -s -X POST https://svoi.example.ru/webhook/router \
  -H 'Content-Type: application/json' \
  -d '{"text":"расход 3000 на реквизит","user":{"name":"Тест","roles":["администратор"]},"categories":["Реквизит и костюмы"]}'
# ожидание: {"ok":true,"intent":"budget","category":"Реквизит и костюмы"}

# 5. роутер целиком
N8N_BASE_URL=https://svoi.example.ru .venv/bin/python scripts/check_router_scenarios.py
# ожидание: «все 20 фраз разобраны верно»

# 6. бот в Telegram
#    написать боту «кто я» → должен ответить именем и ролями
#    нажать «Админ-панель» → должно открыться меню панели
```

Пункт 6 — единственная проверка, которую нельзя автоматизировать без отдельного
тестового аккаунта.

---

## 8. Бэкапы

`/opt/svoi/scripts/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
DIR=/opt/svoi/backups
mkdir -p "$DIR"
STAMP=$(date +%Y-%m-%d_%H%M)
docker exec svoi-postgres pg_dump -U svoi -Fc svoi > "$DIR/svoi_$STAMP.dump"
find "$DIR" -name "svoi_*.dump" -mtime +14 -delete
# проверка: дамп не пустой
test -s "$DIR/svoi_$STAMP.dump" || { echo "бэкап пустой"; exit 1; }
echo "бэкап готов: svoi_$STAMP.dump ($(du -h "$DIR/svoi_$STAMP.dump" | cut -f1))"
```

```bash
chmod +x /opt/svoi/scripts/backup.sh
sudo crontab -e -u svoi
# 0 3 * * * /opt/svoi/scripts/backup.sh >> /var/log/svoi-backup.log 2>&1
```

**Проверка восстановления** (раз в квартал, обязательно):

```bash
docker exec -i svoi-postgres psql -U svoi -d postgres -c "create database svoi_check;"
docker exec -i svoi-postgres pg_restore -U svoi -d svoi_check /opt/svoi/backups/<файл>.dump
docker exec -i svoi-postgres psql -U svoi -d svoi_check -c "select count(*) from budget_tx;"
docker exec -i svoi-postgres psql -U svoi -d postgres -c "drop database svoi_check;"
```

Бэкап, который ни разу не восстанавливали, бэкапом не считается.

---

## 9. Мониторинг

**Минимум (30 минут на настройку):**

```bash
sudo apt install -y prometheus-node-exporter   # или Uptime Kuma в docker
```

Вариант проще — сторож в контуре сервисов: `scripts/watchdog.sh` под
`svoi-watchdog.timer` (раздел 5a). Он молчит, когда всё хорошо, и при проблеме
возвращает 1 (это видно в `journalctl -u svoi-watchdog`). Проверяет:
доступность tool-api и n8n, `pg_isready`, занятость диска и свежесть бэкапа; отдельно
сверяет `git_sha` из `/health` с HEAD — старая сборка тоже считается проблемой.

Алерты сторожа и health-агента идут **одним путём**: `scripts/watchdog_alert.py` и
`scripts/alerts.py` ставят запись в `notification` (`kind='health'`) активным админам,
оттуда её забирает `svoi-notify-send.timer` с тихими часами и лимитом «3 в час».
Ключ дедупликации — «проблема + час» (отпечаток набора проблем), поэтому лежащий
сервис не присылает сообщение каждые 10 минут, а один и тот же сбой не приходит
дважды — от сторожа и от агента. Прямая отправка в Telegram осталась только аварийным
резервом внутри `watchdog_alert.py` на случай, если база недоступна; для неё нужны
`TELEGRAM_TOKEN` и `ADMIN_CHAT_ID` в `.env`.

Полную сверку версии делает `scripts/health_agent.py` (`svoi-health.timer`, каждые
5 минут) — раздел 5a; очередь у него та же.

Копировать скрипты в `/opt/svoi` не нужно: они лежат в репозитории, а юнит ссылается
на `/opt/svoi/scripts/watchdog.sh`. Минимальная настройка — `.env` с `DATABASE_URL`,
включённый таймер и `chmod +x scripts/watchdog.sh`; `TELEGRAM_TOKEN` и `ADMIN_CHAT_ID`
нужны только аварийному резерву, когда база недоступна.

Что смотреть регулярно:

| Что | Как | Порог |
|---|---|---|
| доступность | watchdog каждые 10 мин | 2 провала подряд |
| версия сборки | health-агент каждые 5 мин | `git_sha` != HEAD или `unknown` |
| ошибки сервисов | `journalctl -u svoi-gateway -p err` | больше 5 за час |
| инварианты данных | `svoi-invariants.timer` раз в сутки (раздел 5a) | любой FAIL |
| диск | watchdog | больше 85 % |
| бэкап | watchdog | нет файла моложе 24 ч |

---

## 10. Обновление версии

Рабочий путь — `deploy/release.sh` с рабочей машины, выкат идёт через GitHub Actions
(§12). Ручной SSH-путь ниже остаётся резервом.

### 10.0. Рабочий путь: release.sh

```bash
deploy/release.sh 0.3.3             # prepare: гейты, CHANGELOG, локальный тег. Прод не трогает.
deploy/release.sh v0.3.3 --apply    # по команде владельца: push тега, workflow, сверка версии
deploy/release.sh --status          # что в проде: тот же workflow с dry_run=true
```

`prepare` требует ветку `main`, чистое дерево и совпадение с `origin/main`, прогоняет
`ruff check .`, `scripts/test_audit.py --strict`, `pytest -q`, проверяет раздел версии
в `CHANGELOG.md` и печатает план (тег, `sha`, коммиты от предыдущего тега). Ничего не
публикует: сеть трогается только на `git fetch --tags`.

`--apply` пушит тег, запускает `.github/workflows/deploy.yml` (`tag=<тег>`, `dry_run=false`),
ждёт run и записывает фактическое состояние в `docs/reports/78-release-state.md`. При провале
run сообщает админу, проверяет откат в логе и при его отсутствии выкатывает предыдущий тег.

**Откат:** `deploy/release.sh <предыдущий тег> --apply` либо `deploy/server-deploy.sh rollback
<предыдущий тег>` на сервере. База при откате не откатывается, поэтому откат выполняется,
только если `alembic_version` равна head тега; иначе скрипт отказывается переключать код —
порядок «сначала БД, потом код» в §10.3.

### 10.1. Резерв: руками на сервере

Резерв, а не основной путь: основной — GitHub Actions, он повторяем и оставляет лог.

Доступ с рабочей машины **есть**: `ssh svoi-prod` (алиас в `~/.ssh/config`, ключ
`~/.ssh/deploy_prod`) — проверено 09.11.2026 03:55: `hostname`, `systemctl is-active
svoi-tool-api` и `git describe --tags` ответили. Голый `ssh root@129.101.126.208` с ключом
`~/.ssh/svoi_prod` получает `Permission denied (publickey)` — это не авторизованный ключ,
а не блокировка сети. Запись «узел VPN режет адрес и порт 22» относилась к 01–02.11.2026
(`docs/development/70` O-011) и на 09.11.2026 неверна: `curl https://svoi-theater-samara.online`
с ноутбука отвечает 200.

```bash
cd /opt/svoi
git fetch --tags && git checkout v0.3.0
.venv/bin/pip install -r requirements.txt
sudo deploy/server-deploy.sh db        # postgres, схема: alembic upgrade head (учёт версии ведёт он)
sudo systemctl restart svoi-tool-api svoi-gateway
sleep 5 && curl -s http://127.0.0.1:8000/health
.venv/bin/python scripts/health_agent.py     # git_sha должен совпасть с HEAD
bash scripts/deploy-check.sh                 # весь контур: /health, версия, gateway, юниты (§10a)
sudo deploy/server-deploy.sh storage         # хранилище файлов: контейнер, ключи, бакеты, CORS (§3a)
```

Почему не `psql < db/init/*.sql`: такой прогон меняет структуру, но не трогает
`alembic_version`, и следующий выкат останавливается на сверке версии (так упал v0.8.0
08.11.2026, разбор — §10.2).

Правило миграций: код новой версии не удаляет колонки сразу — сначала добавляет,
потом (через релиз) чистит. Это даёт откат без восстановления базы.

**Откат руками:** сначала база, потом код (§10.3): ручной `git checkout` не сверяет схему,
и старый тег поверх накатанной базы не стартует.

```bash
# 1) база — восстановление дампа (docs/infrastructure/110 §4) или alembic downgrade
# 2) код
git checkout v0.2.0
sudo systemctl restart svoi-tool-api svoi-gateway
```

---

### 10a. Автоматическая проверка выката

`scripts/deploy-check.sh` — одна команда после обновления: сверяет `/health` и `git_sha`
с HEAD через `scripts/health_agent.py`, проверяет процесс `tg-gateway` и юниты
планировщика (`scripts/check_services.py`). Код выхода 1 при любой проблеме, поэтому
скрипт годится и для ручного выката, и как последний шаг своего скрипта деплоя.

```bash
cd /opt/svoi
bash scripts/deploy-check.sh                 # /opt/svoi и http://127.0.0.1:8000
bash scripts/deploy-check.sh --root /opt/svoi --url http://127.0.0.1:8000
```

При выкате через `release.sh --apply` ту же проверку делает сам workflow: шаг «проверка
фактом» сверяет `git_sha` из `/api/health` с HEAD на диске и падает при расхождении.
`release.sh --status` запускает `deploy.yml` с `dry_run=true` и печатает из лога текущую
версию, число коммитов и миграций до цели — это единственный способ увидеть прод с рабочей
машины, когда сеть до сервера закрыта.

Тот же контур проверяется в CI: job `images` в `.github/workflows/tests.yml` собирает
оба образа, поднимает `tool-api` с настоящим Postgres (пустая база, схема накатывается
`alembic upgrade head`), ждёт
`/health` и делает smoke-запрос `/tools/categories` с сервисным токеном. Если образ
собирается, но не стартует или не видит базу — job красный.

## 10.2. Порядок «миграция → версия → выкат» (repair-schema)

Обычный выкат миграции не накатывает: `deploy.yml` вызывает `server-deploy.sh`, а тот
`alembic upgrade head` не запускает. Штатный путь для новой схемы — workflow
`.github/workflows/repair-schema.yml` (Actions → «ремонт схемы прода» → Run workflow,
`files` — файлы из `db/init`, `confirm=apply`).

Порядок один и тот же, что руками, что workflow:

1. **миграция** — файлы `db/init/*.sql` применяются к базе;
2. **версия** — `alembic_version` ставится на ревизию применённых файлов;
3. **выкат** — `deploy/release.sh <тег> --apply`; его сверка `deploy/check_schema.py
   --expect-head` требует, чтобы версия была равна head цепочки.

Шаг 2 обязателен: `psql` про Alembic не знает. 08.11.2026 файл 061 был накатан через
`repair-schema` (права роли `master` в базе появились), версия осталась на
`060_participant_role_revoked`, выкат v0.8.0 остановился на строке «версия схемы 060 не
равна head 061», откатился на v0.7.0 и потребовал ручного шага. Теперь workflow делает
шаг 2 сам:

* `deploy/check_schema.py --stamp-head --files "<список>"` — сначала сверяет структуру
  с `db/init` (таблицы и колонки). Если структура отстаёт, версия не двигается: иначе
  учёт обещал бы выкату то, чего в базе нет. Затем ставит версию на ревизию применённых
  файлов (`db/init/NNN_x.sql` → ревизия `NNN_x`), а при пустом списке — на head.
  Вперёд структуры версия не уходит и назад не откатывается.
* `deploy/check_schema.py --expect-head` — явная проверка «`alembic current` = head»;
  расхождение валит workflow с сообщением, что делать.

Локально/на сервере те же проверки (обе читают `DATABASE_URL`, либо `--env-file PATH`):

```bash
.venv/bin/python deploy/check_schema.py                       # чего не хватает в структуре
.venv/bin/python deploy/check_schema.py --expect-head          # версия = head (read-only)
.venv/bin/python deploy/check_schema.py --stamp-head \
    --files "db/init/061_master_role.sql"                      # поставить версию после psql-наката
```

### Если версия отстала (SQL накатан, `alembic_version` — нет)

```
версия схемы 060_participant_role_revoked не равна head 061_master_role
ОШИБКА: версия схемы не равна head: server-deploy.sh db --migrate
```

Лечение — одна команда на сервере, файлы идемпотентны, повторный накат ничего не меняет:

```bash
cd /opt/svoi
sudo deploy/server-deploy.sh db          # alembic upgrade head
.venv/bin/python deploy/check_schema.py --expect-head --env-file .env
```

После этого повторять `deploy/release.sh <тег> --apply` — сверка версии пройдёт без
ручных шагов. Откатывать выкат из-за одной версии не нужно: данные и структура в этот
момент уже правильные, расходится только учёт.

Два ограничения, о которых стоит помнить:

* `--stamp-head` сверяет таблицы и колонки (что видят миграции), но не права, индексы
  и строки справочников. Если файл меняет только данные (056–058, 061 в части прав), его
  применение проверяется по факту — поэтому в списке `files` называйте ровно то, что
  накатили: версия встанет по максимуму из названных файлов.
* Версия ставится из checkout раннера, а не из `/opt/svoi`: на сервере лежит прошлый тег,
  новых ревизий там ещё нет. Workflow копирует `alembic.ini`, `db/init`,
  `services/tool-api/migrations` и `deploy/check_schema.py` во временный каталог
  `/tmp/svoi-schema-repair` и запускает их серверным `.venv`.

## 10.3. Откат выката: сначала БД, потом код

`alembic_version` в базе — часть контракта: `services/tool-api/app/main.py` при старте требует
`version == head` кода, иначе служба падает с `RuntimeError: схема не применена` и уходит в
цикл перезапусков (сайт отвечает 502). Понижающих миграций в `db/init` нет, поэтому **откат
кода на старший тег поверх накатанной схемы невозможен**. Порядок один:

1. **БД** — привести схему к состоянию тега:
   * восстановление из дампа `/opt/svoi/backups/svoi_*.dump` — `docs/infrastructure/110-backups-and-restore.md` §4
     (остановить службы, `pg_restore --clean --if-exists --no-owner`, поднять службы);
   * либо `alembic downgrade <ревизия>` — только если у затронутых миграций есть понижающая
     часть; её отсутствие не «дописывают на ходу», это отдельная задача.
2. **Код** — `deploy/server-deploy.sh rollback <тег>`.

`server-deploy.sh rollback` проверяет это сам: тянет дерево тега (`git archive FETCH_HEAD`,
без checkout), сверяет `alembic_version` с его head и **отказывается** переключать код, если
версии не равны, — прод остаётся на текущей рабочей версии, а не в 502. Текст отказа называет
причину и оба пути лечения (дамп или `downgrade`). Ручной `git checkout` такой проверки не
делает: после него служба не поднимется, пока база не приведена к тегу.

Так закрыт дефект отката, найденный при выкате Д-03 09.11.2026: `rollback` переключил код на
старый тег поверх схемы, накатанной новой версией, и откат ничего не восстановил.

## 11. Что нельзя делать

1. Запускать gateway одновременно на локали и сервере — Telegram отдаст `409 Conflict`,
   бот начнёт «мигать» (разбор — §5b).
2. Копировать `.env` из локали — там dev-токен и локальные пути.
3. Держать Postgres открытым наружу (`-p 5432:5432` без `127.0.0.1`).
4. Править воркфлоу только в UI — расходится с git, и следующая правка промпта
   перезапишет ручные изменения.
5. Оставлять `N8N_BASIC_AUTH_ACTIVE=false` на публичном домене — редактор открыт всем.
6. Забывать `chmod 600` на `.env` и `secrets/google-sa.json`.

## 12. Выкат через GitHub Actions (публичный репозиторий)

Прод обновляется по команде владельца. Workflow `.github/workflows/deploy.yml` запускается
руками (`workflow_dispatch`), автоматического деплоя нет. Файл живёт в публичном
репозитории `tentakleebeast-beep/teatrsvoi_bot` (docs/development/26 §10); там же заданы
секрет `DEPLOY_SSH_KEY` и переменная `DEPLOY_HOST`. Прод `/opt/svoi` берёт код из этого же
публичного репозитория (`origin = https://github.com/tentakleebeast-beep/teatrsvoi_bot.git`).

SSH с рабочей машины до прода с 08.11.2026 проверен и работает: ручной путь через
`ssh svoi-prod` — резерв, а не единственный способ. Рабочий путь — GitHub Actions,
запущенный руками; с раннеров GitHub сервер достаётся напрямую.

Что нужно один раз:

1. Ключ деплоя **без парольной фразы** (GitHub Actions не может спрашивать пароль):
   ```bash
   cp ~/.ssh/svoi_prod ~/.ssh/deploy_prod
   ssh-keygen -p -f ~/.ssh/deploy_prod -N ""      # старая фраза, затем пустая
   gh secret set DEPLOY_SSH_KEY -R tentakleebeast-beep/teatrsvoi_bot < ~/.ssh/deploy_prod
   ```
2. GitHub → Settings → Secrets and variables → Actions → **New repository secret**:
   имя `DEPLOY_SSH_KEY`, значение — содержимое приватного ключа целиком.
3. Переменная `DEPLOY_HOST` — адрес сервера (если не задана, workflow не стартует:
   плейсхолдер в файле убран, чтобы адрес не лежал в публичном дереве).

Порядок выката: `deploy/release.sh <версия> --apply` — ставит тег, пушит его в публичный
репозиторий, запускает `deploy.yml` там же и ждёт результат. Либо Actions → «деплой на прод»
→ Run workflow → тег → `dry_run: true` (только посмотреть версию, разницу и число миграций)
→ затем `dry_run: false`. При падении проверки после выката workflow сам делает `rollback`
на предыдущий тег.

## 13. Свой раннер GitHub Actions на VPS — снят

Раннер `svoi-vps` поднимался 04.11.2026 как обход закрытой квоты минут Actions. После
публикации репозитория минуты для публичных репозиториев не тарифицируются, и 08.11.2026
раннер снят целиком: `/opt/actions-runner` удалён, служба удалена, регистрация в GitHub
убрана. CI идёт на `ubuntu-latest`; self-hosted на публичном репозитории с `pull_request`
не возвращается — это исполнение кода из форка на прод-машине.

## 14. Карта версий и опорные точки

Зачем: после публикации репозитория со сжатой историей старые SHA исчезают, и без этой таблицы
на вопрос «что сейчас работает на проде и в чём проблема» ответить нечем. Одна таблица, одна
строка на изменение.

| Дата | Тег | SHA | Где | Что вошло |
|---|---|---|---|---|
| 2026-11-04 | `v0.5.0` | `d0f307c` | прод | последняя версия до волны Ш-01…Ш-15; схема до миграции 054 |
| 2026-11-05 | `v0.6.1` | `5d61fe8` | прод и dev | Alembic — источник схемы: на проде `alembic_version` поставлен `stamp head` после сверки структуры (55 таблиц); общие настройки в `services/shared/settings.py`; docs/19 пересчитана (111 требований). Дамп до выката: `prod-before-061-0954.sql` |
| 2026-11-05 | `v0.6.0` | `d3d658f` | прод и dev | волна Ш-01…Ш-15, нижняя клавиатура «Обнулить/Озадачить/Сколько мне осталось»; миграции 055–060 накачены точечно (055, 060 — схема; 056–058 — данные и права), схема 55 таблиц. Дамп до выката: `prod-before-0932.sql` |

Правило: строка пишется при выкате, а при публикации репозитория — дополнительно строка
соответствия «старый SHA → публичный коммит», чтобы старая ссылка на версию не превращалась в
мусор.

Перед выкаткой и после неё — один и тот же снимок, он же проверка «та ли сборка работает»:

```bash
bash scripts/deploy-check.sh --url http://127.0.0.1:8000 --root /opt/svoi   # до выката
ssh svoi-prod 'docker exec svoi-postgres pg_dump -U svoi svoi' > svoi-$(date +%F).sql
# выкат по §10a
bash scripts/deploy-check.sh --url http://127.0.0.1:8000 --root /opt/svoi   # после
```

Откат кода не требует GitHub: объекты git лежат в `/opt/svoi`, поэтому
`git -C /opt/svoi checkout v0.5.0` возвращает прежнюю версию. Откат схемы — только восстановление
дампа: down-миграций в проекте нет. Порядок жёсткий — сначала БД, потом код (§10.3):
`deploy/server-deploy.sh rollback <тег>` сам отказывается переключать код, если
`alembic_version` не равна head тега.