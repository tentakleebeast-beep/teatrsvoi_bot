#!/usr/bin/env python3
"""Слой А дизайн-ревью Mini App: 31 экран, четыре роли, две схемы, две ширины.

Зачем отдельный скрипт, а не `scripts/ux_audit.py`: замер 31.10.2026 (отчёт 95)
обходил 20 экранов из 30 и не знал тёмной схемы, контраст проверялся на 5 экранах.
Здесь измерение идёт по списку экранов из кода и по четырём ролям с живыми данными
стенда (docs/development/141 §4.1, §5).

Что мерится на каждом экране, каждой ширине и каждой схеме (А1–А9):

    А1 переполнение по горизонтали     documentElement.scrollWidth - clientWidth
    А2 контейнеры со своим скроллом    overflow-x: auto/scroll и scrollWidth > clientWidth
    А3 минимальный шрифт               по видимым листовым узлам, порог 13 px
    А4 цели нажатия                    a, button, input, select, textarea — от 44×44 px
    А5 эмодзи в видимом тексте         регулярка по innerText
    А6 ошибки JS                       слушатель pageerror
    А7 контраст текста к фону          вычисленные цвета, 4.5 / 3.0 для крупного
    А8 модальные alert/confirm/prompt  перехват на старте страницы
    А9 главных кнопок на экране        счётчик, в pass не входит

А10 (семь редиректов: 301 и цель открывается) проверяется отдельно, без браузера.

Список экранов берётся из кода: маршруты-страницы `app/pages/routes.py` (21 штука)
плюс записи с полем `page` в реестре `app/webapp.py:SECTIONS` (10 разделов) —
ровно те же 31 адрес, что отдаёт стенд `tests/webapp/server.py:PAGE_ROUTES`.
Сверка списков — `--verify-inventory` (код возврата 1 при расхождении).

Вход на стенд: `POST /webapp/auth` с initData, подписанным токеном dev-бота, —
тот же путь, что в бою (docs/development/141 §5.2). Токены четырёх ролей либо
передаются готовыми (`--tokens`), либо выпускаются по chat_id (`--env-file`).

Ничего не нажимает и не отправляет: скрипт открывает экран, ждёт загрузки данных,
снимает мерки и (по флагу `--shots`) сохраняет кадр. Кнопки «Отправить», «Принять»,
«Начислить», «Выпустить код», «Применить» не нажимаются никогда (141 §5.3).

    env -u PYTHONPATH .venv/bin/python scripts/ui_review.py --help
    env -u PYTHONPATH .venv/bin/python scripts/ui_review.py --verify-inventory
    env -u PYTHONPATH .venv/bin/python scripts/ui_review.py \\
        --base-url http://127.0.0.1:8010 --env-file .env.ui-review \\
        --chat-ids admin=250364739,org=555,troupe=999,master=900000001 \\
        --out /tmp/layer-a.json --markdown /tmp/layer-a.md
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_API = ROOT / "services" / "tool-api"
ROUTES_SOURCE = TOOL_API / "app" / "pages" / "routes.py"
STAND_SOURCE = ROOT / "tests" / "webapp" / "server.py"

ROLES = ("admin", "org", "master", "troupe")
DEFAULT_WIDTHS = (390, 320)
DEFAULT_SCHEMES = ("light", "dark")
WIDE_WIDTH = 430

# Номер и короткое имя экрана — из чек-листа 141 §2 (та же нумерация в отчёте).
# Сам список экранов берётся из кода (`build_inventory`), здесь только подписи:
# без общего номера отчёт и снимки `NNN-*.png` не сходятся между собой.
SCREEN_LABELS: dict[str, tuple[int, str, str]] = {
    "/webapp/home": (1, "home", "Главная"),
    "/webapp/week": (2, "week", "Время"),
    "/webapp/money": (3, "money", "Деньги"),
    "/webapp/more": (4, "more", "Ещё / Управление"),
    "/webapp/week/summary": (5, "week-summary", "Сводка занятости"),
    "/webapp/week/month": (6, "week-month", "Сетка месяца"),
    "/webapp/slots": (7, "slots", "Поиск окон"),
    "/webapp/tx": (8, "tx", "Операция"),
    "/webapp/dues": (9, "dues", "Мои взносы"),
    "/webapp/reconcile": (10, "reconcile", "Сверка листов"),
    "/webapp/broadcast": (11, "broadcast", "Рассылка"),
    "/webapp": (12, "profile", "Анкета участника"),
    "/webapp/notifications": (13, "notifications", "Уведомления"),
    "/webapp/feedback": (14, "feedback", "Обратная связь"),
    "/webapp/notes": (15, "notes", "Заметки"),
    "/webapp/files": (16, "files", "Файлы студии"),
    "/webapp/folder": (17, "folder", "Папка"),
    "/webapp/file": (31, "file", "Файл"),
    "/webapp/person": (18, "person", "Карточка участника"),
    "/webapp/services": (19, "services", "Сервисы"),
    "/webapp/ui": (20, "ui", "Справочник компонентов"),
    "/webapp/admin/t/pending": (21, "admin-t-pending", "Заявки"),
    "/webapp/admin/t/people": (22, "admin-t-people", "Люди"),
    "/webapp/admin/t/dues": (23, "admin-t-dues", "Взносы (админ)"),
    "/webapp/admin/t/invites": (24, "admin-t-invites", "Приглашения"),
    "/webapp/admin/t/rights": (25, "admin-t-rights", "Права"),
    "/webapp/admin/t/archive": (26, "admin-t-archive", "Архив"),
    "/webapp/admin/t/feedback": (27, "admin-t-feedback", "Обращения"),
    "/webapp/admin/t/audit": (28, "admin-t-audit", "Аудит"),
    "/webapp/admin/t/agent": (29, "admin-t-agent", "Разбор агента"),
    "/webapp/admin/t/notifications": (30, "admin-t-notifications", "Уведомления (общие)"),
}

# Редиректы: старый адрес → новый, критерий А10 «301 и цель открывается».
REDIRECTS = {
    "/webapp/admin": "/webapp/admin/t/people",
    "/webapp/days": "/webapp/week",
    "/webapp/my/sheet": "/webapp/week",
    "/webapp/schedule": "/webapp/week/summary",
    "/webapp/search": "/webapp/admin/t/people",
    "/webapp/people": "/webapp/admin/t/people",
    "/webapp/assign": "/webapp/slots",
}

# Пороги 141 §4.1 — не пересматриваются этим скриптом.
MAX_OVERFLOW_PX = 1
MIN_FONT_PX = 13
MIN_TARGET_PX = 44
CONTRAST_NORMAL = 4.5
CONTRAST_LARGE = 3.0          # крупный текст: 18.66 px и больше или 600+ от 14 px (в MEASURE)

# Регулярка эмодзи живёт в MEASURE (JS): питону она не нужна.
# Слова, которыми экран объясняет отсутствие права (принцип Б3). Ищем именно текст,
# а не код ответа: «403» в интерфейсе — это дефект, «нет права» — норма.
NO_RIGHT_TEXTS = (
    "нет права", "нет прав", "прав нет", "нет доступа", "недоступн", "не доступн",
    "не хватает права", "недостаточно прав", "только для админ", "вам не открыт",
)
ERROR_TEXTS = ("не удалось", "ошибка", "ошибк", "попробуйте", "не получилось")
EMPTY_TEXTS = ("пока пусто", "пока нет", "пока ничего", "ничего нет", "нет данных",
               "выберите", "пусто")

MODAL_INIT_SCRIPT = """
window.__modalCalls = [];
['alert', 'confirm', 'prompt'].forEach(function (name) {
  var original = window[name];
  window[name] = function (message) {
    try { window.__modalCalls.push(name + ': ' + String(message || '').slice(0, 120)); }
    catch (e) { window.__modalCalls.push(name); }
    if (name === 'confirm') { return false; }
    return undefined;
  };
  window[name].__svoi_wrapped = true;
  return original;
});
"""

MEASURE = r"""
() => {
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
    return el.getClientRects().length > 0;
  };
  const selectorOf = el => {
    const id = el.id ? '#' + el.id : '';
    const cls = (typeof el.className === 'string' && el.className.trim())
      ? '.' + el.className.trim().split(/\s+/).join('.') : '';
    const base = el.tagName.toLowerCase() + id + cls;
    return base.slice(0, 80);
  };
  const all = [...document.querySelectorAll('body *')];

  let minFont = 99, minFontWhere = '';
  all.forEach(el => {
    if (!(el.innerText || '').trim() || el.children.length || !visible(el)) return;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (size < minFont) { minFont = size; minFontWhere = selectorOf(el); }
  });

  const parseColor = text => {
    const m = /^rgba?\(([^)]+)\)$/.exec(String(text || '').trim());
    if (!m) return null;
    const parts = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    if (parts.length < 3 || parts.some(Number.isNaN)) return null;
    return {r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1};
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const channel = v => {
    v = v / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  const luminance = c => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const l1 = luminance(a), l2 = luminance(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const effectiveBg = el => {
    const layers = [];
    let node = el;
    while (node && node !== document.documentElement) {
      const s = getComputedStyle(node);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const c = parseColor(s.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
      node = node.parentElement;
    }
    let bg = parseColor(getComputedStyle(document.body).backgroundColor);
    if (!bg || bg.a < 1) bg = {r: 255, g: 255, b: 255, a: 1};
    for (let i = layers.length - 1; i >= 0; i -= 1) bg = over(layers[i], bg);
    return bg;
  };

  const violations = [];
  let minContrast = 99;
  all.forEach(el => {
    if (!visible(el)) return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && (n.textContent || '').trim());
    if (!own) return;
    const s = getComputedStyle(el);
    const fg = parseColor(s.color);
    if (!fg || fg.a === 0) return;
    const bg = effectiveBg(el);
    if (!bg) return;
    const color = fg.a < 1 ? over(fg, bg) : fg;
    const size = parseFloat(s.fontSize);
    const weight = parseInt(s.fontWeight, 10) || 400;
    const large = size >= 18.66 || (weight >= 600 && size >= 14);
    const need = large ? 3.0 : 4.5;
    const value = ratio(color, bg);
    if (value < minContrast) minContrast = value;
    if (value < need) {
      violations.push({
        selector: selectorOf(el),
        text: (el.innerText || '').trim().slice(0, 60),
        fg: s.color, bg: 'rgb(' + [bg.r, bg.g, bg.b].map(v => Math.round(v)).join(', ') + ')',
        ratio: Math.round(value * 100) / 100,
        need: need, font_px: size,
      });
    }
  });

  // label не цель: это подпись поля, а не кнопка.
  const small = [...document.querySelectorAll('a,button,input,select,textarea')]
    .filter(el => visible(el) && !(el.closest('label')))
    .map(el => {
      const r = el.getBoundingClientRect();
      return {tag: el.tagName.toLowerCase(), selector: selectorOf(el),
              text: (el.innerText || el.id || '').trim().slice(0, 32),
              w: Math.round(r.width), h: Math.round(r.height)};
    })
    .filter(t => t.h > 0 && (t.h < 44 || t.w < 44));

  const scroll = all.filter(el => {
    const s = getComputedStyle(el);
    return (s.overflowX === 'auto' || s.overflowX === 'scroll')
      && el.scrollWidth > el.clientWidth + 1;
  }).map(el => selectorOf(el));

  const emoji = [...new Set((document.body.innerText || '').match(
    /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu) || [])];

  // «Есть данные» против «пусто»: считаются строки данных, а не длина текста,
  // иначе подписи каркаса принимаются за содержимое.
  const rows = [...document.querySelectorAll(
    'li, tr, article, [class*="row"], [class*="card"], [class*="item"]')]
    .filter(el => visible(el) && (el.innerText || '').trim().length > 3).length;

  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    scroll: [...new Set(scroll)].slice(0, 10),
    emoji: emoji,
    minFont: Math.round(minFont * 100) / 100,
    minFontWhere,
    small: small.slice(0, 20),
    minContrast: minContrast === 99 ? null : Math.round(minContrast * 100) / 100,
    violations: violations.sort((a, b) => a.ratio - b.ratio).slice(0, 25),
    violationCount: violations.length,
    primaryButtons: document.querySelectorAll('.ui-btn--primary').length,
    rows: rows,
    bodyText: (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 1200),
    modalCalls: (window.__modalCalls || []).slice(),
  };
}
"""


# --- инвентарь из кода ---------------------------------------------------------

def inventory_from_code() -> dict[str, str]:
    """31 экран: маршруты-страницы `routes.py` + разделы реестра `SECTIONS`."""
    import sys as _sys

    for path in (str(ROOT), str(TOOL_API)):
        if path not in _sys.path:
            _sys.path.insert(0, path)
    from app.webapp import SECTIONS  # noqa: PLC0415

    tree = ast.parse(ROUTES_SOURCE.read_text(encoding="utf-8"))
    screens: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr != "get" or not dec.args:
                continue
            arg = dec.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                continue
            path = arg.value
            # HTML-страница — только там, где явно просят HTMLResponse: у редиректов
            # и у `/webapp/nav` этот аргумент другой.
            is_html = any(
                (kw.arg == "response_class" and isinstance(kw.value, ast.Name)
                 and kw.value.id == "HTMLResponse") for kw in dec.keywords
            )
            if is_html:
                screens[path] = f"routes.py:{node.name}"
    for section in SECTIONS:
        if section.page:
            screens[section.url] = f"webapp.py:SECTIONS[{section.key}]"
    return screens


def stand_inventory() -> dict[str, str]:
    """Список экранов стенда — `PAGE_ROUTES` из `tests/webapp/server.py`."""
    source = STAND_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PAGE_ROUTES" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                return {
                    key.value: "PAGE_ROUTES"
                    for key in node.value.keys
                    if isinstance(key, ast.Constant)
                }
    return {}


def screen_slug(path: str) -> str:
    label = SCREEN_LABELS.get(path)
    if label:
        return label[1]
    return path.strip("/").replace("/", "-").replace("webapp-", "") or "index"


def build_inventory() -> list[str]:
    """Список 31 экрана в порядке чек-листа 141 §2."""
    screens = inventory_from_code()
    order = sorted(screens, key=lambda p: (SCREEN_LABELS.get(p, (999, "", ""))[0], p))
    return order


def verify_inventory() -> int:
    code = inventory_from_code()
    stand = stand_inventory()
    problems = []
    only_code = sorted(set(code) - set(stand))
    only_stand = sorted(set(stand) - set(code))
    if only_code:
        problems.append("есть в коде, нет на стенде: " + ", ".join(only_code))
    if only_stand:
        problems.append("есть на стенде, нет в коде: " + ", ".join(only_stand))
    missing_labels = sorted(set(code) - set(SCREEN_LABELS))
    if missing_labels:
        problems.append("нет номера в чек-листе: " + ", ".join(missing_labels))
    print(f"код: {len(code)} экранов, стенд: {len(stand)}, чек-лист: {len(SCREEN_LABELS)}")
    for problem in problems:
        print("расхождение — " + problem)
    return 1 if problems else 0


# --- вход на стенд -------------------------------------------------------------

def mint_init_data(chat_id: int, bot_token: str, now: int | None = None) -> str:
    """initData, подписанный токеном бота: тот же путь, что даёт Telegram (141 §5.2)."""
    auth_date = int(now if now is not None else time.time())
    pairs = {
        "auth_date": str(auth_date),
        "query_id": "AA" + str(chat_id),
        "user": json.dumps({"id": chat_id, "first_name": "Ревью", "username": "ui_review"},
                           ensure_ascii=False, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(sorted(pairs.items())) + "&hash=" + digest


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:  # сервер ответил, а не сеть
        body = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"detail": body[:200]}


def issue_tokens(base_url: str, bot_token: str, chat_ids: dict[str, int]) -> dict[str, str]:
    tokens = {}
    for role, chat_id in chat_ids.items():
        init_data = mint_init_data(chat_id, bot_token)
        status, body = post_json(base_url + "/webapp/auth", {"init_data": init_data})
        if status != 200 or not body.get("token"):
            raise SystemExit(f"{role}: вход не удался, {status} {body}")
        tokens[role] = body["token"]
        print(f"вход {role}: участник «{body['participant']['name']}»")
    return tokens


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split(" #")[0].strip().strip('"').strip("'")
    return values


def parse_pairs(text: str, name: str) -> dict[str, str]:
    result = {}
    for chunk in (text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"{name}: ожидается вид ключ=значение, получено {chunk!r}")
        key, _, value = chunk.partition("=")
        result[key.strip()] = value.strip()
    return result


# --- измерения ----------------------------------------------------------------

def classify_role_state(body_text: str, rows: int) -> str:
    """Как открылся экран для роли — слово для ролевой матрицы 141 §7 п. 4."""
    low = (body_text or "").lower()
    if "403" in low or "forbidden" in low:
        return "403"
    if any(text in low for text in NO_RIGHT_TEXTS):
        return "нет права словами"
    if any(text in low for text in ERROR_TEXTS) and rows == 0:
        return "ошибка"
    if rows > 0:
        return "есть"
    if any(text in low for text in EMPTY_TEXTS):
        return "пусто"
    return "пусто"


def _get_status(base_url: str, path: str) -> tuple[int | None, str]:
    """GET без следования редиректу: статус и `Location` (А10).

    `urllib` с обработчиком-заглушкой пришлось бы переопределять методом, а он
    выглядит мёртвым кодом для `check_deadcode`; `http.client` того же достигает
    явно и без подмены поведения библиотеки.
    """
    parts = urllib.parse.urlsplit(base_url)
    connection = http.client.HTTPConnection(parts.hostname or "127.0.0.1",
                                            parts.port or 80, timeout=20)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.getheader("Location") or ""
    except OSError as exc:
        return None, str(exc)[:120]
    finally:
        connection.close()


def check_redirects(base_url: str) -> list[dict]:
    """А10: 301 без следования и 200 по цели."""
    results = []
    for source, target in REDIRECTS.items():
        status, location = _get_status(base_url, source)
        target_status, _ = _get_status(base_url, target)
        results.append({
            "from": source, "to": target, "status": status, "location": location,
            "target_status": target_status,
            "pass": status == 301 and target_status == 200,
        })
    return results


def _measure_one(browser, *, base_url: str, token: str, role: str, path: str,
                 width: int, scheme: str) -> dict:
    context = browser.new_context(viewport={"width": width, "height": 844},
                                  color_scheme=scheme, device_scale_factor=1)
    context.add_init_script(MODAL_INIT_SCRIPT)
    page = context.new_page()
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console.append(message.text)
            if message.type == "error" else None)
    url = f"{base_url}{path}?dev_token={urllib.parse.quote(token)}"
    status = None
    try:
        response = page.goto(url, wait_until="load", timeout=30000)
        status = response.status if response is not None else None
        try:
            page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:  # noqa: BLE001 — долгие опросы не должны валить замер
            pass
        page.wait_for_timeout(300)
        metrics = page.evaluate(MEASURE)
    except Exception as exc:  # noqa: BLE001 — «не открылось» это факт, а не падение
        metrics = {"bodyText": "", "rows": 0, "scroll": [], "emoji": [], "minFont": 0,
                   "minFontWhere": "", "small": [], "overflow": 0, "violations": [],
                   "minContrast": None, "violationCount": 0, "primaryButtons": 0,
                   "modalCalls": []}
        errors = errors + [f"навигация: {exc}"]

    record = {
        "role": role,
        "screen": path,
        "screen_no": SCREEN_LABELS.get(path, (0, "", ""))[0],
        "slug": screen_slug(path),
        "width": width,
        "scheme": scheme,
        "status": status,
        "overflow_px": metrics["overflow"],
        "overflow_containers": metrics["scroll"],
        "min_font_px": metrics["minFont"],
        "min_font_where": metrics["minFontWhere"],
        "small_targets": metrics["small"],
        "emoji": metrics["emoji"],
        "page_errors": errors,
        "console_errors": console[:10],
        "modal_calls": len(metrics["modalCalls"]),
        "modal_log": metrics["modalCalls"],
        "primary_buttons": metrics["primaryButtons"],
        "min_contrast": metrics["minContrast"],
        "contrast_violations": metrics["violations"],
        "contrast_violation_count": metrics["violationCount"],
        "rows": metrics["rows"],
        "role_state": classify_role_state(metrics["bodyText"], metrics["rows"]),
        "text_excerpt": metrics["bodyText"][:400],
    }
    record["checks"] = {
        "A1_overflow": record["overflow_px"] <= MAX_OVERFLOW_PX,
        "A2_containers": not record["overflow_containers"],
        "A3_font": record["min_font_px"] >= MIN_FONT_PX,
        "A4_targets": not record["small_targets"],
        "A5_emoji": not record["emoji"],
        "A6_page_errors": not record["page_errors"],
        "A7_contrast": not record["contrast_violations"],
        "A8_modals": record["modal_calls"] == 0,
    }
    # А9 (число главных кнопок) в pass не входит: у старых экранов их может быть две.
    record["pass"] = all(record["checks"].values())
    context.close()
    return record


def launch(args):
    """Запустить браузер: возвращает (playwright, browser), чтобы было чем закрыть."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    playwright = sync_playwright().start()
    browser = getattr(playwright, args.engine).launch()
    return playwright, browser


def take_shots(browser, args) -> list[str]:
    """Снимки экранов: по одному на роль × экран × ширину × схему."""
    saved = []
    directory = Path(args.shots)
    directory.mkdir(parents=True, exist_ok=True)
    for role in args.role_list:
        token = args.tokens[role]
        for path in args.screen_list:
            for width in args.width_list:
                for scheme in args.scheme_list:
                    context = browser.new_context(
                        viewport={"width": width, "height": 844},
                        color_scheme=scheme, device_scale_factor=2)
                    context.add_init_script(MODAL_INIT_SCRIPT)
                    page = context.new_page()
                    url = f"{args.base_url}{path}?dev_token={urllib.parse.quote(token)}"
                    try:
                        page.goto(url, wait_until="load", timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=1500)
                        except Exception:  # noqa: BLE001
                            pass
                        page.wait_for_timeout(300)
                        name = f"{role}-{screen_slug(path)}-{width}-{scheme}.png"
                        target = directory / name
                        page.screenshot(path=str(target), full_page=True)
                        saved.append(str(target))
                    except Exception as exc:  # noqa: BLE001
                        print(f"снимок не снят: {path} {role} {width} {scheme}: {exc}")
                    finally:
                        context.close()
    return saved


# --- сводка -------------------------------------------------------------------

def summarize(records: list[dict]) -> dict:
    failed = [r for r in records if not r["pass"]]
    by_criterion = {}
    for criterion in ("A1_overflow", "A2_containers", "A3_font", "A4_targets",
                      "A5_emoji", "A6_page_errors", "A7_contrast", "A8_modals"):
        by_criterion[criterion] = sum(1 for r in records if not r["checks"][criterion])
    return {
        "combinations": len(records),
        "failed": len(failed),
        "by_criterion": by_criterion,
        "screens": len({r["screen"] for r in records}),
        "roles": len({r["role"] for r in records}),
    }


def matrix(records: list[dict]) -> dict[str, dict[str, str]]:
    """Ролевая матрица: экран → роль → как открылось (худшее из ширин и схем)."""
    severity = {"не открылось": 4, "403": 3, "ошибка": 2, "нет права словами": 1,
                "пусто": 0, "есть": -1}
    table: dict[str, dict[str, str]] = {}
    for record in records:
        state = record["role_state"] if record["status"] not in (None, 200) \
            else ("не открылось" if record["status"] is None else record["role_state"])
        cell = table.setdefault(record["screen"], {})
        current = cell.get(record["role"])
        if current is None or severity.get(state, 0) > severity.get(current, 0):
            cell[record["role"]] = state
    return table


def markdown_report(payload: dict) -> str:
    lines = ["# Слой А: измерение экранов Mini App", ""]
    summary = payload["summary"]
    lines.append(f"Комбинаций: {summary['combinations']}, провалов: {summary['failed']}, "
                 f"экранов: {summary['screens']}, ролей: {summary['roles']}.")
    lines.append("")
    lines.append("| Критерий | Провалов |")
    lines.append("|---|---|")
    for criterion, count in summary["by_criterion"].items():
        lines.append(f"| {criterion} | {count} |")
    lines.append("")
    lines.append("| № | Экран | Адрес | Проверок | Провалов | Мин. шрифт | Мин. контраст "
                 "| Главных кнопок |")
    lines.append("|---|---|---|---|---|---|---|---|")
    by_screen: dict[str, list[dict]] = {}
    for record in payload["records"]:
        by_screen.setdefault(record["screen"], []).append(record)
    for path in sorted(by_screen, key=lambda p: SCREEN_LABELS.get(p, (999, "", ""))[0]):
        items = by_screen[path]
        number, _slug, title = SCREEN_LABELS.get(path, (0, "", path))
        fonts = [i["min_font_px"] for i in items if i["min_font_px"]]
        contrasts = [i["min_contrast"] for i in items if i["min_contrast"] is not None]
        primary = max((i["primary_buttons"] for i in items), default=0)
        lines.append(
            f"| {number} | {title} | `{path}` | {len(items)} | "
            f"{sum(1 for i in items if not i['pass'])} | {min(fonts) if fonts else '—'} | "
            f"{min(contrasts) if contrasts else '—'} | {primary} |")
    lines.append("")
    lines.append("Редиректы (А10):")
    lines.append("")
    lines.append("| Откуда | Куда | Статус | Цель |")
    lines.append("|---|---|---|---|")
    for item in payload["redirects"]:
        lines.append(f"| `{item['from']}` | `{item['to']}` | {item['status']} | "
                     f"{item['target_status']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Слой А дизайн-ревью Mini App: 31 экран, 4 роли, 2 схемы, 2 ширины.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010",
                        help="адрес стенда tool-api (по умолчанию http://127.0.0.1:8010)")
    parser.add_argument("--out", help="куда записать JSON с мерками")
    parser.add_argument("--markdown", help="куда записать Markdown-таблицу")
    parser.add_argument("--tokens", help="готовые токены: admin=…,org=…,master=…,troupe=…")
    parser.add_argument("--tokens-file", help="файл с токенами (по строке «роль=токен»)")
    parser.add_argument("--env-file", help="файл среды стенда: из него берётся DEV_BOT_TOKEN")
    parser.add_argument("--bot-token", help="токен dev-бота для выпуска токенов ролей")
    parser.add_argument("--chat-ids", help="chat_id ролей: admin=…,org=…,master=…,troupe=…")
    parser.add_argument("--roles", help="роли через запятую (по умолчанию все четыре)")
    parser.add_argument("--widths", help="ширины через запятую (по умолчанию 390,320)")
    parser.add_argument("--schemes", help="схемы через запятую: light,dark")
    parser.add_argument("--engine", default="chromium", choices=("chromium", "webkit"))
    parser.add_argument("--screens", help="подмножество экранов через запятую (отладка)")
    parser.add_argument("--shots", help="каталог для снимков экранов")
    parser.add_argument("--verify-inventory", action="store_true",
                        help="сверить список экранов из кода со стендом и выйти")
    args = parser.parse_args(argv)

    if len(sys.argv) == 1 and argv is None:
        parser.print_help()
        return 2

    if args.verify_inventory:
        return verify_inventory()

    if not (args.out or args.markdown or args.shots):
        parser.error("нужен хотя бы один из ключей --out, --markdown, --shots")

    inventory = build_inventory()
    allowed = set(inventory)
    if args.screens:
        requested = [p.strip() for p in args.screens.split(",") if p.strip()]
        unknown = [p for p in requested if p not in allowed]
        if unknown:
            raise SystemExit("неизвестные экраны: " + ", ".join(unknown))
        screen_list = requested
    else:
        screen_list = inventory

    args.role_list = [r.strip() for r in (args.roles or ",".join(ROLES)).split(",") if r.strip()]
    args.width_list = [int(w) for w in (args.widths or
                                        ",".join(str(w) for w in DEFAULT_WIDTHS)).split(",")]
    args.scheme_list = [s.strip() for s in
                        (args.schemes or ",".join(DEFAULT_SCHEMES)).split(",") if s.strip()]
    args.screen_list = screen_list

    tokens: dict[str, str] = {}
    if args.tokens or args.tokens_file:
        if args.tokens:
            tokens.update(parse_pairs(args.tokens, "--tokens"))
        if args.tokens_file:
            path = Path(args.tokens_file)
            pairs = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    pairs[key.strip()] = value.strip()
            tokens.update(pairs)
    else:
        bot_token = args.bot_token or ""
        env_values = {}
        if args.env_file:
            env_values = read_env_file(Path(args.env_file))
            bot_token = bot_token or env_values.get("DEV_BOT_TOKEN", "")
        if not bot_token:
            raise SystemExit("нет токена бота: --bot-token, --env-file или --tokens")
        chat_ids = {role: int(value) for role, value in
                    parse_pairs(args.chat_ids or "", "--chat-ids").items()}
        missing = [role for role in args.role_list if role not in chat_ids]
        if missing:
            raise SystemExit("нет chat_id ролей: " + ", ".join(missing))
        tokens = issue_tokens(args.base_url, bot_token, chat_ids)
    missing_tokens = [role for role in args.role_list if not tokens.get(role)]
    if missing_tokens:
        raise SystemExit("нет токенов ролей: " + ", ".join(missing_tokens))
    args.tokens = tokens

    redirects = check_redirects(args.base_url)
    for item in redirects:
        if not item["pass"]:
            print(f"редирект не в порядке: {item['from']} → {item['status']} "
                  f"{item['to']} {item['target_status']}")

    playwright, browser = launch(args)
    started = time.time()
    try:
        records = []
        total = len(args.role_list) * len(screen_list) * len(args.width_list) \
            * len(args.scheme_list)
        done = 0
        for role in args.role_list:
            token = args.tokens[role]
            for path in screen_list:
                for width in args.width_list:
                    for scheme in args.scheme_list:
                        records.append(_measure_one(browser, base_url=args.base_url,
                                                    token=token, role=role,
                                                    path=path, width=width, scheme=scheme))
                        done += 1
                        if done % 40 == 0:
                            print(f"{done}/{total} замеров, "
                                  f"{time.time() - started:.0f} с", flush=True)
        shots = take_shots(browser, args) if args.shots else []
    finally:
        try:
            browser.close()
            playwright.stop()
        except Exception:  # noqa: BLE001
            pass

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "engine": args.engine,
        "widths": args.width_list,
        "schemes": args.scheme_list,
        "roles": args.role_list,
        "screens": screen_list,
        "thresholds": {
            "overflow_px": MAX_OVERFLOW_PX, "min_font_px": MIN_FONT_PX,
            "min_target_px": MIN_TARGET_PX, "contrast_normal": CONTRAST_NORMAL,
            "contrast_large": CONTRAST_LARGE, "wide_width_px": WIDE_WIDTH,
        },
        "summary": summarize(records),
        "records": records,
        "matrix": matrix(records),
        "redirects": redirects,
        "shots": shots,
    }
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {args.out}")
    if args.markdown:
        Path(args.markdown).write_text(markdown_report(payload), encoding="utf-8")
        print(f"Markdown: {args.markdown}")
    summary = payload["summary"]
    print(f"комбинаций {summary['combinations']}, провалов {summary['failed']}, "
          f"снимков {len(shots)}, время {time.time() - started:.0f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
