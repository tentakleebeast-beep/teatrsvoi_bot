# 09. Google: service account и структура Drive с нуля

> **n8n выведен из контура (V-026, 01.11.2026).** Где ниже n8n назван частью контура или
> источником разбора свободного текста, читать: `POST /tools/route` в tool-api
> (`services/tool-api/app/router_llm.py`); воркфлоу, публичный вебхук и переменные `N8N_*`
> удалены из деплоя. История контрактов — `docs/archive/29-n8n-scenarios.md`.

Полная инструкция «сделай и заработает». Ничего не переиспользуем из n8n-credentials: у `tool-api` будет своя машинная личность.

---

## Часть 1. Service account (10 минут)

### Шаг 1. Проект в Google Cloud
1. https://console.cloud.google.com → сверху селектор проектов → **New Project** → имя `svoi-bot` → Create.
2. Убедись, что в селекторе выбран именно `svoi-bot`.

### Шаг 2. Включить три API
Menu → **APIs & Services → Library** → по очереди найти и нажать **Enable**:
- `Google Drive API`
- `Google Sheets API`
- `Google Calendar API`

Без Sheets API чтение `Свод` и запись транзакций упадут с `403 API has not been used`.

### Шаг 3. Создать service account
1. **APIs & Services → Credentials → Create credentials → Service account**.
2. Name: `svoi-tool-api` → Create and continue → роли не нужны (доступ даём через Drive, не через IAM) → Done.
3. Открой созданный аккаунт → вкладка **Keys** → **Add key → Create new key → JSON → Create**.
4. Скачается файл `svoi-bot-xxxx.json`. Это единственный экземпляр ключа — второй раз скачать нельзя, только пересоздать.
5. Открой файл, найди строку `"client_email"` — это адрес вида `svoi-tool-api@<project-id>.iam.gserviceaccount.com`. **Он понадобится на шаге 5.**

### Шаг 4. Положить ключ в проект
```bash
cd "teatrsvoi_bot Project"
mkdir -p secrets
mv ~/Downloads/svoi-bot-*.json secrets/google-sa.json
chmod 600 secrets/google-sa.json
```
`secrets/` уже в `.gitignore`. В репозиторий ключ не попадёт.

### Шаг 5. Расшарить ресурсы на email сервис-аккаунта

Ты сказал, что папки/файлы/календарь можно создать отдельные — так и делаем, с нуля.

**5.1. Корневая папка в Google Drive**
1. Drive → **New → Folder** → имя `SVOI`.
2. Правый клик на `SVOI` → **Share** → вставить `client_email` из шага 3 → роль **Editor** → Send.
   (Отправлять письмо не нужно; «Send» просто подтверждает. Галочку «Notify people» можно снять.)
3. Открой папку — в URL будет `https://drive.google.com/drive/folders/1AbCdEf...`. Скопируй `1AbCdEf...` — это `DRIVE_ROOT_ID`.

Права наследуются: всё, что бот создаст внутри `SVOI`, будет доступно тебе, а бот увидит всё, что создашь ты.

**5.2. Таблица бюджета**
1. Внутри `SVOI` создай папку `_shared`, внутри неё `budget`.
2. В `budget` создай Google Sheets → имя `Бюджет`.
3. Скопируй ID из URL: `https://docs.google.com/spreadsheets/d/1XyZ.../edit` → `1XyZ...` — это `BUDGET_SPREADSHEET_ID`.
4. Отдельно шарить этот файл не нужно (наследует доступ от `SVOI`), но если делал его в другом месте — расшарь на `client_email` как Editor.

**5.3. Таблица занятости**
1. Папка `SVOI/_shared/schedule` → новый Sheets → `Занятость`.
2. ID из URL → `SCHEDULE_SPREADSHEET_ID`.

**5.4. Календарь**
1. https://calendar.google.com → слева **Other calendars → + → Create new calendar** → имя `SVOI` → Create.
2. **Settings and sharing** этого календаря → **Share with specific people or groups → Add people** → `client_email` → права **Make changes to events** → Send.
3. Там же, блок **Integrate calendar** → `Calendar ID` (вида `abc123@group.calendar.google.com`) → это `CALENDAR_ID`.

Важно: сервис-аккаунт **не может** создать календарь, который будет виден тебе. Поэтому календарь создаёшь ты и шаришь боту — иначе события будут падать в невидимый календарь бота.

### Шаг 6. Заполнить `.env`
```env
GOOGLE_SA_JSON=secrets/google-sa.json
DRIVE_ROOT_ID=1AbCdEf...
BUDGET_SPREADSHEET_ID=1XyZ...
SCHEDULE_SPREADSHEET_ID=1Yya...
CALENDAR_ID=abc123@group.calendar.google.com
ENV=dev
```

### Шаг 7. Проверить и достроить структуру
```bash
source .venv/bin/activate
uv pip install google-api-python-client google-auth google-auth-httplib2

python scripts/bootstrap_google.py --dry-run   # только проверка доступа
python scripts/bootstrap_google.py             # создаёт _shared/{media,notes}, шапки листов
```
Скрипт идемпотентен: повторный запуск ничего не дублирует. Он проверяет доступ ко всем трём API, создаёт подпапки `media`, `notes`, приводит листы бюджета к контракту из `docs/architecture/03` (шапка `Транзакции`, `Справочники`, `Взносы`), создаёт лист `Занятость` с шапкой, и печатает сводку «что готово / чего не хватает».

---

## Часть 2. Итоговая структура Drive

```
SVOI/                            ← DRIVE_ROOT_ID, расшарена на SA (Editor)
├── _shared/                     ← общее, один экземпляр, без дублей в личных папках
│   ├── budget/Бюджет            ← BUDGET_SPREADSHEET_ID (истина по деньгам)
│   ├── schedule/Занятость       ← SCHEDULE_SPREADSHEET_ID (истина по доступности)
│   ├── media/                   ← фото, видео, афиши (создаёт скрипт)
│   └── notes/                   ← общие заметки труппы (создаёт скрипт)
└── TG_<chat_id>/                ← создаётся ботом при регистрации
    ├── Заметки/
    └── Файлы/
```

Правила доступа — из `ARCHITECTURE.md` §8.1: чтение `_shared` всем, запись в budget/schedule только `org`/`admin`, личные папки — только владельцу.

---

## Часть 3. Грабли, о которых лучше знать заранее

| Грабли | Что делать |
|---|---|
| `403 API has not been used` | не включён Sheets/Calendar/Drive API в проекте SA |
| `404 File not found` при верном ID | файл не расшарен на `client_email`, либо расшарен на другой аккаунт |
| Файлы бота «пропали» у тебя | создавались вне расшаренной папки — создавай только внутри `SVOI`, права наследуются |
| `storageQuotaExceeded` | сервис-аккаунт ограничен 15 ГБ; медиа грузят люди, бот только читает |
| `403 The caller does not have permission` при `spreadsheets.create` | **сервис-аккаунт не может создавать Google Sheets** (у него нет квоты Drive). Таблицу создаёт человек, SA получает доступ по наследству от папки. Папки SA создавать может — это проверено |
| `storageQuota.limit = 0`, ошибка `Service Accounts do not have storage quota` | проверено экспериментально: SA создаёт **папки** (квота не тратится), но **не может создать или загрузить ни один файл** — ни .txt, ни Google Doc, ни Google Sheet. Следствия: заметки хранятся в БД, медиа грузят люди, таблицы создаёт человек |
| Удалили SA | все созданные им файлы станут недоступны — не создавай критичные файлы от имени SA, лучше создавай их руками и шарь |
| OAuth вместо SA | refresh-token в режиме Testing живёт 7 дней → бот отвалится через неделю. Поэтому SA, а не OAuth |
| Календарь «не тот» | `CALENDAR_ID` должен быть из блока *Integrate calendar*, а не `primary` |

---

## Часть 4. Что делает `tool-api` с этими доступами

| Операция | API | Метод |
|---|---|---|
| Проверить свежесть файла (ADR-3) | Drive | `files.get(fields=modifiedTime)` |
| Прочитать транзакции | Sheets | `spreadsheets.values.get` |
| Дописать транзакцию | Sheets | `values.append(INSERT_ROWS)` |
| Прочитать `Свод` (формулы) | Sheets | `values.get(UNFORMATTED_VALUE)` |
| Записать занятость | Sheets | `values.append` |
| Создать папку `TG_<chat_id>` | Drive | `files.create(mimeType=folder)` |
| Сохранить заметку `.md` | Drive | `files.create` |
| Создать событие | Calendar | `events.insert` |
| Прочитать занятость площадки | Calendar | `events.list` |

n8n ни один из этих вызовов не делает — только `tool-api`.
