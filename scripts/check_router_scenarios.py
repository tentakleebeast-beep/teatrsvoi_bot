#!/usr/bin/env python3
"""
«Золотой набор» фраз для маршрутизатора свободного текста (docs/infrastructure/29 §7).

Раньше сценарии гонялись через вебхук n8n; с V-026 (01.11.2026) n8n выведен из
контура, и роутер живёт в tool-api: `POST /tools/route`. Набор фраз и ожидаемые
сценарии не изменились — иначе сравнивать поведение было бы не с чем.

Проверяет, что классификация не разъехалась после правки промпта.
  python scripts/check_router_scenarios.py            # прогнать все фразы
  python scripts/check_router_scenarios.py --quiet    # только итог
Код выхода 1, если хоть одна фраза ушла не туда — можно ставить в CI.
Код выхода 2, если tool-api недоступен или отверг токен.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from _cli import api_url, apply_env, env, parser

apply_env()

API = api_url()
TOKEN = env("TOOL_API_TOKEN")
ADMIN_CHAT = env("ADMIN_CHAT_ID")
CATEGORIES = ["Аренда помещения", "Реквизит и костюмы", "Коммунальные платежи",
              "Гонорары и доли", "Реклама и полиграфия", "Продажа билетов", "Прочее"]

# (фраза, ожидаемый интент, ожидаемая категория | None)
CASES: list[tuple[str, str, str | None]] = [
    ("расход 3000 на реквизит", "budget", "Реквизит и костюмы"),
    ("купили ткань на костюмы 12000", "budget", "Реквизит и костюмы"),
    ("оплатили аренду 45000", "budget", "Аренда помещения"),
    ("заплатили за свет 7000", "budget", "Коммунальные платежи"),
    ("отдали Крылову 21% со сборов", "budget", "Гонорары и доли"),
    ("напечатали афиши за 5000", "budget", "Реклама и полиграфия"),
    ("продали билетов на 68080", "budget", "Продажа билетов"),
    ("расход 500", "budget", None),
    ("занят в среду после 18", "schedule", None),
    ("свободен 28.10 с 10 до 14", "schedule", None),
    ("не могу в пятницу вечером", "schedule", None),
    ("когда могут все на репетицию", "schedule", None),
    ("сколько я должен по взносам", "dues", None),
    ("мои взносы за октябрь", "dues", None),
    ("кто я", "assistant", None),
    ("что ты умеешь", "assistant", None),
    ("как записать доход", "assistant", None),
    ("где посмотреть расписание", "assistant", None),
    ("привет", "assistant", None),
    ("спасибо", "assistant", None),
]


def ask(text: str) -> dict:
    body = json.dumps({"text": text,
                       "user": {"name": "Проверка", "roles": ["администратор"]},
                       "categories": CATEGORIES}).encode()
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {TOKEN}"}
    if ADMIN_CHAT:
        headers["X-Actor-Chat-Id"] = str(ADMIN_CHAT)
    req = urllib.request.Request(f"{API}/tools/route", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    ap = parser(__doc__ or "Проверка разбора фраз маршрутизатором tool-api")
    ap.add_argument("--quiet", action="store_true", help="показывать только итог")
    quiet = ap.parse_args().quiet
    failures = []

    if not TOKEN or not ADMIN_CHAT:
        print("нет TOOL_API_TOKEN или ADMIN_CHAT_ID в .env — роутер не проверить")
        sys.exit(2)

    try:
        probe = ask("проверка связи")
    except urllib.error.HTTPError as exc:
        print(f"tool-api отверг запрос ({exc.code}): "
              f"{exc.read().decode(errors='replace')[:200]}")
        sys.exit(2)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"tool-api недоступен ({API}): {exc}")
        sys.exit(2)

    # ok:false — это не «фраза ушла не туда», а «роутер не может работать»:
    # нет ключа модели, квота или провайдер лежит. Иначе 20 красных строк
    # прячут настоящую причину.
    if probe.get("ok") is False:
        print(f"роутер не может работать: {probe.get('error')} "
              f"({probe.get('detail') or 'без подробностей'})")
        sys.exit(2)

    for text, expected_intent, expected_category in CASES:
        try:
            result = ask(text)
        except Exception as exc:                        # noqa: BLE001
            failures.append((text, "ошибка запроса", str(exc)[:60], expected_intent))
            continue

        intent = result.get("intent") or result.get("scenario")
        category = result.get("category")
        ok = intent == expected_intent
        if expected_category and category != expected_category:
            ok = False
        if not ok:
            failures.append((text, intent, category, expected_intent))
        if not quiet:
            mark = "OK  " if ok else "FAIL"
            print(f"{mark} {text[:38]:<40} intent={intent} category={category}")

    print()
    if failures:
        print(f"провалено {len(failures)} из {len(CASES)}:")
        for text, got_intent, got_cat, expected in failures:
            print(f"  «{text}» → intent={got_intent}, category={got_cat}; ожидался {expected}")
        sys.exit(1)
    print(f"все {len(CASES)} фраз разобраны верно")


if __name__ == "__main__":
    main()
