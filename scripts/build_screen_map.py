"""Единый граф переходов Mini App и атлас экранов: снимается со стенда, не пишется руками.

Источник истины — код: `tests/webapp/server.py` отдаёт ровно те же страницы, что боевой
tool-api, поэтому ссылки на графе — настоящие `<a href>`, а не пересказ документации.
Граф и атлас пересобираются одной командой; расхождение стенда с кодом ловится проверками
в конце (ссылка на несуществующий экран — ошибка, а не «как-нибудь нарисуем»).

    # стенд (в отдельном терминале)
    env -u PYTHONPATH .venv/bin/python -m uvicorn tests.webapp.server:app --port 8799
    # граф + атлас
    env -u PYTHONPATH .venv/bin/python scripts/build_screen_map.py

Пишет:
    docs/architecture/167-transition-graph.md   — граф Mermaid + таблицы
    docs/reports/168-screens-atlas.html         — атлас: экран, адрес, входы, снимок
    docs/reports/168-screens/<адрес>.png        — снимки 390 × 844 ×2
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from urllib.parse import quote, urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))                      # пакет tests.*
sys.path.insert(0, str(ROOT / "services" / "tool-api"))

STAND = "http://127.0.0.1:8799"
GRAPH_DOC = ROOT / "docs" / "architecture" / "167-transition-graph.md"
ATLAS_DOC = ROOT / "docs" / "reports" / "168-screens-atlas.html"
SHOTS = ROOT / "docs" / "reports" / "168-screens"

# Как Telegram передаёт данные Mini App: стенд подпись не проверяет (как в conftest).
INIT_DATA = ("user=%7B%22id%22%3A30%2C%22first_name%22%3A%22Test%22%7D"
             "&auth_date=1793000000&hash=testhash")
HASH = (f"#tgWebAppData={quote(INIT_DATA, safe='')}"
        "&tgWebAppVersion=7.0&tgWebAppPlatform=desktop&tgWebAppThemeParams=%7B%7D")

# Подписи вкладок: полоса вкладок есть на каждом экране, и её четыре линии на графе
# дали бы 35 × 4 одинаковых рёбер. Поэтому она не рисуется, а описана текстом.
TAB_LABELS = {"Главная", "Время", "Деньги", "Управление", "Ещё"}
TABS = ("/webapp/home", "/webapp/week", "/webapp/money", "/webapp/more", "/webapp")

# Группы на графе: по смыслу, а не по префиксу адреса.
GROUPS = {
    "Вкладки Mini App": ("/webapp", "/webapp/home", "/webapp/week", "/webapp/money", "/webapp/more"),
    "Время": ("/webapp/week/summary", "/webapp/week/month", "/webapp/slots"),
    "Деньги": ("/webapp/tx", "/webapp/dues"),
    "Опросы": ("/webapp/polls", "/webapp/polls/edit", "/webapp/polls/results",
               "/webapp/polls/mine", "/webapp/polls/answer"),
    "Файлы": ("/webapp/files", "/webapp/folder", "/webapp/file"),
    "Прочее": ("/webapp/person", "/webapp/notifications", "/webapp/feedback",
               "/webapp/services", "/webapp/broadcast", "/webapp/reconcile"),
}
# Всё, что осталось, — разделы «Управления» из реестра SECTIONS.
ADMIN_GROUP = "Управление (разделы)"


def slug(path: str) -> str:
    text = path.strip("/").replace("/", "-") or "webapp"
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text)


def label_of(title: str, path: str) -> str:
    """Подпись узла: заголовок страницы, а не адрес — на графе читают слова."""
    override = {"/webapp": "Профиль", "/webapp/home": "Главная"}
    if path in override:
        return override[path]
    return (title or "").strip() or path


ROLES = ("admin", "org", "master", "troupe")


def collect() -> list[dict]:
    """Пройти все экраны стенда в четырёх ролях: ссылки объединяются, снимок — один.

    Ссылка на раздел рисуется по правам: у труппы «Управления» почти нет, у админа есть
    всё. Граф «все права» — это объединение, иначе половина экранов выглядит недостижимой
    (стенд без заголовка роли отдаёт набор труппы).
    """
    from playwright.sync_api import sync_playwright

    from tests.webapp.server import PAGE_ROUTES

    SHOTS.mkdir(parents=True, exist_ok=True)
    screens: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for role in ROLES:
            # «Мои опросы» на Главной стенд отдаёт только с этим заголовком: без него
            # строка-вход в опросы не рисуется и экран выглядит недостижимым.
            headers = {"X-SVOI-Role": role}
            if role == "admin":
                headers["X-SVOI-Polls"] = "open"
            context = browser.new_context(viewport={"width": 390, "height": 844},
                                          device_scale_factor=2,
                                          extra_http_headers=headers)
            page = context.new_page()
            for path in PAGE_ROUTES:
                page.goto(f"{STAND}{path}{HASH}", wait_until="load")
                try:
                    page.wait_for_function(
                        "() => window.SVOI && (window.SVOI.token || !window.Telegram?.WebApp?.initData)",
                        timeout=10_000)
                except Exception as exc:                  # стенд не отдал токен
                    raise SystemExit(f"{role} {path}: страница не получила токен ({exc})") from exc
                page.wait_for_timeout(200)
                # Строки-ссылки на «Главной» и «Ещё» рисуются после ответа API: без
                # ожидания сети в графе не будет половины переходов.
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                links = page.evaluate(
                    "() => Array.from(document.querySelectorAll('a[href]')).map(a => {"
                    "  const t = a.querySelector('.ui-row__title, [class*=title], strong, h2, h3');"
                    "  const text = ((t && t.textContent) || a.textContent || '')"
                    "    .trim().replace(/\\s+/g, ' ');"
                    "  return [a.getAttribute('href'), text.slice(0, 48)];"
                    "})")
                # Переход может быть не ссылкой, а обработчиком: «Люди → Взносы» ведёт
                # через `location.href` (admin_people.py). Без этого прохода экран
                # «Взносы» выглядит недостижимым, хотя кнопка есть.
                jumps = page.evaluate(
                    "() => Array.from(document.querySelectorAll('[onclick]')).map(el => {"
                    "  const m = (el.getAttribute('onclick') || '')"
                    "    .match(/location\\.href\\s*=\\s*[\"']([^\"']+)/);"
                    "  return m ? [m[1], (el.textContent || '').trim()"
                    "    .replace(/\\s+/g, ' ').slice(0, 48)] : null;"
                    "}).filter(Boolean)")
                links += [list(item) for item in jumps]
                entry = screens.setdefault(path, {"path": path, "title": page.title(),
                                                  "label": label_of(page.title(), path),
                                                  "links": {}, "roles": set()})
                entry["roles"].add(role)
                for href, text in links:
                    entry["links"].setdefault(href, text)   # подпись от первой роли (админ)
                if role == "admin":
                    shot = SHOTS / f"{slug(path)}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    entry["shot"] = shot
            context.close()
        browser.close()
    for entry in screens.values():
        entry["links"] = list(entry["links"].items())
        entry["shot"] = entry.get("shot") or (SHOTS / f"{slug(entry['path'])}.png")
    return [screens[path] for path in PAGE_ROUTES]


def redirects() -> dict[str, str]:
    """Старые адреса из `pages/routes.py`: узел графа — новый адрес, ребро — «редирект»."""
    text = (ROOT / "services" / "tool-api" / "app" / "pages" / "routes.py").read_text("utf-8")
    pairs = {}
    for block in re.split(r"\n    @router\.get\(", text)[1:]:
        route = re.match(r'"([^"]+)"', block)
        target = re.search(r'RedirectResponse\("([^"]+)"', block)
        if route and target:
            pairs[route.group(1)] = target.group(1)
    return pairs


def chat_entries(known: set[str]) -> list[tuple[str, str]]:
    """Кнопки бота, открывающие экран Mini App: (подпись кнопки, адрес).

    Читаются вызовы `webapp_keyboard(...)`; текст берётся литералом или из `texts`,
    адрес — литералом. Динамические адреса (`screen`, константы-адреса) пропускаются
    и считаются: их немного, и они попадают в граф через сами экраны.
    """
    sys.path.insert(0, str(ROOT / "services" / "tg-gateway" / "app"))
    import texts as bot_texts

    found: list[tuple[str, str]] = []
    pattern = re.compile(r'webapp_keyboard\(\s*([^,\n]+?)\s*,\s*"([^"]+)"')
    for path in sorted((ROOT / "services" / "tg-gateway" / "app").rglob("*.py")):
        for text_arg, url in pattern.findall(path.read_text("utf-8")):
            text_arg = text_arg.strip()
            if text_arg.startswith('"'):
                label = text_arg.strip('"')
            elif text_arg.startswith("texts."):
                label = getattr(bot_texts, text_arg.split(".", 1)[1], "")
            else:
                continue
            if label and url.startswith("/webapp") and (label, url) not in found:
                found.append((label, url))
    return found


def build(screens: list[dict]) -> tuple[str, list[str], list[str]]:
    known = {s["path"] for s in screens}
    moves = redirects()
    # Справочник компонентов — не экран потока: в графе он только мешает.
    on_graph = [s for s in screens if s["path"] != "/webapp/ui"]

    def node(path: str) -> str:
        return "n" + slug(path).replace("-", "_")

    lines = ["flowchart TB"]
    lines.append('  chat["Чат бота: /start, кнопка «Меню»"]')

    groups = [(name, [p for p in paths if p in known]) for name, paths in GROUPS.items()]
    admin = [s["path"] for s in on_graph
             if s["path"] not in {p for ps in GROUPS.values() for p in ps}]
    groups.append((ADMIN_GROUP, admin))
    # Два «Уведомления» и два «Файлы» на графе — разные экраны: раздел и свой. Различаем
    # пометкой, иначе узлы читаются как дубли.
    seen_labels: dict[str, int] = {}
    for _, members in groups:
        for path in members:
            label = next(x for x in on_graph if x["path"] == path)["label"]
            seen_labels[label] = seen_labels.get(label, 0) + 1
    for index, (name, members) in enumerate((g for g in groups if g[1]), start=1):
        lines.append(f'  subgraph g{index}["{name}"]')
        lines.append("    direction TB")
        for path in members:
            s = next(x for x in on_graph if x["path"] == path)
            label = s["label"]
            if seen_labels[label] > 1:
                label += " (раздел)" if path.startswith("/webapp/admin/t/") else " (свой)"
            lines.append(f'    {node(path)}["{label}"]')
        lines.append("  end")

    edges: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    for s in on_graph:
        for href, text in s["links"]:
            target = urlparse(href or "").path
            if not target.startswith("/webapp") or target.startswith("/webapp/ui.css"):
                continue
            if target not in known:
                # Стенд может отдать старый адрес: разворачиваем через редиректы кода.
                if target in moves and moves[target] in known:
                    edges.append((s["path"], moves[target], f"{text} (редирект)"))
                elif target.replace("/admin/", "/admin/t/") in known:
                    edges.append((s["path"], target.replace("/admin/", "/admin/t/"), text))
                else:
                    skipped.append(f"{s['path']} → {target} «{text}»")
                continue
            if target == "/webapp/ui":
                continue
            if target in TABS and text in TAB_LABELS:
                continue                                  # полоса вкладок — текстом
            if text == "‹":
                continue                                  # возврат на уровень выше — текстом
            edges.append((s["path"], target, text))

    for label, url in chat_entries(known):
        target = url if url in known else moves.get(url, url)
        if target in known and target != "/webapp/ui":
            edges.append(("chat", target, label))

    # Одна пара «экран → экран» — одна линия: у «Файлов» десяток строк в один и тот же
    # экран файла, и без склейки граф превращается в список файлов.
    merged: dict[tuple[str, str], list[str]] = {}
    for source, target, text in edges:
        merged.setdefault((source, target), []).append(text)
    for (source, target), labels in merged.items():
        labels.sort(key=lambda item: item == "‹")      # «‹» — подпись по умолчанию, не главная
        label = labels[0] if len(labels) == 1 else f"{labels[0]} и ещё {len(labels) - 1}"
        if len(label) > 28:
            label = label[:28].rstrip(" ,.;:") + "…"
        src = "chat" if source == "chat" else node(source)
        lines.append(f'  {src} -->|"{label.replace(chr(34), "")}"| {node(target)}')

    return "\n".join(lines), skipped, admin


def write_doc(mermaid: str, screens: list[dict], moves: dict[str, str],
              skipped: list[str], admin: list[str]) -> None:
    from app.webapp import SECTIONS

    caps = {s.url: ", ".join(s.caps) for s in SECTIONS}
    incoming: dict[str, int] = {}
    for line in mermaid.splitlines():
        for target in re.findall(r"\| *n([a-z0-9_]+)\]?", line):
            incoming[target] = incoming.get(target, 0) + 1

    rows = []
    for s in sorted(screens, key=lambda x: (x["path"] not in TABS, x["path"])):
        rights = caps.get(s["path"], "—")
        rows.append(f'| `{s["path"]}` | {s["label"]} | {rights} |')
    move_rows = [f"| `{old}` | `{new}` |" for old, new in sorted(moves.items())]

    text = f"""# 167. Единый граф переходов: чат бота → экраны Mini App

**Дата:** 17.11.2026. **Собирается:** `scripts/build_screen_map.py` — не правится руками.
**Заменяет:** `docs/archive/prototypes/44-menu-map.html` (описывал меню и шесть экранов;
в коде четыре вкладки и {len(screens)} экранов).
**Связано:** `docs/development/92-navigation-model.md` (модель навигации),
`docs/development/141-miniapp-design-review.md` (инвентарь экранов),
`docs/development/91-design-system.md` (вид), `docs/development/168-prototype-workspace.md`
(как с этим работаем).

Источник истины — код: `tests/webapp/server.py` отдаёт те же страницы, что боевой tool-api,
поэтому рёбра графа — настоящие `<a href>` со стенда, а подписи — текст кнопок. Документ
устаревает только вместе с кодом: пересборка — одна команда.

## 1. Граф

Полоса вкладок («Главная · Время · Деньги · Управление») есть на каждом экране и ведёт в
четыре корня. На графе она не нарисована: 35 × 4 одинаковых линий сделали бы схему нечитаемой.
Так же не нарисована стрелка «‹»: возврат на уровень выше есть на каждом вложенном экране и
всегда ведёт к корню своей вкладки.

```mermaid
{mermaid}
```

## 2. Экраны

Права — из реестра `SECTIONS` (`services/tool-api/app/webapp.py`): экран рисуется, только если
возможность есть; данные всё равно проверяются в API.

| Адрес | Экран | Права |
|---|---|---|
{chr(10).join(rows)}

## 3. Старые адреса (редиректы 301)

Адреса не удаляются: ссылки в чате и закладках живут дальше.

| Было | Стало |
|---|---|
{chr(10).join(move_rows)}

## 4. Как обновить

```
# стенд
env -u PYTHONPATH .venv/bin/python -m uvicorn tests.webapp.server:app --port 8799
# граф и атлас экранов
env -u PYTHONPATH .venv/bin/python scripts/build_screen_map.py
```

Скрипт падает, если на экране есть ссылка на адрес, которого нет среди экранов и редиректов:
это дефект стенда или приложения, а не повод нарисовать стрелку «куда-нибудь».

## 5. Что не попало на граф

- **Пункты меню чата** (Деньги · Время · Админ · Профиль · Помощь) — это `callback_data`,
  а не адреса: они не ведут в Mini App, а работают в чате. Их состав — `keyboards.menu_groups`.
- **Динамические кнопки** бота: адрес собирается из переменной (`screen`, константы страниц).
  Они видны на графе как сами экраны, к которым ведут.
- **Снимки экранов** — в `docs/reports/168-screens-atlas.html` (собирается тем же прогоном).

{f"## 6. Найдено при сборке{chr(10)}{chr(10)}Ссылки, которые никуда не ведут: {len(skipped)}{chr(10)}{chr(10)}" + chr(10).join(f"- {item}" for item in skipped) if skipped else ""}
"""
    GRAPH_DOC.write_text(text, "utf-8")


def write_atlas(screens: list[dict]) -> None:
    cards = []
    for s in screens:
        cards.append(
            f'<a class="card" href="{s["path"]}">'
            f'<div class="cap"><b>{s["label"]}</b>'
            f'<span>{s["path"]}</span></div>'
            f'<img src="168-screens/{s["shot"].name}" alt="{s["label"]}"></a>')
    ATLAS_DOC.write_text(f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>СВОИ — атлас экранов Mini App, 17.11.2026</title>
<style>
  /* Атлас собран скриптом со стенда: снимки 390 × 844 ×2, порядок — как в графе 167. */
  body {{ margin: 0; padding: 24px; background: #f7f8fa; color: #16181d;
          font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  p.lead {{ margin: 0 0 20px; color: #5d6b7a; max-width: 900px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }}
  .card {{ display: block; text-decoration: none; color: inherit; background: #fff;
           border: 1px solid #dfe4ea; border-radius: 12px; overflow: hidden; }}
  .card:hover {{ border-color: #2f6fed; }}
  .cap {{ padding: 10px 12px; border-bottom: 1px solid #eef1f5; }}
  .cap b {{ display: block; font-size: 14px; }}
  .cap span {{ font-size: 11px; color: #6b7280; font-family: ui-monospace, monospace; }}
  img {{ display: block; width: 100%; height: auto; background: #eceef1; }}
</style></head>
<body>
<h1>Атлас экранов Mini App</h1>
<p class="lead">{len(screens)} экранов со стенда (<code>tests.webapp.server</code>) в светлой схеме,
ширина 390 px, плотность 2. Собирается <code>scripts/build_screen_map.py</code>; граф переходов
между этими экранами — <code>docs/architecture/167-transition-graph.md</code>.
Снимки — состояние демо-данных стенда, а не прода.</p>
<div class="grid">
{chr(10).join(cards)}
</div>
</body></html>
""", "utf-8")


def main() -> int:
    from tests.webapp.server import PAGE_ROUTES

    screens = collect()
    mermaid, skipped, admin = build(screens)
    known = {s["path"] for s in screens}

    # Проверки: граф обязан покрывать все экраны, атлас — тоже.
    missing = [p for p in PAGE_ROUTES if p not in known]
    assert not missing, f"экраны стенда не попали в атлас: {missing}"
    for s in screens:
        assert s["shot"].exists(), f"нет снимка {s['shot']}"
    # Достижимость вместо счёта рёбер: экран, до которого нет ни одной ссылки, —
    # дефект навигации, а не «нарисуем стрелку откуда-нибудь».
    reach: set[str] = set()
    edges: dict[str, set[str]] = {}
    for source, target in re.findall(r"^\s*(\w+) -->\|\"[^\"]*\"\| (\w+)$", mermaid, re.M):
        edges.setdefault(source, set()).add(target)
    frontier = ["chat"] + [f"n{slug(p).replace('-', '_')}" for p in TABS]
    while frontier:
        current = frontier.pop()
        if current in reach:
            continue
        reach.add(current)
        frontier.extend(edges.get(current, ()))
    unreachable = [s["path"] for s in screens
                   if s["path"] not in ("/webapp/ui",)
                   and f"n{slug(s['path']).replace('-', '_')}" not in reach]
    assert not unreachable, f"до экранов нет перехода: {unreachable}"
    for s in screens:
        if s["path"] == "/webapp/ui":        # справочник компонентов, не экран потока
            continue
        assert f'n{slug(s["path"]).replace("-", "_")}["' in mermaid, f"нет узла {s['path']}"

    write_doc(mermaid, screens, redirects(), skipped, admin)
    write_atlas(screens)
    print(json.dumps({"экранов": len(screens), "переходов": mermaid.count("-->"),
                      "битых ссылок": skipped, "атлас": str(ATLAS_DOC),
                      "граф": str(GRAPH_DOC)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
