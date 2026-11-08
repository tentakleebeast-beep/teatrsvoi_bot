# 89. Измерения интерфейса Mini App

**Дата:** 31.10.2026
**Зачем:** разбор дизайна (`docs/development/90-ui-review.md`) должен опираться на числа,
которые можно повторить, а не на впечатления от скриншотов.

Воспроизведение:

```bash
env -u PYTHONPATH .venv/bin/python scripts/ux_audit.py            # краткая сводка
env -u PYTHONPATH .venv/bin/python scripts/ux_audit.py --markdown # эта таблица
```

Стенд — `tests/webapp/server.py`, движок Chromium, схемы по умолчанию светлая.
Считается: высота полотна, переполнение по горизонтали, контейнеры с собственной
горизонтальной прокруткой, цели нажатия меньше 44 px, самый мелкий шрифт видимого
текста, эмодзи в тексте.

Важно: `/webapp/schedule` стенд не отдаёт (страница есть в боевом `main.py`, но её нет
в `PAGE_ROUTES` стенда) — в таблице она видна как `35 px`. Это отдельное упущение
стенда, исправляется шагом Ш-11 плана `docs/plans/94-ux-ui-rework-plan.md`.

## Таблица

| Экран | Высота | Переполнение | Гориз. скролл | Цели < 44 px | Мелкий шрифт | Эмодзи |
|---|---|---|---|---|---|---|
| `/webapp/home` @390 | 399 px | 0 px | — | 0 из 3 | 12 px | 🗓 🔔 🛠 |
| `/webapp/days` @390 | 506 px | 0 px | — | 2 из 6 | 11 px | — |
| `/webapp/my/sheet` @390 | 127 px | 0 px | — | 1 из 1 | 10 px | — |
| `/webapp/schedule` @390 | 35 px | 0 px | — | 0 из 0 | 13 px | — |
| `/webapp/tx` @390 | 543 px | 0 px | — | 12 из 14 | 12 px | — |
| `/webapp/dues` @390 | 414 px | 0 px | — | 0 из 0 | 13 px | — |
| `/webapp` @390 | 863 px | 0 px | — | 6 из 15 | 14 px | — |
| `/webapp/slots` @390 | 327 px | 0 px | — | 4 из 7 | 12 px | — |
| `/webapp/notifications` @390 | 483 px | 0 px | — | 1 из 4 | 12 px | — |
| `/webapp/broadcast` @390 | 478 px | 0 px | — | 4 из 6 | 13 px | — |
| `/webapp/feedback` @390 | 507 px | 0 px | — | 0 из 6 | 13 px | — |
| `/webapp/reconcile` @390 | 346 px | 0 px | — | 0 из 2 | 12 px | — |
| `/webapp/admin/t/people` @390 | 539 px | 0 px | 2 | 18 из 23 | 13 px | — |
| `/webapp/admin/t/budget` @390 | 357 px | 0 px | 1 | 3 из 8 | 13 px | — |
| `/webapp/admin/t/audit` @390 | 239 px | 0 px | 1 | 3 из 8 | 13 px | — |
| `/webapp/ui` @390 | 1617 px | 0 px | — | 2 из 14 | 11 px | 📅 💸 |
| `/webapp/home` @320 | 399 px | 0 px | — | 0 из 3 | 12 px | 🗓 🔔 🛠 |
| `/webapp/days` @320 | 506 px | 0 px | — | 2 из 6 | 11 px | — |
| `/webapp/my/sheet` @320 | 150 px | 0 px | — | 1 из 1 | 10 px | — |
| `/webapp/schedule` @320 | 35 px | 0 px | — | 0 из 0 | 13 px | — |
| `/webapp/tx` @320 | 543 px | 0 px | — | 12 из 14 | 12 px | — |
| `/webapp/dues` @320 | 414 px | 0 px | — | 0 из 0 | 13 px | — |
| `/webapp` @320 | 863 px | 0 px | — | 6 из 15 | 14 px | — |
| `/webapp/slots` @320 | 344 px | 3 px | — | 4 из 7 | 12 px | — |
| `/webapp/notifications` @320 | 501 px | 0 px | — | 1 из 4 | 12 px | — |
| `/webapp/broadcast` @320 | 498 px | 0 px | 1 | 4 из 6 | 13 px | — |
| `/webapp/feedback` @320 | 528 px | 0 px | — | 0 из 6 | 13 px | — |
| `/webapp/reconcile` @320 | 382 px | 0 px | — | 0 из 2 | 12 px | — |
| `/webapp/admin/t/people` @320 | 560 px | 0 px | 2 | 18 из 23 | 13 px | — |
| `/webapp/admin/t/budget` @320 | 357 px | 0 px | 1 | 3 из 8 | 13 px | — |
| `/webapp/admin/t/audit` @320 | 239 px | 0 px | 1 | 3 из 8 | 13 px | — |
| `/webapp/ui` @320 | 1672 px | 0 px | — | 2 из 14 | 11 px | 📅 💸 |

## Как читать

| Столбец | Порог | Что значит превышение |
|---|---|---|
| Переполнение | 0 px | страница шире экрана — запрещено требованием заказчика |
| Гориз. скролл | 0 контейнеров | лента вкладок или таблица прокручивается вбок внутри экрана |
| Цели < 44 px | 0 | палец в дороге промахивается |
| Мелкий шрифт | ≥ 13 px | читается с трудом, особенно на 320 px |
| Эмодзи | 0 | запрещены требованием заказчика и правилом 83 §4.9 |

## После переработки (31.10.2026)

Замеры тем же скриптом по новой навигации (`docs/development/92`). Сравнение
с таблицей «до» — в `docs/development/90-ui-review.md` §2.

| Экран | Высота | Переполнение | Гориз. скролл | Цели < 44 px | Мелкий шрифт | Эмодзи |
|---|---|---|---|---|---|---|
| `/webapp/home` @390 | 912 px | 0 px | — | 0 из 9 | 13 px | — |
| `/webapp/week` @390 | 1037 px | 0 px | — | 0 из 19 | 13 px | — |
| `/webapp/week/summary` @390 | 780 px | 0 px | — | 0 из 16 | 13 px | — |
| `/webapp/money` @390 | 475 px | 0 px | — | 0 из 8 | 13 px | — |
| `/webapp/more` @390 | 821 px | 0 px | — | 0 из 13 | 13 px | — |
| `/webapp/tx` @390 | 707 px | 0 px | — | 0 из 13 | 13 px | — |
| `/webapp/dues` @390 | 502 px | 0 px | — | 0 из 1 | 13 px | — |
| `/webapp` @390 | 1010 px | 0 px | — | 0 из 10 | 13 px | — |
| `/webapp/slots` @390 | 650 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/notifications` @390 | 612 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/broadcast` @390 | 526 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/feedback` @390 | 736 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/reconcile` @390 | 232 px | 0 px | — | 0 из 2 | 13 px | — |
| `/webapp/services` @390 | 770 px | 0 px | — | 0 из 2 | 13 px | — |
| `/webapp/admin/t/people` @390 | 379 px | 0 px | — | 0 из 4 | 13 px | — |
| `/webapp/admin/t/pending` @390 | 259 px | 0 px | — | 0 из 3 | 13 px | — |
| `/webapp/admin/t/audit` @390 | 233 px | 0 px | — | 0 из 1 | 13 px | — |
| `/webapp/admin/t/feedback` @390 | 249 px | 0 px | — | 0 из 1 | 13 px | — |
| `/webapp/ui` @390 | 3531 px | 0 px | — | 0 из 30 | 13 px | — |
| `/webapp/home` @320 | 955 px | 0 px | — | 0 из 9 | 13 px | — |
| `/webapp/week` @320 | 1084 px | 0 px | — | 0 из 19 | 13 px | — |
| `/webapp/week/summary` @320 | 780 px | 0 px | — | 0 из 16 | 13 px | — |
| `/webapp/money` @320 | 494 px | 0 px | — | 0 из 8 | 13 px | — |
| `/webapp/more` @320 | 821 px | 0 px | — | 0 из 13 | 13 px | — |
| `/webapp/tx` @320 | 707 px | 0 px | — | 0 из 13 | 13 px | — |
| `/webapp/dues` @320 | 544 px | 0 px | — | 0 из 1 | 13 px | — |
| `/webapp` @320 | 1029 px | 0 px | — | 0 из 10 | 13 px | — |
| `/webapp/slots` @320 | 688 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/notifications` @320 | 692 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/broadcast` @320 | 578 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/feedback` @320 | 736 px | 0 px | — | 0 из 7 | 13 px | — |
| `/webapp/reconcile` @320 | 232 px | 0 px | — | 0 из 2 | 13 px | — |
| `/webapp/services` @320 | 845 px | 0 px | — | 0 из 2 | 13 px | — |
| `/webapp/admin/t/people` @320 | 379 px | 0 px | — | 0 из 4 | 13 px | — |
| `/webapp/admin/t/pending` @320 | 259 px | 0 px | — | 0 из 3 | 13 px | — |
| `/webapp/admin/t/audit` @320 | 233 px | 0 px | — | 0 из 1 | 13 px | — |
| `/webapp/admin/t/feedback` @320 | 249 px | 0 px | — | 0 из 1 | 13 px | — |
| `/webapp/ui` @320 | 3800 px | 0 px | — | 0 из 30 | 13 px | — |

### Итог сравнения

| Показатель | До | После |
|---|---|---|
| Экранов с горизонтальным скроллом | 4 (админка: лента вкладок + таблица; широкие таблицы) | **0** |
| Экранов с текстом мельче 13 px | 12 из 16 | **0** |
| Целей меньше 44 px | 62 на шести экранах (форма операции, админка, анкета) | **0** |
| Экранов с эмодзи | 2 (хаб, справочник) | **0** |
| Таблиц на мобильном | 3 (участники, взносы, бюджет) | **0** |

Проверка: `tests/webapp/test_miniapp_system.py` — те же четыре правила на каждом
экране в двух ширинах и двух темах; повторяемости — `scripts/ux_audit.py`.

Экраны файлов (`/webapp/files`, `/webapp/folder`) переверстаны 31.10.2026 и входят
в замеры: 390 и 320 px, светлая и тёмная схема — переполнения 0, минимальный шрифт
13 px, цели ≥ 44 px, эмодзи 0, ошибок JS 0. Снимки — `92-files-*.png`,
`92-folder-*.png`, прототип — `94-proto-*.png`.
