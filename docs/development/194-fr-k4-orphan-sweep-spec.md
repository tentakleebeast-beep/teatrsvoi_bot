# 194. FR-K4. Уборка брошенных загрузок — спецификация

**Дата:** 23.11.2026. **Карта:** `t_fb6f62e8`. **Дизайн:** `docs/development/193-fr-k4-orphan-uploads.md`
(ветка `wt/fr-k4-orphan-cleanup`, коммит `a623452`). **Требование:** FR-K4 в `docs/development/86`
§5.1, краевой случай — §7. **Контекст:** `docs/development/192` §15, §16 п.2 (уборки нет — карта).

**Что это.** Постановка для карты реализации: точные сигнатуры модуля, тексты, порядок шагов
в транзакции, две точки коммита с замком, юниты таймера, тесты по именам файлов и критерии
приёмки. **Кода здесь нет** — реализация отдельной картой (`worker-feature`).

**Что уже решено дизайном и здесь не пересматривается:** сирота = объект бакета
`bucket_private()` без строки `file_object` и старше 24 ч; журнала presign нет; убирает своё
задание (модуль + CLI + таймер), а не lifecycle-правило Garage; гонка с `commit` закрывается
advisory-замком `(7312, hashtext(key))`; стоп-кран `50 объектов или 10 %`; `deleted_at`,
`cold_at` и ключи вне формы — не сироты; лог построчно, аудит одной записью на прогон.

---

## 1. Область

| Файл | Что делаем |
|---|---|
| `services/tool-api/app/files_orphans.py` | **новый**: константы, `BucketObject`, `Orphan`, `ScanResult`, `SweepResult`, `listing`, `scan`, `sweep`, `_purge_one`, `_delete_object`, `_write_audit` |
| `services/tool-api/app/files_store.py` | `UPLOAD_LOCK_NS`, `UPLOAD_NOT_FINISHED`, `upload_lock`, `try_upload_lock` — рядом с `head_of` (701) и `delete_object` (720) |
| `services/tool-api/app/api/files.py` | `webapp_files_commit` (327): соединение, замок, `head_of`, `head_bytes`, `save_committed` — всё внутри одной транзакции; новый `detail` (342) |
| `services/tool-api/app/api/admin.py` | `_feedback_with_attachments`: замок на каждый ключ перед `head_of` (236) |
| `services/tool-api/app/pages/admin_core.py` | `ERRORS["file_not_uploaded"]` в словаре ядра (57–73) |
| `scripts/files_orphans.py` | **новый** CLI по образцу `scripts/cold_rotate.py` |
| `deploy/systemd/svoi-files-sweep.service`, `.timer` | **новые** два файла |
| `deploy/server-deploy.sh` | `svoi-files-sweep.timer` в список `enable` внутри `units --with-timers` (417–429) |
| `docs/infrastructure/35-deploy-runbook.md` | строка в таблице таймеров (после 345), два файла в `systemctl link` (401–402), таймер в `systemctl enable` (405–409) |
| `docs/development/86-files-and-storage.md` | снять «**В коде нет**» с FR-K4 (379), дополнить §7 (473) |
| `tests/integration/test_files_orphans.py` | **новый**: правила уборки на тестовой БД с подставным хранилищем |
| `tests/integration/test_files_orphans_garage.py` | **новый**: живой бакет, `skipif(not os.getenv("STORAGE_ENDPOINT"))` |

**Вне области.** Миграции нет (строк не добавляем). Новых HTTP-ручек нет (`POST /admin/files/cold-rotate`
дизайн отверг: мониторинга нет). Квота, видимость и корзина не меняются. Прод-бакет не чистится.
`docs/architecture/85` в этой ветке уже содержит нужную строку `file_not_uploaded` (124) — править
нечего, только не сломать `tests/unit/test_error_catalog.py`.

`files_store.delete_object` (720) **не трогаем**: он глотает ошибку хранилища и пишет `ERROR`,
потому что вызывается из путей ответа человеку. Уборке нужен честный результат, поэтому у неё
свой `_delete_object` (§3).

---

## 2. Контракт модуля `app/files_orphans.py`

```python
ORPHAN_AGE_HOURS = 24          # порог «брошено»
SWEEP_LIMIT = 500              # сколько объектов максимум удаляет один прогон
REFUSE_ABSOLUTE = 50           # стоп-кран: не больше стольких кандидатов
REFUSE_SHARE = 0.10            # ...или не больше такой доли объектов бакета
KEY_RE = re.compile(r"^p\d+/[0-9a-f]{32}(\.[A-Za-z0-9]{1,10})?$")
log = logging.getLogger("files_orphans")
```

`KEY_RE` — та же форма, что даёт `files_store.object_key` (617): `p<id>/<32 hex>` и необязательное
расширение до 10 знаков. Проверено на dev-бакете: под неё не попадает ровно один объект —
`a1b2/заметка.md` (33 байта, 29.10.2026), след проверки хранилища (`86` §8, Ш-1).

```python
@dataclass(frozen=True)
class BucketObject:
    key: str
    size: int
    modified: datetime          # tz-aware, из LastModified как есть (UTC)

@dataclass(frozen=True)
class Orphan:
    key: str
    size: int
    modified: datetime
    age_hours: float
    owner_id: int | None        # число из префикса p<id>; None — префикс не разобрался

@dataclass
class ScanResult:
    bucket: str
    scanned: int                # объектов в бакете (все страницы листинга)
    scanned_bytes: int
    in_shape: int               # из них в форме KEY_RE
    outside_shape: int          # остальные — не трогаем никогда
    db_rows: int                # строк file_object с непустым object_key
    db_rows_trash: int          # из них deleted_at is not null
    db_rows_cold: int           # из них cold_at is not null
    unconfirmed: list[Orphan]   # в форме, строки нет, возраст любой — по возрастанию возраста
    orphans: list[Orphan]       # из unconfirmed с age_hours >= hours — по возрастанию возраста
    ages: dict[str, int]        # {"<1ч","<24ч","<7д",">=7д"} по in_shape
    keys_missing: int           # строк с ключом, которых нет в бакете
    keys_missing_cold: int
    elapsed_ms: int
    scanned_at: datetime

    # свойства, чтобы CLI и тесты не считали одно и то же трижды
    @property
    def orphans_bytes(self) -> int
    @property
    def unconfirmed_bytes(self) -> int
    @property
    def share(self) -> float           # len(orphans) / scanned, 0.0 при scanned == 0
    @property
    def oldest(self) -> Orphan | None
    @property
    def by_owner(self) -> dict[int, int]   # owner_id -> число кандидатов, по убыванию

@dataclass
class SweepResult:
    scanned: int
    candidates: int             # len(orphans) до применения limit
    deleted: int
    bytes: int
    skipped_busy: int
    kept: int                   # строка появилась между листингом и замком
    errors: int                 # отказы хранилища
    refused: bool
    forced: bool
    limit: int
    oldest_days: int | None
    scanned_at: datetime
    by_participant: dict[str, int]
```

### 2.1 Функции

```python
def listing(bucket: str | None = None, client=None) -> tuple[list[BucketObject], int]:
    """Все объекты бакета одним листингом. Возвращает (объекты, миллисекунды).

    `client=None` → `files_store.storage()`; `bucket=None` → `files_store.bucket_private()`.
    Пагинатор `list_objects_v2`, страницы по 1000; `LastModified` и `Size` берутся как есть.
    Ничего не пишет.
    """

def scan(conn, *, hours: int = ORPHAN_AGE_HOURS, bucket: str | None = None,
         client=None, now: datetime | None = None) -> ScanResult:
    """Счётчик без удаления. Только чтение: ни замков, ни delete_object."""

def sweep(conn, *, hours: int = ORPHAN_AGE_HOURS, limit: int = SWEEP_LIMIT,
          force: bool = False, bucket: str | None = None,
          now: datetime | None = None) -> SweepResult:
    """Один прогон уборки. `conn` — соединение в autocommit=True: транзакция на объект (§3)."""

def _purge_one(conn, orphan: Orphan, bucket: str) -> str:
    """Один объект в одной транзакции. Возвращает "deleted" | "busy" | "kept" | "failed"."""

def _delete_object(bucket: str, key: str) -> None:
    """Удалить объект; отказ хранилища — исключение STORAGE_ERRORS наружу (не глотаем)."""

def _write_audit(conn, result: SweepResult, *, action: str) -> None: ...
```

`conn` в `sweep`/`_purge_one` обязан быть `autocommit=True`. Это не пожелание: `_purge_one`
открывает `with conn.transaction()` на каждый объект, а в неавтокоммитном соединении эта
конструкция сделала бы одну общую транзакцию на весь прогон, и один сбойный ключ откатил бы
уже удалённые записи. В CLI — `psycopg.connect(dsn(), autocommit=True)`, в тестах — так же.

### 2.2 Сколько строк в БД читаем

`scan` делает один `select object_key, bucket, deleted_at, cold_at from file_object
where object_key is not null` и складывает в словарь по ключу. Это весь `file_object`, а не
только кандидаты: `keys_missing` (строки, которых нет в бакете) иначе не посчитать. Объём
ограничен квотой (2 ГБ на человека, `QUOTA_BYTES`) — на dev это 0 строк, на проде десятки
тысяч максимум. Если однажды станет сотни тысяч, это отдельная карта, а не тихая деградация.

### 2.3 Совпадение строки — по паре (ключ, бакет), но `bucket is null` спасает объект

Условие «строка есть»:

```sql
select 1 from file_object
 where object_key = %s and (bucket = %s or bucket is null)
```

`bucket is null` — **уточнение дизайна §D1**, и вот почему. Сегодня `bucket` пишут все три
ветки вставки (`files_store` 306, 314: `bucket_private()` в самом `insert`), поэтому строк
с ключом и пустым бакетом быть не может. Но если такая строка всё же появится (руками,
легаси, будущая ветка), буквальное сравнение `bucket = %s` объявило бы живой файл сиротой и
удалило байты, на которые строка ссылается: скачивание отдало бы 404 при живой строке.
Цена ошибки в одну сторону — «мусор полежит ещё час», в другую — «файл человека потерян».
Поэтому `null` считается «бакет неизвестен → не наш случай, не трогаем».

Строка с **другим** бакетом сироту не спасает: она ссылается на байты в другом месте
(тест §8 п.7 дизайна это и проверяет).

---

## 3. Порядок шагов на объект и что делать при сбое

`_purge_one` целиком:

```python
def _purge_one(conn, orphan, bucket):
    with conn.transaction():                                  # 1. своя транзакция
        if not files_store.try_upload_lock(conn, orphan.key):  # 2. try-замок, не ждём
            return "busy"
        row = conn.execute(                                   # 3. перечитать строку под замком
            "select 1 from file_object where object_key = %s"
            " and (bucket = %s or bucket is null)", (orphan.key, bucket)).fetchone()
        if row is not None:
            return "kept"                                     # коммит успел — оставляем
        try:
            _delete_object(bucket, orphan.key)                # 4. удалить байты (сеть)
        except STORAGE_ERRORS as exc:
            log.error("не удалось удалить %s: %s", orphan.key, exc)
            return "failed"                                   # 5. без записи в БД
        log.info("удалён объект %s (%d байт, возраст %.1f ч)",
                 orphan.key, orphan.size, orphan.age_hours)   # 6. строка в журнал
    return "deleted"                                          # 7. commit, замок снят
```

Почему именно так:

1. **Замок раньше чтения.** Между листингом и удалением коммит мог вставить строку; проверка
   без замка это окно не закрывает.
2. **Удаление внутри транзакции.** Замок держится всё время сетевого вызова `delete_object`
   (десятки миллисекунд). Пришедший в это окно `commit` встанет на `pg_advisory_xact_lock`
   и после нашего commit увидит `head_of` → `None` → 404. Строки без объекта не появится.
3. **`try`-замок, а не ожидание.** Свип не должен стоять на чужом коммите: занятый ключ
   пропускается, следующий прогон его доберёт. Один и тот же ключ не «теряется»: он остаётся
   объектом без строки и снова попадёт в листинг.
4. **Сбой хранилища — не отказ прогона.** `return "failed"` внутри `with conn.transaction()`
   делает commit пустой транзакции (замок снимается), `errors` растёт, следующий объект
   обрабатывается. `raise` здесь был бы хуже: одна недоступная страница бакета отменила бы
   весь часовой прогон.
5. **Транзакция пустая по записи.** В БД внутри неё ничего не пишется, поэтому отказ commit'а
   означает обрыв соединения: исключение `DB_ERRORS` уходит из `sweep` наружу, CLI печатает
   ошибку и выходит с кодом 1. Байты к этому моменту уже удалены, строка журнала написана —
   это честный след, врать не о чем.

Порядок по прогону (`sweep`):

1. `result = scan(...)` — один листинг, один `select` по БД, ноль удалений.
2. Кандидатов нет → **ничего не пишем** (ни журнала, ни аудита) и возвращаем нули.
3. Стоп-кран: `refused = candidates > REFUSE_ABSOLUTE and candidates > REFUSE_SHARE * scanned`
   (то есть удаляем, только если кандидатов ≤ 50 **или** ≤ 10 % объектов бакета). `force=True`
   вердикт игнорирует.
4. `refused` → `log.warning(...)`, аудит `file.orphan.sweep_refused`, ни одного `_purge_one`.
5. Иначе — по `result.orphans[:limit]` (уже отсортированы от старых к новым), считаем
   `deleted/bytes/skipped_busy/kept/errors`.
6. Если `deleted > 0` — итоговая строка журнала и аудит `file.orphan.sweep`.

`limit` ограничивает объём работы, а **не** вердикт стоп-крана: вердикт считается по полному
числу кандидатов. Иначе `--limit 10` на «БД восстановили из бэкапа» прошёл бы стоп-кран
и удалил десять живых файлов.

---

## 4. Замок и две точки коммита

### 4.1 `files_store`

```python
UPLOAD_LOCK_NS = 7312          # 7311 занят миграциями (migrations/env.py:35)
UPLOAD_LOCK_WAIT = "5s"
UPLOAD_NOT_FINISHED = ("Загрузка не завершилась: файл не дошёл или убран после суток "
                       "без подтверждения.")

def upload_lock(conn, key: str) -> None:
    """Взять замок загрузки ключа; не дождался — FileRejected 404 «не подтверждено»."""
    conn.execute("set local lock_timeout = %s", (UPLOAD_LOCK_WAIT,))
    try:
        conn.execute("select pg_advisory_xact_lock(%s, hashtext(%s))",
                     (UPLOAD_LOCK_NS, key))
    except psycopg.errors.LockNotAvailable as exc:
        raise FileRejected("file_not_uploaded", UPLOAD_NOT_FINISHED, 404) from exc

def try_upload_lock(conn, key: str) -> bool:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("select pg_try_advisory_xact_lock(%s, hashtext(%s))",
                    (UPLOAD_LOCK_NS, key))
        row = cur.fetchone()
    return bool(row[0])
```

Проверено на живой dev-БД (23.11.2026):

- `hashtext` даёт `int4` (`select hashtext('p21/…')` → `-1111186627`), обе функции принимают
  `(int, int)` — типы сходятся;
- `set local lock_timeout='500ms'` внутри транзакции даёт ровно `psycopg.errors.LockNotAvailable`,
  SQLSTATE `55P03`, ожидание 0.511 с при держателе в другом соединении;
- `conn.cursor(row_factory=tuple_row)` работает на соединении с `dict_row` (так открывает
  пул `core._make_pool`) — поэтому `try_upload_lock` не зависит от фабрики строк вызывающего.

`FileRejected` наследуется от `ApiError`, поэтому отказ замка доходит до клиента тем же
конвертом без переупаковки в ручке: код `file_not_uploaded`, статус 404, текст
`UPLOAD_NOT_FINISHED`. `set local` живёт до конца транзакции и не протекает в следующую —
поэтому он и выбран вместо `set`.

Коллизия `hashtext` (32 бита на ключ) означает «два разных ключа делят замок»: свип пропустит
объект как занятый, коммит подождёт чужой. Это задержка, а не потеря и не удаление живого
файла — отдельной защиты не требует.

### 4.2 `api/files.py` — `webapp_files_commit`

Сейчас `head_of` (340) и `head_bytes` (348) вызываются **до** открытия соединения (352), то есть
вне транзакции и вне замка. Новый порядок — всё внутри одного `with`:

```python
    head = ...                                    # ← этих двух строк до `with` больше нет
    with core.pool.connection() as conn:          # 352
        files_store.upload_lock(conn, body.object_key)     # ← новое: замок до head_of
        head = files_store.head_of(body.object_key)
        if head is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, files_store.UPLOAD_NOT_FINISHED,
                           code="file_not_uploaded")
        size, content_type = head
        ...                                                # проверки пустого и >200 МБ как есть
        head16 = files_store.head_bytes(body.object_key)
        saved = files_store.save_committed(...)            # под тем же замком
        core.audit(conn, "human", participant_id, "file.upload", ...)
```

Решение по отказу: `LockNotAvailable` **не** отдаётся отдельным кодом — `upload_lock` бросает
`FileRejected("file_not_uploaded", …, 404)`, ручка его не переупаковывает (в ней уже стоит
`except files_store.FileRejected: raise`). Это буква дизайна §D5 п.2: «отвечает тем же отказом,
что и „объекта нет“». На экране человек всё равно видит слова из `ERRORS` (§5), а не `detail`.

Текст `detail` в 342 заменяется на `files_store.UPLOAD_NOT_FINISHED` — один источник для двух
сообщений (404 «объекта нет» и 404 «замок занят»).

`delete_object` в ветках «файл пустой» и «файл больше 200 МБ» остаётся как есть: там объект
не наш, он появился только что, и удаление под замком уже безопасно.

### 4.3 `api/admin.py` — `_feedback_with_attachments`

`with core.pool.connection()` открыт на 226, `head_of` — на 236, транзакция уже идёт. Вставляем
одну строку перед 236:

```python
                files_store.upload_lock(conn, item.object_key)   # замок до head_of
                head = files_store.head_of(item.object_key)
```

`try/except` не нужен: `FileRejected` уже несёт `code="file_not_uploaded"` и статус 404, а экран
показывает слова из `ERRORS` по коду. Текст `detail` в этом пути будет общий («загрузка не
завершилась»), а не «скриншот не долетел» — это осознанно: один код — один смысл, различать
«не дошёл» и «занят» человеку нечего.

`lock_timeout` — 5 с на каждый ключ, до трёх вложений в обращении: худший случай ожидания
15 с, дальше — отказ всей формы (существующее поведение: `finally` на `api/admin.py:277`
снимает уже загруженные байты). Приемлемо: столько ждёт только одновременный свип того же ключа.

`upload_stream` (бот) замок не берёт: он кладёт байты и сразу пишет строку в одном запросе,
а объект моложе 24 ч сиротой не считается ни в одном прогоне.

---

## 5. Тексты

### 5.1 Человеку в Mini App

`services/tool-api/app/pages/admin_core.py`, словарь `ERRORS` (57–73), новая строка
после `"no_spreadsheet"`:

```js
    "file_not_uploaded": "Файл не дошёл до хранилища или убран после суток без подтверждения. Загрузите заново."
```

`detail` API — `files_store.UPLOAD_NOT_FINISHED`:

```
Загрузка не завершилась: файл не дошёл или убран после суток без подтверждения.
```

Код `file_not_uploaded` не меняется, новых кодов нет. Тот же код ставят три других места
(`api/files.py` 158, 659, 714 — «строка есть, ключа нет»): они начнут показывать новую фразу
на экране, потому что `problem(res)` читает `code` раньше `status`. Это правильно: смысл
«файла за байтами нет, загрузите заново» у них тот же.

### 5.2 `--status` (сводка, ничего не удаляет)

Формат — ровно эти строки, значения подставляет код:

```
бакет: svoi-private
объектов в бакете: 837, байт: 1240368
строк file_object с ключом: 0 (живых 0, в корзине 0, в холоде 0)
листинг занял: 130 мс
объектов вне формы ключа presign (не трогаем): 1
без строки в форме p<id>/<uuid>: 836, из них старше 24 ч: 803, байт: 1238388
возраст (в форме): {'<1ч': 3, '<24ч': 30, '<7д': 192, '>=7д': 611}
самая старая сирота: p21/3709f160c52d435d8a13666715cb507f.pdf от 2026-10-31 01:39 UTC, возраст 547 ч
по владельцам (топ-5): [(477, 107), (560, 107), (435, 76), (432, 44), (476, 41)]
кандидатов к удалению: 803 из 837 (95.9 %) — стоп-кран: остановлено (порог 50 объектов или 10 %)
ключей в БД, которых нет в бакете: 0 (из них холодных 0)
```

Расшифровка чисел, чтобы их не путали: «без строки» — все объекты в форме ключа, у которых
нет строки (`unconfirmed`, 836); «кандидатов к удалению» — они же старше 24 ч (`orphans`, 803);
разница 33 объекта моложе суток. Строка «стоп-кран» печатает вердикт того же правила, что
применит `sweep` (`пройдёт`, если ≤ 50 или ≤ 10 %). Формат даты — `YYYY-MM-DD HH:MM UTC`,
возраст — целые часы. При `scanned == 0` строка «самая старая сирота» печатает `нет`,
`по владельцам` — `[]`.

### 5.3 `--dry-run` (то же плюс список и вердикт)

После блока §5.2 добавляет:

```
вердикт: остановлено — 803 из 837 (95.9 %), порог 50 объектов или 10 %; нужен --force
к удалению (старые первыми, --limit 500): 803
  2026-10-31 01:39  p21/3709f160c52d435d8a13666715cb507f.pdf  85 байт
  2026-10-31 01:44  p477/0f2c…e1.pdf  1024 байт
  …
```

При проходящем вердикте первая строка: `вердикт: пройдёт — 12 из 900 (1.3 %), порог 50
объектов или 10 %`. Печатается не больше `--limit` строк; если кандидатов больше, добавляется
строка `… и ещё 303 сверх --limit`.

### 5.4 Журнал (journald, `logging.getLogger("files_orphans")`)

| Когда | Уровень | Формат |
|---|---|---|
| объект удалён | `INFO` | `удалён объект %s (%d байт, возраст %.1f ч)` |
| итог прогона, если `deleted > 0` | `INFO` | `свип: проверено %d, кандидатов %d, удалено %d (%d байт), пропущено занятых %d, оставлено %d, ошибок %d` |
| стоп-кран | `WARNING` | `свип остановлен: кандидатов %d из %d (%.1f %%), порог %d объектов или %d %% — нужен --force` |
| отказ хранилища | `ERROR` | `не удалось удалить %s: %s` |

«оставлено %d» — счётчик `kept` (коммит успел между листингом и замком). Это расширение
формата §D9 на одно поле; без него гонка выглядела бы как «удалено меньше, чем кандидатов,
и никто не знает почему». Тихий прогон не пишет ничего: 24 пустые строки в сутки — шум.

### 5.5 Аудит — одна запись на прогон

```python
core.audit(conn, "system", None, "file.orphan.sweep", "storage", bucket,
           after={"scanned": ..., "candidates": ..., "deleted": ..., "bytes": ...,
                  "skipped_busy": ..., "kept": ..., "errors": ...,
                  "oldest_days": ..., "forced": bool,
                  "by_participant": {"477": 107, ...}},
           source={"by": "scripts/files_orphans.py"})
```

- при сработавшем стоп-кране `action="file.orphan.sweep_refused"`, `after.deleted = 0`;
- при `deleted == 0` и без отказа записи нет вовсе (пустой прогон не забивает hash-цепочку);
- запись пишется своей транзакцией `with conn.transaction()` после цикла, `entity_id` — имя
  бакета (удалённых объектов в БД нет, привязывать событие не к чему).

---

## 6. CLI `scripts/files_orphans.py`

По образцу `scripts/cold_rotate.py`: `apply_env()`, `add_tool_api_path()`, `from _cli import dsn`,
`psycopg.connect(dsn(), autocommit=True)`.

| Флаг | Смысл |
|---|---|
| без флагов | прогон уборки (так его зовёт таймер) |
| `--status` | сводка §5.2, выход |
| `--dry-run` | сводка + список + вердикт §5.3, выход |
| `--hours N` | порог возраста, по умолчанию `ORPHAN_AGE_HOURS = 24` |
| `--limit N` | сколько объектов максимум удалить, по умолчанию `SWEEP_LIMIT = 500` |
| `--force` | игнорировать стоп-кран (осознанное действие человека) |
| `--json` | те же данные машинно (`asdict` результата) |

Коды выхода: `0` — удалено сколько-то или удалять нечего; `2` — сработал стоп-кран (нужен
человек); `1` — отказ хранилища на листинге или ошибка БД (прогон не выполнен). Код 2 при
`Type=oneshot` даёт в systemd состояние `failed` — это единственный сигнал о массовой уборке,
пока V-130 не решён, и он намеренный: ровный `active (exited)` на отказе означал бы, что
мусор копится молча.

`--status`/`--dry-run` не берут ни замков, ни транзакций записи: только `list_objects_v2`
и один `select`. Если `STORAGE_ENDPOINT` не задан, `files_store.storage()` бросит
`FileRejected("storage_unavailable")`: CLI печатает `хранилище не настроено: STORAGE_ENDPOINT
не задан` и выходит с кодом 1.

---

## 7. Таймер, деплой, рунбук

`deploy/systemd/svoi-files-sweep.service`:

```ini
[Unit]
Description=SVOI files: убрать брошенные загрузки старше суток (FR-K4, docs/development/193)
Documentation=file:///opt/svoi/docs/development/193-fr-k4-orphan-uploads.md
After=network.target

[Service]
Type=oneshot
User=svoi
Group=svoi
WorkingDirectory=/opt/svoi
EnvironmentFile=/opt/svoi/.env
ExecStart=/opt/svoi/.venv/bin/python scripts/files_orphans.py
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
```

`deploy/systemd/svoi-files-sweep.timer`:

```ini
[Unit]
Description=SVOI files: уборка брошенных загрузок, каждый час

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/server-deploy.sh` (417–429): дописать `svoi-files-sweep.timer` в список
`systemctl enable` внутри `if [ "$with_timers" = "1" ]`. `scripts/check_services.py` требует
у юнита `ExecStart`, `WorkingDirectory`, `EnvironmentFile` и существующий скрипт по пути
`/opt/svoi/...` — всё три на месте; в `REQUIRED` (обязательный набор) юнит **не** добавляем:
уборка не входит в «без чего не работают уведомления».

`docs/infrastructure/35-deploy-runbook.md` — три правки, иначе `tests/unit/test_deploy_scripts.py`
(372, 381) краснеет:

1. строка таблицы после `svoi-cold-rotate` (345):
   `| `svoi-files-sweep.service`/`.timer` | `scripts/files_orphans.py` — убирает объекты, загруженные по presigned-ссылке и не подтверждённые за 24 ч (FR-K4, docs/development/193); стоп-кран 50 объектов или 10 % | каждый час |`
2. блок `systemctl link` (401–402): `…/svoi-files-sweep.service` и `…/svoi-files-sweep.timer`;
3. блок `systemctl enable` (405–409): `svoi-files-sweep.timer`.

---

## 8. Тесты

Дизайн §9 назвал unit-файл `tests/unit/test_files_orphans.py` «на тестовой БД». **Правим
имя:** в этом репозитории ни один unit-тест не открывает БД (`grep "test_db" tests/unit/*.py`
даёт только упоминания в комментариях и в `test_db_guard`), а `tests/unit/conftest.py` намеренно
стережёт окружение от подмены; DB-тесты живут в `tests/integration` и идут в CI на настоящем
Postgres (`.github/workflows/tests.yml`, шаг «тесты (unit + integration на реальном Postgres)»).
Поэтому оба файла — integration, и ни один из них не требует внешних сервисов, кроме БД.

### 8.1 `tests/integration/test_files_orphans.py` — правила на тестовой БД

Хранилище подставное: оба seam'а модуля — `files_store.storage` и `files_store.bucket_private`
(monkeypatch), фейк отдаёт `get_paginator("list_objects_v2").paginate(Bucket=…)` со списком
объектов `{Key, Size, LastModified}` и пишет удалённые ключи в `deleted`, а по требованию
бросает `ClientError` на заданном ключе. Ключи и строки — только свои: фикстура удаляет
`file_object` с `object_key like 'p9%'` и не полагается на пустоту базы (сессия `test_db`
общая).

| Тест | Что доказывает |
|---|---|
| `test_старый_объект_без_строки_удалён` | основное обещание FR-K4: `deleted == [key]`, `orphan` нет в бакете |
| `test_молодой_объект_без_строки_оставлен` | 24 ч — порог, а не «сразу» |
| `test_старый_объект_со_строкой_оставлен` | БД — источник истины, возраст её не отменяет |
| `test_объект_в_корзине_оставлен` | `deleted_at` — обратимое удаление (FR-K21) |
| `test_объект_в_холоде_оставлен` | `cold_at` — строка есть, байтов в бакете нет |
| `test_ключ_вне_формы_оставлен` | `_smoke/x.pdf` и `a1b2/заметка.md` не трогаются |
| `test_строка_в_другом_бакете_не_спасает_объект` | сравнение идёт по паре (§2.3) |
| `test_строка_с_пустым_бакетом_спасает_объект` | `bucket is null` → не сирота (§2.3) |
| `test_стоп_кран_на_60_из_60` | `refused is True`, `deleted == 0`, аудит `file.orphan.sweep_refused` |
| `test_force_удаляет_и_пишет_аудит` | `--force` — осознанное действие; одна строка `file.orphan.sweep` |
| `test_limit_удаляет_самые_старые` | 5 кандидатов, `limit=3` → удалены три с наименьшим `modified` |
| `test_квота_и_список_не_меняются` | `used_bytes` и `list_files` до/после равны (FR-K4 «не в квоте») |
| `test_ноль_удалённых_без_строки_аудита` | пустой прогон не пишет в `audit_event` |
| `test_сбой_хранилища_не_останавливает_прогон` | `errors == 1`, остальные удалены, `failed` в итоге |
| `test_scan_ничего_не_удаляет` | после `scan` фейк `deleted == []` |
| `test_занятый_ключ_пропускается` | замок в другом соединении → `_purge_one` вернул `busy`, объект цел |

### 8.2 `tests/integration/test_files_orphans_garage.py` — живой dev-бакет

`pytestmark = pytest.mark.skipif(not os.getenv("STORAGE_ENDPOINT"), reason="Garage не поднят")`,
как `tests/integration/test_cold.py`. Ключи создаются под своим участником (как `_person`
в `test_cold.py`) и удаляются в `finally`.

**Полный `sweep` на живом бакете не запускается.** Дизайн §9 предлагал «свип с порогом 0 ч» —
на dev это либо отказ стоп-крана (836 кандидатов), либо удаление чужих объектов, если порог
обойти. Поэтому живой тест проверяет `_purge_one` на своём ключе и `scan` без записи; полный
`sweep` закрыт тестами §8.1.

| Тест | Что доказывает |
|---|---|
| `test_scan_на_живом_бакете_не_удаляет` | сводка считается, листинг до/после одинаков |
| `test_свип_удаляет_свой_объект` | presigned PUT 1 КБ → `_purge_one` → `head_of` вернул `None` |
| `test_коммит_ждёт_замок_и_отвечает_404` | второе соединение держит `pg_advisory_xact_lock(7312, hashtext(key))`; `POST /webapp/files/commit` ждёт ~5 с и отдаёт 404 `file_not_uploaded` с новым `detail` — доказывает, что замок берётся **до** `head_of` |
| `test_обрыв_put_не_создаёт_объекта` | повтор пробы §3 дизайна: недокачанный PUT мусора не оставляет |

`test_коммит_ждёт_замок_и_отвечает_404` занимает ~5 с (предел `UPLOAD_LOCK_WAIT`); это
единственный медленный тест файла, и он же единственное доказательство порядка «замок →
`head_of`» на живом пути.

---

## 9. Критерии приёмки

1. `python scripts/files_orphans.py --status` на dev-бакете печатает блок §5.2 и **ничего не
   удаляет** — листинг до и после совпадает по числу и байтам.
2. `--dry-run` на dev-бакете печатает вердикт `остановлено` и не больше `--limit` ключей;
   `STORE`-объекты не тронуты (`head_of` до/после).
3. Правила §D7 выполняются тестами §8.1: старый без строки — удалён; молодой, со строкой,
   в корзине, в холоде, вне формы — нет; пара (ключ, бакет) сравнивается, `bucket is null`
   спасает.
4. Стоп-кран: 60 кандидатов из 60 → `refused`, `deleted == 0`, аудит `file.orphan.sweep_refused`;
   `force=True` удаляет, аудит `file.orphan.sweep`; `limit=3` при 5 кандидатах удаляет три
   самых старых.
5. Гонка: коммит, идущий одновременно со свипом, либо выигрывает (строка есть, объект цел),
   либо отказывает 404 — строки без объекта не появляется ни в одном порядке; занятый ключ
   даёт `skipped_busy`, объект остаётся.
6. Человек, вернувшийся через два дня, видит «Файл не дошёл до хранилища или убран после
   суток без подтверждения. Загрузите заново.», а не «Не найдено»: `ERRORS["file_not_uploaded"]`
   есть в `admin_core.py`, `detail` в `api/files.py` — `UPLOAD_NOT_FINISHED`.
7. `used_bytes` и ответ `/webapp/files/list` до и после прогона совпадают (тест §8.1).
8. Аудит: ровно одна строка на прогон с числами; при нуле удалённых и без отказа — ни одной
   (тест §8.1).
9. `systemctl`-файлы: `tests/unit/test_deploy_scripts.py` зелёный целиком (оба теста таймеров),
   `python scripts/check_services.py` — без ошибок.
10. `python scripts/check_deadcode.py` — 0 новых находок. Если `scan` или `_purge_one`
    окажутся «используются только тестами», ключ в `config/deadcode-baseline.txt` **не**
    добавляется: они зовутся из CLI и из `sweep`, то есть находка будет означать ошибку
    раскладки вызовов.
11. `pytest tests/integration/test_files_orphans.py` зелёный без `STORAGE_ENDPOINT` (БД есть —
    CI её даёт); `tests/integration/test_files_orphans_garage.py` — зелёный с `STORAGE_ENDPOINT`
    и `skipped` без него.
12. `python scripts/check_done.py --quick` зелёный, кроме известного I1 на пустой dev-БД,
    если он красный и на `main` (проверять на `main` тем же прогоном).
13. `docs/README.md` пересобран (`python scripts/build_docs_index.py`) — `tests/unit/test_docs_links.py`
    зелёный.

---

## 10. Документы после реализации

| Документ | Что меняется |
|---|---|
| `docs/development/86` строка FR-K4 (379) | вместо «**В коде нет:** механизм спроектирован в `docs/development/193`, реализация — отдельной картой» → «Механизм: `app/files_orphans.py` + `scripts/files_orphans.py` + таймер `svoi-files-sweep` раз в час (`docs/development/193`, `docs/development/194`); стоп-кран 50 объектов или 10 % бакета» |
| `docs/development/86` §7 (473) | к фразе «Объект убран через 24 ч (FR-K4, `docs/development/193`)» дописать «— `scripts/files_orphans.py`, прогон раз в час, порог 24 ч» |
| `docs/architecture/85` (124) | **не править**: строка `file_not_uploaded` уже говорит «не долетел или убран через сутки без подтверждения»; критерий — `tests/unit/test_error_catalog.py` зелёный |
| `docs/infrastructure/35` | §7 этой спеки |

Оба текста в `docs/development/86` пишутся **после** того, как реализация действительно
работает: пометка «в коде нет» снимается коммитом реализации, не этим.

---

## 11. Числа: замер, а не цитата

Команда замера — `list_objects_v2` + `select … from file_object` (та же, что в `--status`);
скрипт прогонялся на dev 23.11.2026, ничего не удалял.

| Что | Дизайн 22.11.2026 | Замер 23.11.2026 |
|---|---|---|
| объектов в бакете | 834 | **837** (+3 за сутки: прогоны тестов) |
| байт в бакете | 1 240 191 | 1 240 368 |
| из них в форме `KEY_RE` | 833 | 836 |
| вне формы ключа | 1 (`a1b2/заметка.md`) | 1 (тот же) |
| строк `file_object` | 0 | 0 |
| без строки (любой возраст) | 833 | 836 |
| кандидатов (без строки и ≥ 24 ч) | 611 «старше недели», 833 в §6 | **803** |
| моложе 24 ч | 27 | 33 |
| возрасты | 3 / 27 / 192 / 611 | 3 / 30 / 192 / 611 |
| самая старая сирота | `p21/…pdf`, 31.10.2026 | та же, 547 ч |
| листинг | 101 мс | 130 мс |

**Расхождение, которое надо назвать вслух:** §6 дизайна называет «сиротами» 833 объекта —
это все объекты в форме без строки, без фильтра по возрасту; D1 при этом определяет сироту
как «нет строки **и** ≥ 24 ч». В спецификации эти два числа разведены: `unconfirmed` (836)
и `candidates` (803), и стоп-кран считается по второму. На вердикт это не влияет (803 > 50
и 95.9 % > 10 %), но в отчётах и в `--status` одно число вместо двух дало бы неверный ответ
на вопрос «сколько удалит прогон».

Третье: 1,24 МБ на 836 объектов — это dev (тесты кладут крошечные файлы). Объём прода по этим
числам оценивать нельзя; число объектов показывает механику и то, что их никто не убирал.

---

## 12. Отличия от дизайна 193 и почему

| Что | Дизайн | Спецификация | Почему |
|---|---|---|---|
| файл тестов на БД | `tests/unit/test_files_orphans.py` | `tests/integration/test_files_orphans.py` | ни один unit-тест в репозитории не открывает БД; CI гоняет integration на настоящем Postgres |
| `sweep` на живом бакете | «свип с порогом 0 ч» | `_purge_one` на своём ключе + `scan` | порог 0 ч на dev = 836 кандидатов: либо стоп-кран, либо удаление чужих объектов |
| стоп-кран считает | «833 кандидата из 834» | кандидаты = без строки **и** ≥ 24 ч (803) | §6 дизайна посчитал «сирот» без фильтра возраста; D1 фильтр требует |
| `bucket is null` | пара (ключ, бакет) | строка с пустым бакетом спасает объект | иначе легаси-строка теряет байты живого файла; все вставки пишут бакет, случай нештатный |
| итоговая строка журнала | 5 чисел | 6 (добавлено `оставлено`) | без счётчика `kept` гонка выглядит как «удалили меньше, чем нашли» |
| код выхода CLI | не задан | 0 / 1 / 2 (+ systemd `failed` при отказе) | пока V-130 не решён, красный юнит — единственный сигнал о массовой уборке |
| `detail` от отказа замка | «тот же 404» | `UPLOAD_NOT_FINISHED`, общий с «объекта нет» | одно сообщение на два случая, различить их человеку нечем |

---

## 13. Границы и риски

- **Кода нет.** Реализация — карта `worker-feature`; эта карта даёт постановку.
- **Прод-бакет не чистим.** Первый прогон на проде встретит стоп-кран и потребует `--force`
  руками — это и есть задуманный порядок: сначала `--status`, потом решение.
- **Квоту, видимость, корзину не меняем.** FR-K21 (30 дней) — вопрос V-129; алерт о массовой
  уборке — V-130. Спецификация от них не зависит.
- **Риск, названный явно:** `--force` на большом бакете удаляет безвозвратно; в спецификации
  поэтому `--force` — только ручной флаг, а не настройка таймера, и `limit` по умолчанию 500.
- **Риск отката БД:** если базу восстановили из бэкапа, все файлы за сутки станут сиротами;
  защищает стоп-кран (803 из 837 — отказ). Это же поведение делает первый прогон на любом
  «грязном» бакете отказом, а не уборкой.

---

## 14. Документы

| Документ | Отношение |
|---|---|
| `docs/development/193` | дизайн: решения D1–D9, замер, тесты §8–§9 |
| `docs/development/86` §5.1, §7 | требование FR-K4 и краевой случай |
| `docs/development/192` §15, §16 п.2 | E4: уборки нет, карта заведена |
| `docs/architecture/85` | `file_not_uploaded` — смысл уже уточнён |
| `docs/infrastructure/35` | таблица таймеров и ручной путь включения |
| `docs/reports/74-questions.md` | V-129 (корзина), V-130 (алерт) — работу не блокируют |
